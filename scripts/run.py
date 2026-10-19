#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Run one recipe on one benchmark (benches/<bench>/bench.toml).

    uv run scripts/run.py terminal-bench-4 recipes/<id>.toml                  # full run (bench n_attempts)
    uv run scripts/run.py terminal-bench-4 recipes/<id>.toml --attempts 1     # start with k=1
    uv run scripts/run.py terminal-bench-4 recipes/<id>.toml --extend runs/terminal-bench-4/<run> --attempts 2
    uv run scripts/run.py terminal-bench-4 recipes/<id>.toml --resume runs/terminal-bench-4/<run>

A run is one or more Harbor jobs ("parts") with identical settings; --extend adds a part with more
attempts (e.g. a k=1 run topped up to the bench's n_attempts, by anyone, on any host), --resume
continues the last part.

    runs/<bench>/<run>/          in git: run.json (bench, recipe, host, versions, parts),
                                 recipe.toml, bench.toml, harbor-config-p<N>.json, trials.jsonl
    evidence/<bench>/<job>/      git-ignored, one per part: Harbor job output (trajectories, recordings,
                                 verifier logs) and harbor.log; packed to <job>.tar.gz, sha256 in run.json

<run> = <date>_<recipe id>; part N > 1 is job <run>_p<N>. When Harbor exits,
scripts/collect.py --pack extracts trials.jsonl and archives the evidence. The API key is read from the
recipe's api_key_env and passed only through the process environment.
"""
import argparse, datetime, json, os, platform, shutil, subprocess, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def harbor_config(bench, model, attempts, n_concurrent, job_name, jobs_dir):
    agent, suite, job = bench["agent"], bench["suite"], bench["job"]
    price = model.get("pricing", {})
    model_info = {
        "max_input_tokens": agent["max_input_tokens"],
        "max_output_tokens": agent["max_output_tokens"],
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": price.get("input", 0) / 1e6,
        "output_cost_per_token": price.get("output", 0) / 1e6,
        "cache_read_input_token_cost": price.get("cached_input", price.get("input", 0)) / 1e6,
    }
    return {
        "job_name": job_name,
        "jobs_dir": str(jobs_dir),
        "n_attempts": attempts,
        "n_concurrent_trials": n_concurrent,
        "timeout_multiplier": job["timeout_multiplier"],
        "retry": {"max_retries": job["max_retries"]},
        "environment": {"type": job["environment"]},
        "datasets": [{
            "name": suite["dataset"],
            "version": suite["version"],
            "task_names": [f"{suite['dataset'].split('/')[0]}/{t}" for t in suite["tasks"]],
        }],
        "agents": [{
            "name": agent["name"],
            "model_name": model["model"],
            "kwargs": {
                "api_base": model["api_base"],
                "reasoning_effort": agent["reasoning_effort"],
                "temperature": agent["temperature"],
                "model_info": model_info,
                "llm_kwargs": {"max_tokens": agent["max_output_tokens"]},
            },
        }],
    }


def harbor_version():
    out = subprocess.run(["harbor", "--version"], capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()


def host_info():
    info = {"os": platform.platform(), "machine": platform.machine(), "node": platform.node()}
    try:
        info["docker"] = json.loads(subprocess.run(
            ["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True).stdout)
        info["docker"] = {k: info["docker"].get(k) for k in ("ServerVersion", "NCPU", "MemTotal", "Architecture")}
    except Exception as e:  # noqa: BLE001 — host info is best effort
        info["docker"] = str(e)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bench", help="directory name under benches/, e.g. terminal-bench-4")
    ap.add_argument("model", type=Path, help="recipes/<id>.toml")
    ap.add_argument("--attempts", type=int, help="fewer attempts than the bench's n_attempts (e.g. 1 to start)")
    ap.add_argument("--n-concurrent", type=int, help="override the recipe's n_concurrent (host limits)")
    ap.add_argument("--resume", type=Path, help="run dir whose last part to resume")
    ap.add_argument("--extend", type=Path, help="finished run dir to add a part with --attempts more attempts")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bench_file = ROOT / "benches" / args.bench / "bench.toml"
    if not bench_file.exists():
        sys.exit(f"no {bench_file.relative_to(ROOT)}")
    bench = tomllib.loads(bench_file.read_text())
    if bench["id"] != args.bench or bench["runner"] != "harbor":
        sys.exit(f"{bench_file} is bench {bench['id']!r}, runner {bench['runner']!r}")
    model = tomllib.loads(args.model.read_text())
    if args.extend and not args.attempts:
        sys.exit("--extend needs --attempts")
    attempts = args.attempts or bench["suite"]["n_attempts"]
    n_concurrent = args.n_concurrent or model["n_concurrent"]

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if args.resume or args.extend:
        run_dir = (args.resume or args.extend).resolve()
        meta = json.loads((run_dir / "run.json").read_text())
        if meta["bench"] != args.bench or meta["recipe"]["id"] != model["id"]:
            sys.exit(f"{run_dir} is {meta['recipe']['id']} on {meta['bench']}")
        if (run_dir / "bench.toml").read_text() != bench_file.read_text():
            sys.exit(f"{run_dir} ran a different bench.toml; start a new run")
    else:
        run_dir = ROOT / "runs" / args.bench / f"{datetime.date.today().isoformat()}_{model['id']}"
        if run_dir.exists():
            sys.exit(f"{run_dir} exists; pass --resume or --extend {run_dir}")
        meta = {
            "bench": args.bench,
            "recipe_file": str(args.model.resolve().relative_to(ROOT)),
            "recipe": {k: v for k, v in model.items() if k != "api_key_env"},
            "attempts": 0,
            "smoke": True,
            "finished": False,
            "harbor": harbor_version(),
            "host": host_info(),
            "started_at": now,
            "parts": [],
        }
    if args.extend:
        if harbor_version() != meta["harbor"]:
            sys.exit(f"Harbor is {harbor_version()!r}, the run used {meta['harbor']!r}")
        if not meta.get("finished"):
            sys.exit("the last part is not finished; --resume it first")
        if meta["attempts"] + attempts > bench["suite"]["n_attempts"]:
            sys.exit(f"{meta['attempts']} + {attempts} attempts exceeds the bench's {bench['suite']['n_attempts']}")
    if args.resume:
        part = meta["parts"][-1]
    else:
        n = len(meta["parts"]) + 1
        job_name = run_dir.name if n == 1 else f"{run_dir.name}_p{n}"
        part = {"job_name": job_name, "attempts": attempts, "n_concurrent": n_concurrent,
                "config": f"harbor-config-p{n}.json", "started_at": now, "host": host_info()}
    job_name = part["job_name"]
    ev_dir = Path("evidence") / args.bench / job_name  # relative to ROOT, the cwd Harbor runs in
    cfg = harbor_config(bench, model, part["attempts"], part["n_concurrent"], job_name, ev_dir / "jobs")
    if args.dry_run:
        print(json.dumps(cfg, indent=2))
        return

    run_dir.mkdir(parents=True, exist_ok=True)
    (ROOT / ev_dir).mkdir(parents=True, exist_ok=True)
    (run_dir / part["config"]).write_text(json.dumps(cfg, indent=2) + "\n")
    if not args.resume:
        meta["parts"].append(part)
        meta["attempts"] = sum(p["attempts"] for p in meta["parts"])
        meta["smoke"] = meta["attempts"] != bench["suite"]["n_attempts"]
        meta["finished"] = False
        (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
        if len(meta["parts"]) == 1:
            shutil.copy(args.model, run_dir / "recipe.toml")
            shutil.copy(bench_file, run_dir / "bench.toml")

    env = dict(os.environ)
    key_env = model.get("api_key_env") or ""
    if key_env and not env.get(key_env):
        sys.exit(f"{key_env} is not set")
    env["OPENAI_API_KEY"] = env[key_env] if key_env else "local"

    cmd = ["harbor", "job", "resume", "-p", str(ev_dir / "jobs" / job_name)] if args.resume else \
          ["harbor", "run", "-c", str(run_dir / part["config"]), "-y", "-q"]
    print(" ".join(cmd), flush=True)
    with open(ROOT / ev_dir / "harbor.log", "a") as log:
        rc = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT).returncode
    print(f"harbor exited {rc}", flush=True)
    collect = [sys.executable, str(ROOT / "scripts/collect.py"), str(run_dir)] + (["--pack"] if rc == 0 else [])
    subprocess.run(collect, cwd=ROOT)
    print("next: uv run scripts/summarize.py", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()

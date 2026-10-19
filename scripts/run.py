#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Run one recipe on one benchmark (benches/<bench>/bench.toml).

    uv run scripts/run.py terminal-bench-4 recipes/<id>.toml --attempts 1     # one attempt per task
    uv run scripts/run.py terminal-bench-4 recipes/<id>.toml                  # the bench's n_attempts
    uv run scripts/run.py terminal-bench-4 recipes/<id>.toml --resume runs/terminal-bench-4/<run>

Every run is a standalone contribution; scripts/summarize.py pools all runs of a recipe on a bench, so
more attempts later are simply another run (by anyone, on any host). --resume continues an interrupted
run.

    runs/<bench>/<run>/          in git: run.json (bench, recipe, host, versions, Harbor job),
                                 recipe.toml, bench.toml, harbor-config-p1.json, trials.jsonl
    evidence/<bench>/<run>/      git-ignored: Harbor job output (trajectories, recordings, verifier logs)
                                 and harbor.log; packed to <run>.tar.gz, sha256 in run.json

<run> = <date>_<recipe id>[_<n>]. run.json keeps a "parts" list holding the run's one Harbor job. When Harbor exits,
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
    ap.add_argument("--attempts", type=int, help="attempts per task (default: the bench's n_attempts)")
    ap.add_argument("--n-concurrent", type=int, help="override the recipe's n_concurrent (host limits)")
    ap.add_argument("--resume", type=Path, help="run dir to resume")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bench_file = ROOT / "benches" / args.bench / "bench.toml"
    if not bench_file.exists():
        sys.exit(f"no {bench_file.relative_to(ROOT)}")
    bench = tomllib.loads(bench_file.read_text())
    if bench["id"] != args.bench or bench["runner"] != "harbor":
        sys.exit(f"{bench_file} is bench {bench['id']!r}, runner {bench['runner']!r}")
    model = tomllib.loads(args.model.read_text())
    attempts = args.attempts or bench["suite"]["n_attempts"]
    n_concurrent = args.n_concurrent or model["n_concurrent"]

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if args.resume:
        run_dir = args.resume.resolve()
        meta = json.loads((run_dir / "run.json").read_text())
        if meta["bench"] != args.bench or meta["recipe"]["id"] != model["id"]:
            sys.exit(f"{run_dir} is {meta['recipe']['id']} on {meta['bench']}")
        if (run_dir / "bench.toml").read_text() != bench_file.read_text():
            sys.exit(f"{run_dir} ran a different bench.toml; start a new run")
    else:
        base = f"{datetime.date.today().isoformat()}_{model['id']}"
        run_dir, n = ROOT / "runs" / args.bench / base, 1
        while run_dir.exists():
            n += 1
            run_dir = ROOT / "runs" / args.bench / f"{base}_{n}"
        meta = {
            "bench": args.bench,
            "recipe_file": str(args.model.resolve().relative_to(ROOT)),
            "recipe": {k: v for k, v in model.items() if k != "api_key_env"},
            "attempts": attempts,
            "finished": False,
            "harbor": harbor_version(),
            "host": host_info(),
            "started_at": now,
            "parts": [],
        }
    if args.resume:
        part = meta["parts"][-1]
    else:
        part = {"job_name": run_dir.name, "attempts": attempts, "n_concurrent": n_concurrent,
                "config": "harbor-config-p1.json", "started_at": now, "host": meta["host"]}
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
        (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
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

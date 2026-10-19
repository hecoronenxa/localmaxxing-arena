#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Run one recipe on one benchmark under that benchmark's protocol.

    uv run scripts/run.py terminal-bench-4 recipes/<id>.toml                 # full run (protocol n_attempts)
    uv run scripts/run.py terminal-bench-4 recipes/<id>.toml --attempts 1    # smoke run
    uv run scripts/run.py terminal-bench-4 recipes/<id>.toml --resume runs/terminal-bench-4/<dir>

The protocol is the newest benches/<bench>/protocol-v*.toml unless --protocol is given. Two places:

    runs/<bench>/<run>/       in git: run.json (what ran: protocol, recipe, host, versions),
                              recipe.toml, protocol.toml, harbor-config.json, trials.jsonl (raw metrics)
    evidence/<bench>/<run>/   git-ignored: Harbor job output (trajectories, recordings, verifier logs)
                              and harbor.log; packed to <run>.tar.gz with its sha256 in run.json

<run> = <date>_<protocol>_<recipe id>[_k<attempts>]. When Harbor exits, scripts/collect.py --pack
extracts trials.jsonl and archives the evidence. The API key is read from the recipe's api_key_env and
passed only through the process environment.
"""
import argparse, datetime, json, os, platform, shutil, subprocess, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def harbor_config(protocol, model, attempts, n_concurrent, job_name, jobs_dir):
    agent, suite, job = protocol["agent"], protocol["suite"], protocol["job"]
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
    ap.add_argument("--protocol", type=Path, help="default: newest benches/<bench>/protocol-v*.toml")
    ap.add_argument("--attempts", type=int, help="override protocol n_attempts (smoke runs)")
    ap.add_argument("--n-concurrent", type=int, help="override the recipe's n_concurrent (host limits)")
    ap.add_argument("--resume", type=Path, help="existing run dir to resume")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.protocol is None:
        found = sorted((ROOT / "benches" / args.bench).glob("protocol-v*.toml"),
                       key=lambda f: int(f.stem.removeprefix("protocol-v")))
        if not found:
            sys.exit(f"no protocol in benches/{args.bench}/")
        args.protocol = found[-1]
    protocol = tomllib.loads(args.protocol.read_text())
    if protocol["bench"] != args.bench or protocol["runner"] != "harbor":
        sys.exit(f"{args.protocol} is bench {protocol['bench']!r}, runner {protocol['runner']!r}")
    model = tomllib.loads(args.model.read_text())
    attempts = args.attempts or protocol["suite"]["n_attempts"]
    n_concurrent = args.n_concurrent or model["n_concurrent"]

    if args.resume:
        run_dir = args.resume.resolve()
        bench_ = json.loads((run_dir / "run.json").read_text())["bench"]
        if bench_ != args.bench:
            sys.exit(f"{run_dir} is a {bench_} run")
        meta = json.loads((run_dir / "run.json").read_text())
        job_name = meta["job_name"]
    else:
        date = datetime.date.today().isoformat()
        suffix = "" if attempts == protocol["suite"]["n_attempts"] else f"_k{attempts}"
        job_name = f"{date}_{protocol['name']}_{model['id']}{suffix}"
        run_dir = ROOT / "runs" / args.bench / job_name
        if run_dir.exists():
            sys.exit(f"{run_dir} exists; pass --resume {run_dir} to continue it")

    ev_dir = Path("evidence") / args.bench / job_name  # relative to ROOT, the cwd Harbor runs in
    cfg = harbor_config(protocol, model, attempts, n_concurrent, job_name, ev_dir / "jobs")
    if args.dry_run:
        print(json.dumps(cfg, indent=2))
        return

    run_dir.mkdir(parents=True, exist_ok=True)
    (ROOT / ev_dir).mkdir(parents=True, exist_ok=True)
    (run_dir / "harbor-config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    if not args.resume:
        meta = {
            "job_name": job_name,
            "bench": args.bench,
            "protocol": protocol["name"],
            "protocol_file": str(args.protocol.resolve().relative_to(ROOT)),
            "recipe_file": str(args.model.resolve().relative_to(ROOT)),
            "recipe": {k: v for k, v in model.items() if k != "api_key_env"},
            "attempts": attempts,
            "smoke": attempts != protocol["suite"]["n_attempts"],
            "n_concurrent": n_concurrent,
            "harbor": harbor_version(),
            "host": host_info(),
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
        shutil.copy(args.model, run_dir / "recipe.toml")
        shutil.copy(args.protocol, run_dir / "protocol.toml")

    env = dict(os.environ)
    key_env = model.get("api_key_env") or ""
    if key_env and not env.get(key_env):
        sys.exit(f"{key_env} is not set")
    env["OPENAI_API_KEY"] = env[key_env] if key_env else "local"

    cmd = ["harbor", "job", "resume", "-p", str(ev_dir / "jobs" / job_name)] if args.resume else \
          ["harbor", "run", "-c", str(run_dir / "harbor-config.json"), "-y", "-q"]
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

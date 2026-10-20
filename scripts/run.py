#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Run one recipe on one benchmark (benches/<bench>/bench.toml), through the bench's runner: Harbor
(container tasks) or inspect (inspect_evals tasks, run with uvx in a throwaway environment).

    uv run scripts/run.py <bench> recipes/<id>.toml --attempts 1     # one attempt per task
    uv run scripts/run.py <bench> recipes/<id>.toml                  # the bench's n_attempts
    uv run scripts/run.py <bench> recipes/<id>.toml --resume runs/<bench>/<run>

Every run is a standalone contribution; scripts/summarize.py pools all runs of a recipe on a bench, so
more attempts later are simply another run (by anyone, on any host). --resume continues an interrupted
run (inspect: re-runs only the tasks without a clean sample, into another log in the same directory).

    runs/<bench>/<run>/          in git: run.json (bench, recipe, host, versions, job),
                                 recipe.toml, bench.toml, <runner>-config-p1.json, trials.jsonl
    evidence/<bench>/<run>/      git-ignored: Harbor job output (trajectories, recordings, verifier logs)
                                 or inspect logs (JSON), and the runner's log; packed to <run>.tar.gz,
                                 sha256 in run.json

<run> = <date>_<recipe id>[_<n>]. run.json keeps a "parts" list holding the run's one job. When the runner exits,
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
                "llm_kwargs": {"max_tokens": agent["max_output_tokens"], "timeout": agent["request_timeout_sec"]},
            },
        }],
    }


# Wire protocol a recipe's endpoint speaks (recipe field `protocol`, default "chat"). The benchmark itself is the
# same for all three; only the inspect provider differs. bench.toml is untouched, and "chat" generates exactly
# the command earlier runs used, so existing results stay valid.
PROTOCOLS = ("chat", "responses", "anthropic")
ANTHROPIC_REQUIREMENT = "anthropic==1.15.0"   # only added for protocol = "anthropic"; not in bench.toml's pins


def protocol_of(model):
    proto = model.get("protocol", "chat")
    if proto not in PROTOCOLS:
        sys.exit(f"recipe {model.get('id')!r}: protocol {proto!r} is not one of {PROTOCOLS}")
    return proto


def served_name(model):
    """Recipe model "openai/<served name>" (LiteLLM style) -> the name the endpoint serves."""
    return model["model"].split("/", 1)[1] if model["model"].startswith("openai/") else model["model"]


def inspect_config(bench, model, attempts, n_concurrent, job_name, log_dir):
    """The inspect command (minus the API key), as data so check.py can regenerate and compare it.
    By recipe protocol: "chat" = inspect's generic OpenAI-compatible provider named "arena" (ARENA_BASE_URL,
    ARENA_API_KEY from the environment); "responses" = inspect's openai provider on the Responses API
    (OPENAI_BASE_URL, OPENAI_API_KEY; responses_store=true because servers may reject the encrypted-reasoning
    `include` that inspect sends otherwise); "anthropic" = inspect's anthropic provider (ANTHROPIC_BASE_URL,
    ANTHROPIC_API_KEY; `anthropic` added to the throwaway environment; inspect sends no temperature)."""
    harness, agent, suite = bench["harness"], bench["agent"], bench["suite"]
    proto = protocol_of(model)
    cmd = ["uvx", "--python", harness["python"], "--from", harness["requirements"][0]]
    for req in harness["requirements"][1:] + ([ANTHROPIC_REQUIREMENT] if proto == "anthropic" else []):
        cmd += ["--with", req]
    cmd += ["inspect", "eval", harness["task"]]
    for key, value in harness.get("task_args", {}).items():
        cmd += ["-T", f"{key}={json.dumps(value)}"]
    cmd += [
        "--sample-id", ",".join(suite["tasks"]),
        "--epochs", str(attempts),
        "--model", {"chat": "openai-api/arena/", "responses": "openai/", "anthropic": "anthropic/"}[proto] + served_name(model),
        "--reasoning-effort", agent["reasoning_effort"],
        "--temperature", str(agent["temperature"]),
        "--max-tokens", str(agent["max_output_tokens"]),
        "--timeout", str(agent["request_timeout_sec"]),
        "--max-connections", str(n_concurrent),
        "--max-samples", str(n_concurrent),
        "--no-fail-on-error",
        "--log-format", "json",
        "--log-dir", str(log_dir),
        "--display", "plain",
    ]
    if proto == "responses":
        cmd += ["-M", "responses_api=true", "-M", "responses_store=true"]
    if model.get("strict_tools", True) is False:
        # inspect's openai-api provider marks every tool "strict": true. Local engines ignore that; OpenAI itself
        # then rejects schemas with optional parameters (BFCL has many). Only the chat protocol sends it.
        if proto != "chat":
            sys.exit(f"recipe {model.get('id')!r}: strict_tools only applies to protocol = \"chat\"")
        cmd += ["-M", "strict_tools=false"]
    return {"job_name": job_name, "log_dir": str(log_dir), "api_base": model["api_base"], "command": cmd}


def inspect_missing(log_dir, tasks, attempts):
    """The bench tasks that lack a sample without error for some epoch, across every log in log_dir."""
    done = set()
    for f in sorted(Path(log_dir).glob("*.json")):
        for smp in json.loads(f.read_text()).get("samples") or []:
            if not smp.get("error") and smp.get("scores"):
                done.add((smp["id"], smp.get("epoch", 1)))
    return [t for t in tasks if any((t, e) not in done for e in range(1, attempts + 1))]


def harness_version(bench, model=None):
    if bench["runner"] == "harbor":
        return harbor_version()
    reqs = list(bench["harness"]["requirements"])
    if model and protocol_of(model) == "anthropic":
        reqs.append(ANTHROPIC_REQUIREMENT)
    return " + ".join(reqs)


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
    ap.add_argument("bench", help="directory name under benches/")
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
    if bench["id"] != args.bench or bench["runner"] not in ("harbor", "inspect"):
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
            "recipe_file": args.model.resolve().relative_to(ROOT).as_posix(),
            "recipe": {k: v for k, v in model.items() if k != "api_key_env"},
            "attempts": attempts,
            "finished": False,
            "runner": bench["runner"],
            "harness": harness_version(bench, model),
            "host": host_info(),
            "started_at": now,
            "parts": [],
        }
    if args.resume:
        part = meta["parts"][-1]
        if part.pop("evidence", None) and not args.dry_run:  # the evidence changes: collect.py --pack re-archives it
            for old in (ROOT / "evidence" / args.bench).glob(f"{part['job_name']}.*gz"):
                old.unlink()
            (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    else:
        part = {"job_name": run_dir.name, "attempts": attempts, "n_concurrent": n_concurrent,
                "config": f"{bench['runner']}-config-p1.json", "started_at": now, "host": meta["host"]}
    job_name = part["job_name"]
    # Absolute: Harbor resolves a relative jobs_dir against each task's compose directory when it copies
    # artifacts out of the container, which fails and can leave the verifier without the agent's files.
    ev_dir = ROOT / "evidence" / args.bench / job_name
    if bench["runner"] == "harbor":
        cfg = harbor_config(bench, model, part["attempts"], part["n_concurrent"], job_name, ev_dir / "jobs")
    else:
        cfg = inspect_config(bench, model, part["attempts"], part["n_concurrent"], job_name, ev_dir / "logs")
    if args.dry_run:
        print(json.dumps(cfg, indent=2))
        return

    run_dir.mkdir(parents=True, exist_ok=True)
    ev_dir.mkdir(parents=True, exist_ok=True)
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

    if bench["runner"] == "harbor":
        cmd = ["harbor", "job", "resume", "-p", str(ev_dir / "jobs" / job_name)] if args.resume else \
              ["harbor", "run", "-c", str(run_dir / part["config"]), "-y", "-q"]
    else:
        proto = protocol_of(model)
        if proto == "chat":
            env["ARENA_BASE_URL"], env["ARENA_API_KEY"] = model["api_base"], env["OPENAI_API_KEY"]
        elif proto == "responses":
            env["OPENAI_BASE_URL"] = model["api_base"]
        else:  # the Anthropic SDK appends /v1/messages itself, so the same api_base as the OpenAI routes works
            env["ANTHROPIC_BASE_URL"] = model["api_base"].removesuffix("/").removesuffix("/v1")
            env["ANTHROPIC_API_KEY"] = env["OPENAI_API_KEY"]
        cmd = cfg["command"]
        if args.resume:  # re-run only the tasks with no clean sample yet, into a new log next to the old ones
            missing = inspect_missing(ev_dir / "logs", bench["suite"]["tasks"], part["attempts"])
            if not missing:
                sys.exit(f"every task already has a clean sample in {ev_dir / 'logs'}")
            print(f"resuming {len(missing)} of {len(bench['suite']['tasks'])} tasks", flush=True)
            cmd = list(cmd)
            cmd[cmd.index("--sample-id") + 1] = ",".join(missing)
    print(" ".join(cmd), flush=True)
    with open(ev_dir / f"{bench['runner']}.log", "a") as log:
        rc = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT).returncode
    print(f"{bench['runner']} exited {rc}", flush=True)
    collect = [sys.executable, str(ROOT / "scripts/collect.py"), str(run_dir)] + (["--pack"] if rc == 0 else [])
    subprocess.run(collect, cwd=ROOT)
    print("next: uv run scripts/summarize.py", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()

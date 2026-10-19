#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Extract the raw metrics of a run from its Harbor output (evidence) into the repo.

    uv run scripts/collect.py runs/<bench>/<run dir>           # refresh trials.jsonl (works mid-run)
    uv run scripts/collect.py runs/<bench>/<run dir> --pack    # also archive the evidence, record its sha256

Reads evidence/<bench>/<run>/ (git-ignored: trajectories, terminal recordings, verifier logs) and writes
runs/<bench>/<run>/trials.jsonl, one line of core stats per finished trial. With --pack, writes
evidence/<bench>/<run>.tar.gz and records its name, size and sha256 under "evidence" in run.json; upload
the archive anywhere and put the link in evidence.url.
"""
import argparse, hashlib, json, statistics, subprocess, sys, tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def seconds(span):
    if not span or not span.get("started_at") or not span.get("finished_at"):
        return None
    t = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    return round((t(span["finished_at"]) - t(span["started_at"])).total_seconds(), 1)


PARSE_ERROR = "Previous response had parsing errors"
TRUNCATED = "ERROR!! NONE of the actions you just requested were performed"
OVER_CAP = "hit max_tokens limit"


def call_stats(agent_dir, reply_cap):
    """Per-LLM-call stats from the main trajectory and its continuations (not the summarizer subagents)."""
    files = [agent_dir / "trajectory.json"] + sorted(agent_dir.glob("trajectory.cont-*.json"))
    steps = [s for f in files if f.exists() for s in json.loads(f.read_text()).get("steps", [])]
    calls = [s for s in steps if s.get("source") == "agent"]
    if not calls:
        return {}
    m = lambda s, k: (s.get("metrics") or {}).get(k) or 0  # noqa: E731
    tool_calls = [c for s in calls for c in (s.get("tool_calls") or [])]
    reasoning_chars = sum(len(s.get("reasoning_content") or "") for s in calls)
    visible_chars = sum(len(s.get("message") or "") + len(json.dumps(s.get("tool_calls") or [])) for s in calls)
    feedback = " ".join(str(s.get("observation") or "") for s in steps) + \
        " ".join(str(s.get("message") or "") for s in steps if s.get("source") == "user")
    return {
        "llm_calls": len(calls),
        "tool_calls": sum(1 for c in tool_calls if c.get("function_name") != "mark_task_complete"),
        "task_complete_calls": sum(1 for c in tool_calls if c.get("function_name") == "mark_task_complete"),
        # Harbor does not keep the provider's reasoning-token count; split each call's output tokens by
        # the character share of reasoning_content vs visible output (message + tool calls).
        "reasoning_share": round(reasoning_chars / (reasoning_chars + visible_chars), 3)
                           if reasoning_chars + visible_chars else None,
        "empty_reasoning_calls": sum(1 for s in calls if not s.get("reasoning_content")),
        "max_prompt_tokens": max(m(s, "prompt_tokens") for s in calls),
        "max_output_tokens": max(m(s, "completion_tokens") for s in calls),
        "calls_at_reply_cap": sum(1 for s in calls if m(s, "completion_tokens") >= reply_cap - 1),
        "parse_errors": feedback.count(PARSE_ERROR),
        "truncated_replies": feedback.count(TRUNCATED),
    }


def trial_row(trial_dir, reply_cap):
    r = json.loads((trial_dir / "result.json").read_text())
    if not r.get("finished_at"):
        return None
    ar = r.get("agent_result") or {}
    md = ar.get("metadata") or {}
    api_ms = md.get("api_request_times_msec") or []
    row = {
        "trial": r["trial_name"],
        "task": r["task_name"].split("/")[-1],
        "task_ref": (r.get("task_id") or {}).get("ref"),
        "reward": float(((r.get("verifier_result") or {}).get("rewards") or {}).get("reward") or 0.0),
        "exception": (r.get("exception_info") or {}).get("exception_type"),
        "input_tokens": ar.get("n_input_tokens"),
        "cached_tokens": ar.get("n_cache_tokens"),
        "output_tokens": ar.get("n_output_tokens"),
        "cost_usd": ar.get("cost_usd"),
        "episodes": md.get("n_episodes"),
        "summarizations": md.get("summarization_count"),
        "llm_s": round(sum(api_ms) / 1000, 1) if api_ms else None,
        "llm_call_p50_s": round(statistics.median(api_ms) / 1000, 1) if api_ms else None,
        "llm_call_max_s": round(max(api_ms) / 1000, 1) if api_ms else None,
        "env_setup_s": seconds(r.get("environment_setup")),
        "agent_s": seconds(r.get("agent_execution")),
        "verifier_s": seconds(r.get("verifier")),
        "total_s": seconds(r),
        "started_at": r.get("started_at"),
    }
    row.update(call_stats(trial_dir / "agent", reply_cap))
    # Replies cut at max_tokens that Terminus-2 could not salvage never become trajectory steps; the
    # trial log is the only record of them.
    log = trial_dir / "trial.log"
    row["replies_over_cap"] = log.read_text(errors="replace").count(OVER_CAP) if log.exists() else None
    if row.get("reasoning_share") is not None and row["output_tokens"]:
        row["reasoning_tokens_est"] = round(row["output_tokens"] * row["reasoning_share"])
    if row["llm_s"] and row["output_tokens"]:
        row["output_tok_per_llm_s"] = round(row["output_tokens"] / row["llm_s"], 1)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--pack", action="store_true")
    args = ap.parse_args()
    run_dir = args.run_dir.resolve()
    meta = json.loads((run_dir / "run.json").read_text())
    ev = ROOT / "evidence" / meta["bench"] / meta["job_name"]
    reply_cap = tomllib.loads((run_dir / "bench.toml").read_text())["agent"]["max_output_tokens"]
    rows = [row for f in sorted(ev.glob("jobs/*/*/result.json")) if (row := trial_row(f.parent, reply_cap))]
    (run_dir / "trials.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    job = next(ev.glob("jobs/*/result.json"), None)
    finished = bool(job and json.loads(job.read_text()).get("finished_at"))
    meta["finished"] = finished
    print(f"{len(rows)} finished trials -> {run_dir}/trials.jsonl (job finished: {finished})")

    if args.pack:
        if not finished:
            sys.exit("job not finished; not packing")
        archive = ev.parent / f"{ev.name}.tar.gz"
        subprocess.run(["tar", "-czf", str(archive), "-C", str(ev.parent), ev.name], check=True)
        h = hashlib.sha256()
        with open(archive, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        meta["evidence"] = {"archive": archive.name, "bytes": archive.stat().st_size,
                            "sha256": h.hexdigest(), "url": (meta.get("evidence") or {}).get("url")}
        print(f"evidence {archive} {archive.stat().st_size / 1e6:.1f} MB sha256 {h.hexdigest()}")
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Extract the raw metrics of a run from its runner output (evidence: Harbor jobs or inspect logs) into the repo.

    uv run scripts/collect.py runs/<bench>/<run dir>           # refresh trials.jsonl (works mid-run)
    uv run scripts/collect.py runs/<bench>/<run dir> --pack    # also archive finished parts' evidence

Reads evidence/<bench>/<job>/ for every part of the run (git-ignored: trajectories, terminal recordings,
verifier logs, inspect logs) and writes runs/<bench>/<run>/trials.jsonl, one line of core stats per finished
trial. For inspect benches a trial is one sample epoch, and its task_ref is the content digest defined by the
bench's select.py.
With --pack, writes evidence/<bench>/<run>.tar.gz once the run is finished and records its name, size and
sha256 in run.json; an archive over 24 MB is also split into <run>.part01.gz, .part02.gz, ... for upload
(GitHub's attachment limit is 25 MB; `cat <run>.part*.gz > <run>.tar.gz` restores it). Attach the archive or its pieces to the PR.
"""
import argparse, hashlib, importlib.util, json, statistics, subprocess, sys, tomllib
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


def bench_digest(bench):
    """The bench's task-content digest function (benches/<bench>/select.py), if it defines one."""
    f = ROOT / "benches" / bench / "select.py"
    if not f.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"select_{bench}", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "digest", None)


def reward_of(sample):
    """First scorer's value: C/I (inspect convention) or a number."""
    scores = list((sample.get("scores") or {}).values())
    if not scores:
        return 0.0
    v = scores[0].get("value")
    if isinstance(v, str):
        return 1.0 if v == "C" else 0.0
    return float(v or 0.0)


def inspect_rows(ev, reply_cap, digest, tasks, attempts):
    """One row per finished sample epoch of the bench's tasks over every log in ev/logs (a resumed run
    has several; the newest log wins for a sample it re-ran). Finished: the newest log completed and
    every task has every epoch. Returns (rows, finished)."""
    logs = sorted((ev / "logs").glob("*.json"))
    samples, status = {}, None
    for f in logs:
        log = json.loads(f.read_text())
        for smp in log.get("samples") or []:
            if smp["id"] in tasks:
                samples[(smp["id"], smp.get("epoch", 1))] = smp
        status = log.get("status")  # cancelled/error -> run.py --resume
    finished = status == "success" and all((t, e) in samples for t in tasks for e in range(1, attempts + 1))
    rows = []
    for (sid, epoch), smp in sorted(samples.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        usage = list((smp.get("model_usage") or {}).values())
        u = lambda k: sum((x.get(k) or 0) for x in usage) if usage else None  # noqa: E731
        assistant = [m for m in smp.get("messages") or [] if m.get("role") == "assistant"]
        model_events = [e for e in smp.get("events") or [] if e.get("event") == "model"]
        outputs = [(e.get("output") or {}) for e in model_events]
        out_usage = [o.get("usage") or {} for o in outputs]
        err = smp.get("error")
        row = {
            "trial": f"{sid}#e{epoch}",
            "task": sid,
            "task_ref": digest(smp.get("input"), smp.get("target"), smp.get("metadata")) if digest else None,
            "reward": reward_of(smp) if not err else 0.0,
            "exception": (err.get("message") or "error").splitlines()[0][:120] if err else None,
            # inspect counts cache reads separately; input_tokens here includes them, as Harbor's does
            "input_tokens": (u("input_tokens") or 0) + (u("input_tokens_cache_read") or 0) if usage else None,
            "cached_tokens": u("input_tokens_cache_read"),
            "output_tokens": u("output_tokens"),
            "reasoning_tokens": u("reasoning_tokens"),
            "llm_calls": len(model_events) or len(assistant),
            "tool_calls": sum(len(m.get("tool_calls") or []) for m in assistant),
            "max_prompt_tokens": max((x.get("input_tokens") or 0) + (x.get("input_tokens_cache_read") or 0)
                                     for x in out_usage) if out_usage else None,
            "max_output_tokens": max((x.get("output_tokens") or 0) for x in out_usage) if out_usage else None,
            "replies_over_cap": sum(1 for o in outputs if (o.get("choices") or [{}])[0].get("stop_reason") == "max_tokens"),
            "agent_s": round(smp["total_time"], 1) if smp.get("total_time") is not None else None,
            "working_s": round(smp["working_time"], 1) if smp.get("working_time") is not None else None,
            "started_at": smp.get("started_at"),
        }
        if row["reasoning_tokens"] is not None and row["output_tokens"]:
            row["reasoning_share"] = round(row["reasoning_tokens"] / row["output_tokens"], 3)
        rows.append(row)
    return rows, finished


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


PIECE = 24 * 1000 * 1000  # GitHub takes attachments up to 25 MB


def split(archive):
    """Split an archive too large to attach into <run>.part01.gz, .part02.gz, ... The pieces are not gzip
    files on their own (the .gz only satisfies GitHub's attachment filter); cat them back in order."""
    stem = archive.name.removesuffix(".tar.gz")
    for old in archive.parent.glob(f"{stem}.part*.gz"):
        old.unlink()
    if archive.stat().st_size <= PIECE:
        return []
    pieces = []
    with open(archive, "rb") as f:
        while chunk := f.read(PIECE):
            piece = archive.with_name(f"{stem}.part{len(pieces) + 1:02d}.gz")
            piece.write_bytes(chunk)
            pieces.append(piece)
    return pieces


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--pack", action="store_true", help="archive finished parts that are not packed yet")
    args = ap.parse_args()
    run_dir = args.run_dir.resolve()
    meta = json.loads((run_dir / "run.json").read_text())
    spec = tomllib.loads((run_dir / "bench.toml").read_text())
    reply_cap = spec["agent"]["max_output_tokens"]
    runner = spec.get("runner", "harbor")
    digest = bench_digest(meta["bench"]) if runner == "inspect" else None
    old = [json.loads(line) for line in (run_dir / "trials.jsonl").read_text().splitlines() if line.strip()] \
        if (run_dir / "trials.jsonl").exists() else []
    rows = []
    for part in meta["parts"]:
        ev = ROOT / "evidence" / meta["bench"] / part["job_name"]
        if not ev.exists():
            # Another contributor's part: no local evidence, keep its committed metrics as they are.
            kept = [r for r in old if r.get("part", meta["parts"][0]["job_name"]) == part["job_name"]]
            rows += kept
            print(f"{part['job_name']}: no local evidence, kept {len(kept)} committed trials")
            continue
        if runner == "inspect":
            part_rows, part["finished"] = inspect_rows(ev, reply_cap, digest, set(spec["suite"]["tasks"]), part["attempts"])
        else:
            part_rows = [row for f in sorted(ev.glob("jobs/*/*/result.json")) if (row := trial_row(f.parent, reply_cap))]
            job = next(ev.glob("jobs/*/result.json"), None)
            part["finished"] = bool(job and json.loads(job.read_text()).get("finished_at"))
        for row in part_rows:
            row["part"] = part["job_name"]
        rows += part_rows
        if args.pack and part["finished"] and not part.get("evidence"):
            archive = ev.parent / f"{ev.name}.tar.gz"
            subprocess.run(["tar", "-czf", str(archive), "-C", str(ev.parent), ev.name], check=True)
            pieces = split(archive)
            part["evidence"] = {"archive": archive.name, "bytes": archive.stat().st_size,
                                "sha256": sha256(archive), "pieces": [q.name for q in pieces], "url": None}
            print(f"evidence {archive} {archive.stat().st_size / 1e6:.1f} MB sha256 {part['evidence']['sha256']}"
                  + (f", split into {len(pieces)} pieces for upload" if pieces else ""))
        print(f"{part['job_name']}: {len(part_rows)} finished trials (job finished: {part['finished']})")
    (run_dir / "trials.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    meta["finished"] = all(p["finished"] for p in meta["parts"])
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"{len(rows)} trials -> {run_dir}/trials.jsonl (run finished: {meta['finished']})")


if __name__ == "__main__":
    main()

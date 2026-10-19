#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Rebuild RESULTS.md from every runs/<bench>/*/run.json and the Harbor trial results under it.

    uv run scripts/summarize.py

One leaderboard per bench and protocol, local recipes ranked by score, with the API references
marked. Score = mean reward over all trials of a run (TB convention: a trial that errors or times
out scores 0). The 95% interval is Wilson over trials, treating reward >= 1 as a pass.
"""
import json, math, statistics, tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def wilson(p, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def load_trials(run_dir):
    trials = []
    for f in sorted((run_dir / "jobs").glob("*/*/result.json")):
        r = json.loads(f.read_text())
        if "task_name" not in r:
            continue
        rewards = (r.get("verifier_result") or {}).get("rewards") or {}
        ar = r.get("agent_result") or {}
        ae = r.get("agent_execution") or {}
        start, end = ts(ae.get("started_at")), ts(ae.get("finished_at"))
        trials.append({
            "task": r["task_name"].split("/")[-1],
            "done": r.get("finished_at") is not None,
            "reward": float(rewards.get("reward", 0.0) or 0.0),
            "exception": (r.get("exception_info") or {}).get("exception_type"),
            "in": ar.get("n_input_tokens") or 0,
            "cache": ar.get("n_cache_tokens") or 0,
            "out": ar.get("n_output_tokens") or 0,
            "cost": ar.get("cost_usd") or 0.0,
            "agent_min": (end - start).total_seconds() / 60 if start and end else None,
        })
    return trials


def score_run(meta, trials, n_tasks):
    done = [t for t in trials if t["done"]]
    passes = sum(t["reward"] >= 1 for t in done)
    times = [t["agent_min"] for t in done if t["agent_min"] is not None]
    return {
        "done": done,
        "expected": meta["attempts"] * n_tasks,
        "score": statistics.fmean(t["reward"] for t in done) if done else 0.0,
        "ci": wilson(passes / len(done), len(done)) if done else (0.0, 0.0),
        "errors": sum(1 for t in done if t["exception"]),
        "tokens": [sum(t[k] for t in done) / 1e6 for k in ("in", "cache", "out")],
        "cost": sum(t["cost"] for t in done),
        "median_min": statistics.median(times) if times else None,
    }


def bench_section(bench_dir):
    protocols = {}
    for f in bench_dir.glob("protocol-v*.toml"):
        p = tomllib.loads(f.read_text())
        protocols[p["name"]] = p
    runs = []
    for d in sorted((ROOT / "runs" / bench_dir.name).glob("*/run.json")):
        meta = json.loads(d.read_text())
        runs.append((meta, load_trials(d.parent)))
    out = []
    for name in sorted({m["protocol"] for m, _ in runs}):
        tasks = protocols[name]["suite"]["tasks"]
        rows = [(m, score_run(m, t, len(tasks))) for m, t in runs if m["protocol"] == name]
        rows.sort(key=lambda r: (r[0]["recipe"].get("role") == "reference", -r[1]["score"]))
        refs = {m["recipe"]["model_family"]: s["score"] for m, s in rows
                if m["recipe"].get("role") == "reference" and not m.get("smoke")}
        out += [f"## {bench_dir.name} · protocol {name}", "",
                f"Settings: [benches/{bench_dir.name}/protocol-{name}.toml](benches/{bench_dir.name}/protocol-{name}.toml)", "",
                "| # | Recipe | Role | k | Score (95% CI) | vs API | Trials done · errored | Tokens in / cached / out (M) | Cost | Median agent time |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        rank = 0
        for m, s in rows:
            r = m["recipe"]
            local = r.get("role") != "reference"
            rank += local
            flags = (" *(smoke)*" if m.get("smoke") else "") + \
                    (f" *(running {len(s['done'])}/{s['expected']})*" if len(s["done"]) < s["expected"] else "")
            ref = refs.get(r["model_family"])
            delta = f"{(s['score'] - ref) * 100:+.1f} pt" if local and ref is not None else "-"
            lo, hi = s["ci"]
            out.append(
                f"| {rank if local else ''} | [{r['display_name']}](runs/{bench_dir.name}/{m['job_name']}/run.json){flags} | "
                f"{'API reference' if not local else 'local'} | {m['attempts']} | **{s['score']:.1%}** ({lo:.0%}–{hi:.0%}) | "
                f"{delta} | {len(s['done'])}/{s['expected']} · {s['errors']} | "
                f"{s['tokens'][0]:.2f} / {s['tokens'][1]:.2f} / {s['tokens'][2]:.2f} | "
                f"{'$%.2f' % s['cost'] if s['cost'] else '-'} | "
                f"{'%.0f min' % s['median_min'] if s['median_min'] is not None else '-'} |")
        out += ["", "Per task (passes / trials):", "",
                "| Recipe | " + " | ".join(f"`{t}`" for t in tasks) + " |",
                "|---|" + "---|" * len(tasks)]
        for m, s in rows:
            cells = []
            for task in tasks:
                tt = [t for t in s["done"] if t["task"] == task]
                e = sum(1 for t in tt if t["exception"])
                cells.append(f"{sum(t['reward'] >= 1 for t in tt)}/{len(tt)}" + (f" ({e} err)" if e else "") if tt else "·")
            out.append(f"| {m['recipe']['display_name']}{' *(smoke)*' if m.get('smoke') else ''} | " + " | ".join(cells) + " |")
        out.append("")
    return out


def main():
    lines = ["# Results", "",
             "Generated by `scripts/summarize.py`; do not edit by hand. Local recipes are ranked by score; "
             "official APIs are reference lines, and *vs API* is the gap to the same model's API reference "
             "(full runs only). Smoke runs (k < protocol) are noisy.", "",
             "Score = mean reward over all trials; a trial that errors or times out scores 0. "
             "`err` = trial raised an exception (timeout, context overflow, infrastructure).", ""]
    for bench_dir in sorted((ROOT / "benches").iterdir()):
        if bench_dir.is_dir():
            lines += bench_section(bench_dir)
    (ROOT / "RESULTS.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()

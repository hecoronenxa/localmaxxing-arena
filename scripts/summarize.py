#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Rebuild the README leaderboard, benches/<bench>/RESULTS.md and runs/<bench>/<run>/STATS.md from raw data.

CI runs this after every merge to main and commits the output; contributors only commit raw data.

    uv run scripts/summarize.py            # write
    uv run scripts/summarize.py --check    # exit 1 if any generated file is stale (CI)

The README leaderboard has one column per bench in leaderboard.toml; each bench's RESULTS.md has the
per-task and behaviour comparison. Score = mean reward over all trials of a run (TB convention: a trial that errors or times
out scores 0). The 95% interval is Wilson over trials, treating reward >= 1 as a pass.
"""
import argparse, json, math, statistics, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def wilson(p, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load_trials(run_dir):
    f = run_dir / "trials.jsonl"
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []


def mean(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return statistics.fmean(vals) if vals else None


def total(rows, key):
    return sum(r.get(key) or 0 for r in rows)


def fmt(v, spec="{:,.0f}"):
    return "-" if v is None else spec.format(v)


def score_run(meta, trials, n_tasks):
    passes = sum(t["reward"] >= 1 for t in trials)
    times = [t["agent_s"] / 60 for t in trials if t.get("agent_s") is not None]
    return {
        "trials": trials,
        "expected": meta["attempts"] * n_tasks,
        "score": statistics.fmean(t["reward"] for t in trials) if trials else 0.0,
        "ci": wilson(passes / len(trials), len(trials)) if trials else (0.0, 0.0),
        "errors": sum(1 for t in trials if t.get("exception")),
        "cost": total(trials, "cost_usd"),
        "median_min": statistics.median(times) if times else None,
    }


# Behaviour columns: (header, value over a list of trials). Averages are per trial.
BEHAVIOUR = [
    ("LLM calls", lambda t: fmt(mean(t, "llm_calls"), "{:.1f}")),
    ("Tool calls", lambda t: fmt(mean(t, "tool_calls"), "{:.1f}")),
    ("Output tok", lambda t: fmt(mean(t, "output_tokens"))),
    ("Reasoning tok (est)", lambda t: fmt(mean(t, "reasoning_tokens_est"))),
    ("Reasoning share", lambda t: fmt(mean(t, "reasoning_share"), "{:.0%}")),
    ("Input tok", lambda t: fmt(mean(t, "input_tokens"))),
    ("Cache hit", lambda t: fmt(total(t, "cached_tokens") / total(t, "input_tokens")
                                if total(t, "input_tokens") else None, "{:.0%}")),
    ("Max prompt", lambda t: fmt(max((r.get("max_prompt_tokens") or 0 for r in t), default=None))),
    ("Max reply", lambda t: fmt(max((r.get("max_output_tokens") or 0 for r in t), default=None))),
    ("Out tok/s", lambda t: fmt(total(t, "output_tokens") / total(t, "llm_s") if total(t, "llm_s") else None, "{:.1f}")),
    ("LLM share of agent time", lambda t: fmt(total(t, "llm_s") / total(t, "agent_s") if total(t, "agent_s") else None, "{:.0%}")),
    ("Agent min", lambda t: fmt(mean(t, "agent_s") and mean(t, "agent_s") / 60, "{:.1f}")),
    ("Parse err", lambda t: str(total(t, "parse_errors"))),
    ("Over reply cap", lambda t: str(total(t, "replies_over_cap"))),
    ("Summarized", lambda t: str(total(t, "summarizations"))),
    ("No task_complete", lambda t: str(sum(1 for r in t if not r.get("task_complete_calls")))),
]
BEHAVIOUR_NOTE = (
    "Per-trial averages unless noted. *Reasoning tok (est)* splits output tokens by the character share of "
    "reasoning vs visible output (Harbor does not keep the provider's reasoning-token count). *Max prompt / "
    "Max reply* = largest single LLM call. *Out tok/s* = output tokens / time inside LLM calls (includes "
    "prefill and queueing). *Parse err*, *Over reply cap* (reply cut at `max_tokens`; the actions in it were not run), *Summarized* (context "
    "summarizations) and *No task_complete* (trial ended without the agent declaring completion) are totals.")


def behaviour_table(first_header, label_rows):
    out = ["| " + " | ".join([first_header] + [h for h, _ in BEHAVIOUR]) + " |",
           "|---|" + "---|" * len(BEHAVIOUR)]
    for label, trials in label_rows:
        out.append("| " + " | ".join([label] + [f(trials) if trials else "·" for _, f in BEHAVIOUR]) + " |")
    return out


def recipe_of(meta):
    """Current recipe file (labels may be fixed after a run); the run's own copy if the file is gone."""
    path = ROOT / meta["recipe_file"]
    return tomllib.loads(path.read_text()) if path.exists() else meta["recipe"]


def name_cell(meta, r):
    tag = " *(API reference)*" if r.get("role") == "reference" else ""
    return f"[{r['display_name']}]({meta['recipe_file']}){tag}"


def run_stats(bench, meta, s, tasks):
    r = recipe_of(meta)
    lines = [f"# {r['display_name']} · {bench}", "",
             "Generated by `scripts/summarize.py`; do not edit by hand.", "",
             f"- Recipe: `{meta['recipe_file']}` ({r.get('role', 'local')})",
             f"- Weights: {r.get('weights_detail', '-')}", f"- Serving: {r.get('serving', '-')}", f"- Hardware: {r.get('hardware_detail', '-')}",
             f"- Attempts: {meta['attempts']}{' (smoke)' if meta.get('smoke') else ''}, concurrency {meta['n_concurrent']}, "
             f"{meta['harbor']}, runner {meta['host'].get('machine')} / {meta['host'].get('os')}",
             f"- Score: **{s['score']:.1%}** over {len(s['trials'])}/{s['expected']} trials, {s['errors']} errored",
             f"- Evidence: {(meta.get('evidence') or {}).get('archive', 'not packed')} "
             f"sha256 `{(meta.get('evidence') or {}).get('sha256', '-')}`", "",
             "## Per task", "",
             "| Task | Passes | Mean reward | Exceptions |", "|---|---|---|---|"]
    for task in tasks:
        tt = [t for t in s["trials"] if t["task"] == task]
        exc = sorted({t["exception"] for t in tt if t.get("exception")})
        lines.append(f"| `{task}` | {sum(t['reward'] >= 1 for t in tt)}/{len(tt)} | "
                     f"{fmt(mean(tt, 'reward'), '{:.2f}')} | {', '.join(exc) or '-'} |")
    lines += ["", "## Behaviour per task", "", BEHAVIOUR_NOTE, ""]
    lines += behaviour_table("Task", [(f"`{t}`", [x for x in s["trials"] if x["task"] == t]) for t in tasks]
                             + [("**all**", s["trials"])])
    return "\n".join(lines) + "\n"


def flags(meta, s):
    n = len(s["trials"])
    return (" *(smoke)*" if meta.get("smoke") else "") + (f" *(running {n}/{s['expected']})*" if n < s["expected"] else "")


def bench_section(bench_dir, files):
    """(RESULTS.md lines, README board column); adds each run's STATS.md to files."""
    spec = tomllib.loads((bench_dir / "bench.toml").read_text())
    tasks = spec["suite"]["tasks"]
    runs = [(json.loads(d.read_text()), d.parent) for d in sorted((ROOT / "runs" / bench_dir.name).glob("*/run.json"))]
    rows = [(m, score_run(m, load_trials(d), len(tasks)), d) for m, d in runs]
    rows.sort(key=lambda r: -r[1]["score"])
    board = {"bench": bench_dir.name, "spec": spec, "cells": {}}
    # One cell per recipe: its newest full run, else its newest smoke run.
    for m, s, d in sorted(rows, key=lambda r: (not r[0].get("smoke"), r[2].name)):
        board["cells"][m["recipe_file"]] = (m, s, d)
    for m, s, d in rows:
        files[ROOT / "runs" / bench_dir.name / d.name / "STATS.md"] = run_stats(bench_dir.name, m, s, tasks)
    if not rows:
        return [], board

    out = [f"## {spec['display_name']} (`{bench_dir.name}`)", "",
           f"Definition: [benches/{bench_dir.name}/bench.toml](benches/{bench_dir.name}/bench.toml)", "",
           "| Recipe | k | Score (95% CI) | Trials · errored | Cost | Median agent time |",
           "|---|---|---|---|---|---|"]
    for m, s, d in rows:
        lo, hi = s["ci"]
        out.append(f"| {name_cell(m, recipe_of(m))}{flags(m, s)} | {m['attempts']} | "
                   f"[**{s['score']:.1%}**](runs/{bench_dir.name}/{d.name}/STATS.md) ({lo:.0%}–{hi:.0%}) | "
                   f"{len(s['trials'])}/{s['expected']} · {s['errors']} | {'$%.2f' % s['cost'] if s['cost'] else '-'} | "
                   f"{'%.0f min' % s['median_min'] if s['median_min'] is not None else '-'} |")
    out += ["", "Per task (passes / trials):", "",
            "| Recipe | " + " | ".join(f"`{t}`" for t in tasks) + " |",
            "|---|" + "---|" * len(tasks)]
    for m, s, _ in rows:
        cells = []
        for task in tasks:
            tt = [t for t in s["trials"] if t["task"] == task]
            e = sum(1 for t in tt if t.get("exception"))
            cells.append(f"{sum(t['reward'] >= 1 for t in tt)}/{len(tt)}" + (f" ({e} err)" if e else "") if tt else "·")
        out.append(f"| {recipe_of(m)['display_name']}{flags(m, s)} | " + " | ".join(cells) + " |")
    out += ["", "Behaviour (per-task breakdown in each run's STATS.md):", "", BEHAVIOUR_NOTE, ""]
    out += behaviour_table("Recipe", [(recipe_of(m)["display_name"] + flags(m, s), s["trials"]) for m, s, _ in rows])
    out.append("")
    return out, board


BOARD_START, BOARD_END = "<!-- leaderboard:start -->", "<!-- leaderboard:end -->"


def readme_board(boards, retired):
    """One table: a score column per bench listed in leaderboard.toml, and their mean."""
    recipes = {}
    for b in boards:
        for f, (m, _, _) in b["cells"].items():
            recipes[f] = (m, recipe_of(m))
    out = []
    if not recipes:
        out.append("_No results yet._")
    else:
        def cell(b, f):
            if f not in b["cells"]:
                return "-", None
            m, s, d = b["cells"][f]
            return f"[{s['score']:.1%}](runs/{b['bench']}/{d.name}/STATS.md){flags(m, s)}", s["score"]

        rows = []
        for f, (m, r) in recipes.items():
            cells = [cell(b, f) for b in boards]
            scores = [v for _, v in cells if v is not None]
            overall = statistics.fmean(scores) if len(scores) == len(boards) else None
            efforts = sorted({b["spec"]["agent"]["reasoning_effort"] for b in boards if f in b["cells"]})
            rows.append((overall, m, r, cells, ", ".join(efforts)))
        rows.sort(key=lambda x: (x[0] is None, -(x[0] or 0), x[2]["display_name"]))
        out += ["| Recipe | Weights | Reasoning | Hardware | "
                + " | ".join(f"[{b['spec']['display_name']}](benches/{b['bench']}/RESULTS.md)" for b in boards)
                + " | Overall |", "|---|---|---|---|" + "---|" * len(boards) + "---|"]
        for overall, m, r, cells, effort in rows:
            out.append(f"| {name_cell(m, r)} | {r.get('weights', '-')} | {effort} | {r.get('hardware', '-')} | "
                       + " | ".join(c for c, _ in cells) + f" | {'**%.1f%%**' % (overall * 100) if overall is not None else '-'} |")
    if retired:
        out += ["", "Retired benchmarks (not in the table; results kept): "
                + ", ".join(f"[{b['spec']['display_name']}](benches/{b['bench']}/RESULTS.md)" for b in retired)]
    return "\n".join(out) + "\n"


def render():
    """Every generated file: {path: content}. README.md only has its leaderboard block replaced."""
    files = {}
    columns = tomllib.loads((ROOT / "leaderboard.toml").read_text())["columns"]
    boards = {}
    for bench_dir in sorted(d for d in (ROOT / "benches").iterdir() if (d / "bench.toml").exists()):
        section, boards[bench_dir.name] = bench_section(bench_dir, files)
        spec = boards[bench_dir.name]["spec"]
        files[bench_dir / "RESULTS.md"] = "\n".join([
            f"# {spec['display_name']} results", "",
            "Generated by `scripts/summarize.py` after each merge; do not edit by hand. Recipes ranked by score. "
            "Smoke runs (fewer attempts than the benchmark defines) are noisy. Score = mean reward over all trials; "
            "a trial that errors or times out scores 0. `err` = trial raised an exception (timeout, context "
            "overflow, infrastructure).", "", *(section[1:] if section else ["_No results yet._", ""])])
        # bench_section links from the repo root; this file sits two levels down.
        text = files[bench_dir / "RESULTS.md"]
        for prefix in ("recipes/", "runs/"):
            text = text.replace(f"]({prefix}", f"](../../{prefix}")
        files[bench_dir / "RESULTS.md"] = text.replace(f"](benches/{bench_dir.name}/bench.toml)", "](bench.toml)")
    retired = [boards[b] for b in sorted(boards) if b not in columns]
    readme = (ROOT / "README.md").read_text()
    head, rest = readme.split(BOARD_START, 1)
    tail = rest.split(BOARD_END, 1)[1]
    board = readme_board([boards[c] for c in columns], retired)
    files[ROOT / "README.md"] = f"{head}{BOARD_START}\n{board}{BOARD_END}{tail}"
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if generated files are stale")
    args = ap.parse_args()
    files = render()
    stale = [p for p, text in files.items() if not p.exists() or p.read_text() != text]
    if args.check:
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)} (run scripts/summarize.py)")
        sys.exit(1 if stale else 0)
    for p in stale:
        p.write_text(files[p])
    for p in stale:
        print(f"wrote {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

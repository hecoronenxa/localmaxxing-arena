#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Rebuild the README leaderboard, benches/<bench>/RESULTS.md and benches/<bench>/recipes/<id>.md.

CI runs this after every merge to main and commits the output; contributors only commit raw data.

    uv run scripts/summarize.py            # write
    uv run scripts/summarize.py --check    # exit 1 if any generated file is stale

All runs of a recipe on a bench are pooled: one score and k (summed attempts) per recipe per bench,
flagged k=1/3 while below the bench's n_attempts. The README leaderboard has one score column per bench
in leaderboard.toml and ranks recipes by a rating fitted on every trial of every bench, retired ones
included (scripts/rating.py). Score = mean reward over all trials (TB convention: a trial that errors
or times out scores 0). The 95% interval is Wilson over trials, treating reward >= 1 as a pass.
"""
import argparse, json, math, os, statistics, sys, tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rating  # noqa: E402

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


def score(trials):
    passes = sum(t["reward"] >= 1 for t in trials)
    times = [t["agent_s"] / 60 for t in trials if t.get("agent_s") is not None]
    return {
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


def link(target, page):
    """Markdown link target for repo path `target` from generated file `page` (both relative to ROOT)."""
    return Path(os.path.relpath(ROOT / target, (ROOT / page).parent)).as_posix()


def name_cell(entry, page):
    r = entry["recipe"]
    tags = (["API reference"] if r.get("role") == "reference" else []) + (["anchor"] if r.get("anchor") else [])
    return f"[{r['display_name']}]({link(entry['recipe_file'], page)})" + (f" *({', '.join(tags)})*" if tags else "")


def pct(entry, bold=False):
    """Score text; a recipe with no finished trial yet has no score."""
    if not entry["trials"]:
        return "-"
    return f"**{entry['score']:.1%}**" if bold else f"{entry['score']:.1%}"


def flag(entry, bench_attempts):
    """Shown next to a score: pooled attempts below the bench's (k=1/3), and unfinished runs."""
    out = f" *(k={entry['k']}/{bench_attempts})*" if entry["k"] < bench_attempts else ""
    return out + (f" *(running {len(entry['trials'])}/{entry['expected']})*" if len(entry["trials"]) < entry["expected"] else "")


def pool(bench, runs, n_tasks):
    """Every run of the same recipe on this bench pooled into one entry: trials, attempts (k), runs."""
    entries = {}
    for meta, run_dir in runs:
        e = entries.setdefault(meta["recipe_file"], {"recipe_file": meta["recipe_file"], "recipe": recipe_of(meta),
                                                     "id": meta["recipe"]["id"], "runs": [], "trials": [], "k": 0})
        trials = load_trials(run_dir)
        e["runs"].append((meta, run_dir, trials))
        e["trials"] += trials
        e["k"] += meta["attempts"]
    for e in entries.values():
        e["expected"] = e["k"] * n_tasks
        e.update(score(e["trials"]))
        e["page"] = f"benches/{bench}/recipes/{e['id']}.md"
    return sorted(entries.values(), key=lambda e: -e["score"])


def recipe_page(bench, spec, e):
    page, tasks, r = e["page"], spec["suite"]["tasks"], e["recipe"]
    lo, hi = e["ci"]
    lines = [f"# {r['display_name']} · {spec['display_name']}", "",
             "Generated by `scripts/summarize.py`; do not edit by hand. All runs of this recipe on this benchmark "
             "are pooled.", "",
             f"- Recipe: [{e['recipe_file']}]({link(e['recipe_file'], page)}) ({r.get('role', 'local')})",
             f"- Weights: {r.get('weights_detail', '-')}", f"- Serving: {r.get('serving', '-')}",
             f"- Hardware: {r.get('hardware_detail', '-')}",
             f"- Score: {pct(e, True)} ({lo:.0%}–{hi:.0%}) over {len(e['trials'])} trials, k={e['k']} results "
             f"(bench defines {spec['suite']['n_attempts']}), {e['errors']} errored", "",
             "## Runs", "",
             "Each run is one contribution. A run whose score sits far from the others deserves a look at its evidence.", "",
             "| Run | k | Score | Trials · errored | Harbor | Runner | Evidence (sha256) |", "|---|---|---|---|---|---|---|"]
    for meta, run_dir, trials in e["runs"]:
        sc = score(trials)
        host = meta.get("host") or {}
        ev = ", ".join(f"`{(p.get('evidence') or {}).get('archive', 'not packed')}` "
                       f"`{(p.get('evidence') or {}).get('sha256', '-')[:12]}`" for p in meta["parts"])
        lines.append(f"| [{run_dir.name}]({link(run_dir.relative_to(ROOT) / 'run.json', page)}) | {meta['attempts']} | "
                     f"{sc['score']:.1%} | {len(trials)}/{meta['attempts'] * len(tasks)} · {sc['errors']} | "
                     f"{meta['harbor']} | {host.get('machine', '-')} | {ev} |")
    lines += ["", "## Per task", "", "| Task | Passes | Mean reward | Exceptions |", "|---|---|---|---|"]
    for task in tasks:
        tt = [t for t in e["trials"] if t["task"] == task]
        exc = sorted({t["exception"] for t in tt if t.get("exception")})
        lines.append(f"| `{task}` | {sum(t['reward'] >= 1 for t in tt)}/{len(tt)} | "
                     f"{fmt(mean(tt, 'reward'), '{:.2f}')} | {', '.join(exc) or '-'} |")
    lines += ["", "## Behaviour per task", "", BEHAVIOUR_NOTE, ""]
    lines += behaviour_table("Task", [(f"`{t}`", [x for x in e["trials"] if x["task"] == t]) for t in tasks]
                             + [("**all**", e["trials"])])
    return "\n".join(lines) + "\n"


def bench_results(bench, spec, entries):
    page, tasks, n = f"benches/{bench}/RESULTS.md", spec["suite"]["tasks"], spec["suite"]["n_attempts"]
    out = [f"# {spec['display_name']} results", "",
           "Generated by `scripts/summarize.py` after each merge; do not edit by hand. Recipes ranked by score, all "
           f"runs of a recipe pooled; k = results (complete passes over the benchmark). *k=1/{n}* marks recipes with "
           f"fewer results than the benchmark's {n} (noisier; any contributor can add a run). Score = mean reward over all trials; a trial that errors or "
           "times out scores 0. `err` = trial raised an exception (timeout, context overflow, infrastructure).", "",
           f"Definition: [bench.toml]({link(f'benches/{bench}/bench.toml', page)})", ""]
    if not entries:
        return "\n".join(out + ["_No results yet._", ""])
    out += ["| Recipe | k | Runs | Score (95% CI) | Trials · errored | Cost | Median agent time |",
            "|---|---|---|---|---|---|---|"]
    for e in entries:
        lo, hi = e["ci"]
        out.append(f"| {name_cell(e, page)} | {e['k']} | {len(e['runs'])} | "
                   f"[{pct(e, True)}]({link(e['page'], page)}){flag(e, n)} ({lo:.0%}–{hi:.0%}) | "
                   f"{len(e['trials'])}/{e['expected']} · {e['errors']} | {'$%.2f' % e['cost'] if e['cost'] else '-'} | "
                   f"{'%.0f min' % e['median_min'] if e['median_min'] is not None else '-'} |")
    out += ["", "Per task (passes / trials):", "",
            "| Recipe | " + " | ".join(f"`{t}`" for t in tasks) + " |", "|---|" + "---|" * len(tasks)]
    for e in entries:
        cells = []
        for task in tasks:
            tt = [t for t in e["trials"] if t["task"] == task]
            err = sum(1 for t in tt if t.get("exception"))
            cells.append(f"{sum(t['reward'] >= 1 for t in tt)}/{len(tt)}" + (f" ({err} err)" if err else "") if tt else "·")
        out.append(f"| {e['recipe']['display_name']} | " + " | ".join(cells) + " |")
    out += ["", "Behaviour (per-task breakdown on each recipe's page):", "", BEHAVIOUR_NOTE, ""]
    out += behaviour_table("Recipe", [(e["recipe"]["display_name"], e["trials"]) for e in entries])
    return "\n".join(out) + "\n"


BOARD_START, BOARD_END = "<!-- leaderboard:start -->", "<!-- leaderboard:end -->"


def readme_board(boards, retired):
    """One table: a pooled score column per bench in leaderboard.toml, plus a rating over every bench."""
    everything = boards + retired
    recipes = {}
    for b in everything:
        for e in b["entries"]:
            recipes[e["recipe_file"]] = e
    if not recipes:
        out = ["_No results yet._"]
    else:
        ratings = rating.fit((e["recipe_file"], (b["bench"], t["task"]), t["reward"])
                             for b in everything for e in b["entries"] for t in e["trials"])
        active_tasks = {(b["bench"], t) for b in boards for t in b["spec"]["suite"]["tasks"]}
        rows = []
        for f, e in recipes.items():
            mine = [(b, x) for b in everything for x in b["entries"] if x["recipe_file"] == f]
            covered = {(b["bench"], t["task"]) for b, x in mine for t in x["trials"]}
            active = len(covered & active_tasks)
            coverage = f"{active}/{len(active_tasks)}" if active else ("retired benches only" if covered else "-")
            if active and len(covered) > active:
                coverage += f" (+{len(covered) - active} retired)"
            cells = []
            for b in boards:
                x = next((x for x in b["entries"] if x["recipe_file"] == f), None)
                cells.append(f"[{pct(x)}]({x['page']}){flag(x, b['spec']['suite']['n_attempts'])}" if x else "-")
            efforts = ", ".join(sorted({b["spec"]["agent"]["reasoning_effort"] for b, _ in mine}))
            rows.append((ratings.get(f), e, cells, efforts, coverage))
        rows.sort(key=lambda x: (x[0] is None, -(x[0] or (0, 0))[0], x[1]["recipe"]["display_name"]))
        out = ["| Recipe | Weights | Reasoning | Hardware | "
               + "".join(f"[{b['spec']['display_name']}](benches/{b['bench']}/RESULTS.md) | " for b in boards)
               + "Rating (95% CI) | Coverage |", "|---|---|---|---|" + "---|" * len(boards) + "---|---|"]
        for rated, e, cells, efforts, coverage in rows:
            r = e["recipe"]
            rating_cell = f"**{rated[0]:.0f}** ({rated[0] - rated[1]:.0f}–{rated[0] + rated[1]:.0f})" if rated else "-"
            out.append(f"| {name_cell(e, 'README.md')} | {r.get('weights', '-')} | {efforts} | {r.get('hardware', '-')} | "
                       + "".join(f"{c} | " for c in cells) + f"{rating_cell} | {coverage} |")
    if retired:
        out += ["", "Retired benchmarks (no column, still in the rating): "
                + ", ".join(f"[{b['spec']['display_name']}](benches/{b['bench']}/RESULTS.md)" for b in retired)]
    return "\n".join(out) + "\n"


def render():
    """Every generated file: {path: content}. README.md only has its leaderboard block replaced."""
    files = {}
    columns = tomllib.loads((ROOT / "leaderboard.toml").read_text())["columns"]
    boards = {}
    for bench_dir in sorted(d for d in (ROOT / "benches").iterdir() if (d / "bench.toml").exists()):
        bench = bench_dir.name
        spec = tomllib.loads((bench_dir / "bench.toml").read_text())
        runs = [(json.loads(f.read_text()), f.parent) for f in sorted((ROOT / "runs" / bench).glob("*/run.json"))]
        entries = pool(bench, runs, len(spec["suite"]["tasks"]))
        boards[bench] = {"bench": bench, "spec": spec, "entries": entries}
        files[bench_dir / "RESULTS.md"] = bench_results(bench, spec, entries)
        for e in entries:
            files[ROOT / e["page"]] = recipe_page(bench, spec, e)
    retired = [boards[b] for b in sorted(boards) if b not in columns]
    readme = (ROOT / "README.md").read_text()
    head, rest = readme.split(BOARD_START, 1)
    tail = rest.split(BOARD_END, 1)[1]
    files[ROOT / "README.md"] = f"{head}{BOARD_START}\n{readme_board([boards[c] for c in columns], retired)}{BOARD_END}{tail}"
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
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(files[p])
    for p in stale:
        print(f"wrote {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

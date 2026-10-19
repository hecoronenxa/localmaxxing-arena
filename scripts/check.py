#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Validate every run in runs/ against its benchmark and recipe. CI runs this on each PR.

    uv run scripts/check.py

A run passes when its bench.toml copy is identical to benches/<bench>/bench.toml (benchmarks are
frozen), its recipe still exists with the same id, model and api_base, each part's <runner>-config-p<N>.json
is exactly what scripts/run.py generates from them, it ran on the bench's harness (Harbor version, or the
inspect requirements), every trial used the pinned task content, trials.jsonl covers every task `attempts` times, every part is finished with its evidence archive recorded,
and the leaderboard renders. Every leaderboard column after the first also needs full runs from at
least two anchor recipes. Speed records (speed/<recipe id>/*.json) are validated too. Generated files are not checked: the publish workflow rebuilds them on main.
"""
import importlib.util, json, statistics, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_run(run_dir, run_py):
    errs = []
    meta = json.loads((run_dir / "run.json").read_text())
    bench = run_dir.parent.name
    if meta.get("bench") != bench:
        errs.append(f"run.json bench {meta.get('bench')!r} but directory is runs/{bench}/")
    bench_src = ROOT / "benches" / bench / "bench.toml"
    recipe_src = ROOT / meta["recipe_file"]
    if not bench_src.exists():
        return [f"{bench_src.relative_to(ROOT)} missing"]
    if (run_dir / "bench.toml").read_text() != bench_src.read_text():
        return [f"bench.toml differs from {bench_src.relative_to(ROOT)}: benchmarks are frozen, a change needs a new bench id"]
    if not recipe_src.exists():
        return [f"{recipe_src.relative_to(ROOT)} missing"]
    spec = tomllib.loads(bench_src.read_text())
    ran = tomllib.loads((run_dir / "recipe.toml").read_text())
    recipe = tomllib.loads(recipe_src.read_text())
    for key in ("id", "model", "api_base"):
        if ran.get(key) != recipe.get(key):
            errs.append(f"recipe {key} changed since the run ({ran.get(key)!r} -> {recipe.get(key)!r}); use a new recipe id")

    runner = spec.get("runner", "harbor")
    for part in meta["parts"]:
        config = run_dir / part["config"]
        cfg = json.loads(config.read_text()) if config.exists() else {}
        sub = "jobs" if runner == "harbor" else "logs"
        out_dir = Path(cfg.get("jobs_dir" if runner == "harbor" else "log_dir", ""))  # absolute path on the runner host
        if out_dir.parts[-4:] != ("evidence", bench, part["job_name"], sub):
            errs.append(f"{part['config']}: output dir {str(out_dir)!r} is not .../evidence/{bench}/{part['job_name']}/{sub}")
        make = run_py.harbor_config if runner == "harbor" else run_py.inspect_config
        expected = make(spec, ran, part["attempts"], part["n_concurrent"], part["job_name"], out_dir)
        if cfg != expected:
            errs.append(f"{part['config']} is not what scripts/run.py generates from bench.toml and recipe.toml")
        ev = part.get("evidence") or {}
        if not (ev.get("archive") and ev.get("sha256") and ev.get("bytes")):
            errs.append(f"{part['job_name']}: evidence not packed (scripts/collect.py --pack)")
        if not part.get("finished"):
            errs.append(f"{part['job_name']}: {runner} job not finished")
    if meta["attempts"] != sum(p["attempts"] for p in meta["parts"]):
        errs.append("attempts is not the sum of the parts' attempts")
    harness = meta.get("harness", meta.get("harbor", ""))
    if runner == "harbor" and spec["harbor_version"] not in harness:
        errs.append(f"ran on {harness!r}, bench requires Harbor {spec['harbor_version']}")
    if runner == "inspect" and harness != run_py.harness_version(spec):
        errs.append(f"ran on {harness!r}, bench requires {run_py.harness_version(spec)!r}")

    rows = [json.loads(l) for l in (run_dir / "trials.jsonl").read_text().splitlines() if l.strip()] \
        if (run_dir / "trials.jsonl").exists() else []
    tasks = spec["suite"]["tasks"]
    pins = spec["suite"].get("task_refs", {})
    for task in tasks:
        n = sum(1 for r in rows if r.get("task") == task)
        if n != meta["attempts"]:
            errs.append(f"{task}: {n} trials, expected {meta['attempts']}")
    if extra := sorted({r.get("task") for r in rows} - set(tasks)):
        errs.append(f"tasks not in bench: {extra}")
    for r in rows:
        if not isinstance(r.get("reward"), (int, float)):
            errs.append(f"{r.get('trial')}: reward missing")
        if pins.get(r.get("task")) and r.get("task_ref") != pins[r["task"]]:
            errs.append(f"{r.get('trial')}: task content {r.get('task_ref')} is not the pinned {pins[r['task']]}")
    return errs


def main():
    run_py = load("run")
    failed = False
    for run_json in sorted(ROOT.glob("runs/*/*/run.json")):
        run_dir = run_json.parent
        errs = check_run(run_dir, run_py)
        print(f"{'FAIL' if errs else 'ok  '} {run_dir.relative_to(ROOT)}")
        for e in errs:
            print(f"     - {e}")
        failed |= bool(errs)
    failed |= not check_anchors()
    failed |= not check_speed()
    load("summarize").render()  # generated files are rebuilt after merge; here only prove the data renders
    sys.exit(1 if failed else 0)


def check_speed():
    """speed/<recipe id>/*.json from scripts/speed.py: recipe still matches, settings and medians intact."""
    ok = True
    for f in sorted(ROOT.glob("speed/*/*.json")):
        rec, errs = json.loads(f.read_text()), []
        recipe_file = ROOT / rec.get("recipe_file", "")
        if not recipe_file.is_file():
            errs.append(f"{rec.get('recipe_file')} missing")
        else:
            recipe = tomllib.loads(recipe_file.read_text())
            if any(rec["recipe"].get(k) != recipe.get(k) for k in ("id", "model", "api_base")):
                errs.append("recipe id / model / api_base changed since the measurement")
            if f.parent.name != recipe["id"]:
                errs.append(f"stored under speed/{f.parent.name}/, recipe id is {recipe['id']}")
        if (rec.get("context_tokens"), rec.get("generate_tokens"), rec.get("concurrency")) != (32768, 512, 1):
            errs.append("not measured with scripts/speed.py settings (32768 context, 512 generated, 1 stream)")
        samples = rec.get("samples") or []
        if len(samples) < 3:
            errs.append(f"{len(samples)} samples, needs 3")
        if any(not 0.9 * 32768 <= s["prompt_tokens"] <= 1.1 * 32768 for s in samples):
            errs.append("a sample's prompt is not within 10% of 32,768 tokens")
        if samples and (rec.get("pp_tok_s") != statistics.median(s["pp_tok_s"] for s in samples)
                        or rec.get("tg_tok_s") != statistics.median(s["tg_tok_s"] for s in samples if s["tg_tok_s"] is not None)):
            errs.append("pp_tok_s / tg_tok_s are not the medians of the samples")
        print(f"{'FAIL' if errs else 'ok  '} {f.relative_to(ROOT)}")
        for e in errs:
            print(f"     - {e}")
        ok &= not errs
    return ok


def check_anchors(minimum=2):
    """Every leaderboard column after the first needs >= minimum anchor recipes whose finished runs pool
    to the bench's n_attempts, so its task difficulties are tied to the rating scale the other benches set."""
    columns = tomllib.loads((ROOT / "leaderboard.toml").read_text())["columns"]
    ok = True
    for bench in columns[1:]:
        need = tomllib.loads((ROOT / "benches" / bench / "bench.toml").read_text())["suite"]["n_attempts"]
        pooled = {}
        for run_json in (ROOT / "runs" / bench).glob("*/run.json"):
            meta = json.loads(run_json.read_text())
            recipe = ROOT / meta["recipe_file"]
            if meta.get("finished") and recipe.exists() and tomllib.loads(recipe.read_text()).get("anchor"):
                pooled[meta["recipe_file"]] = pooled.get(meta["recipe_file"], 0) + meta["attempts"]
        anchors = [f for f, k in pooled.items() if k >= need]
        if len(anchors) < minimum:
            print(f"FAIL leaderboard.toml lists {bench}, which has {len(anchors)} anchors at k>={need} (needs {minimum})")
            ok = False
    return ok


if __name__ == "__main__":
    main()

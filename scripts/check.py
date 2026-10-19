#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Validate every run in runs/ against its bench protocol and recipe. CI runs this on each PR.

    uv run scripts/check.py

A run passes when its protocol and recipe copies match the files in benches/ and recipes/, its
harbor-config.json is exactly what scripts/run.py generates from them, it ran on the protocol's Harbor
version, trials.jsonl covers every task attempts times, the evidence archive is recorded, and the
generated RESULTS.md / STATS.md files are up to date.
"""
import importlib.util, json, sys, tomllib
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
    proto_src = ROOT / "benches" / bench / f"protocol-{meta['protocol']}.toml"
    recipe_src = ROOT / meta["recipe_file"]
    for copy, src in ((run_dir / "protocol.toml", proto_src), (run_dir / "recipe.toml", recipe_src)):
        if not src.exists():
            errs.append(f"{src.relative_to(ROOT)} missing")
        elif not copy.exists() or copy.read_text() != src.read_text():
            errs.append(f"{copy.name} differs from {src.relative_to(ROOT)}")
    if errs:
        return errs
    protocol = tomllib.loads(proto_src.read_text())
    recipe = tomllib.loads(recipe_src.read_text())

    expected = run_py.harbor_config(protocol, recipe, meta["attempts"], meta["n_concurrent"], meta["job_name"],
                                    Path("evidence") / bench / meta["job_name"] / "jobs")
    if json.loads((run_dir / "harbor-config.json").read_text()) != expected:
        errs.append("harbor-config.json is not what scripts/run.py generates from the protocol and recipe")
    if protocol["harbor_version"] not in meta.get("harbor", ""):
        errs.append(f"ran on {meta.get('harbor')!r}, protocol requires Harbor {protocol['harbor_version']}")
    if meta.get("smoke") != (meta["attempts"] != protocol["suite"]["n_attempts"]):
        errs.append("smoke flag does not match attempts vs protocol n_attempts")

    rows = [json.loads(l) for l in (run_dir / "trials.jsonl").read_text().splitlines() if l.strip()] \
        if (run_dir / "trials.jsonl").exists() else []
    tasks = protocol["suite"]["tasks"]
    for task in tasks:
        n = sum(1 for r in rows if r.get("task") == task)
        if n != meta["attempts"]:
            errs.append(f"{task}: {n} trials, expected {meta['attempts']}")
    if extra := sorted({r.get("task") for r in rows} - set(tasks)):
        errs.append(f"tasks not in protocol: {extra}")
    for r in rows:
        if not isinstance(r.get("reward"), (int, float)):
            errs.append(f"{r.get('trial')}: reward missing")
    ev = meta.get("evidence") or {}
    if not (ev.get("archive") and ev.get("sha256") and ev.get("bytes")):
        errs.append("evidence not packed (scripts/collect.py --pack)")
    if not meta.get("finished"):
        errs.append("Harbor job not finished")
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
    stale = [p for p, text in load("summarize").render().items() if not p.exists() or p.read_text() != text]
    for p in stale:
        print(f"FAIL {p.relative_to(ROOT)} is stale (run scripts/summarize.py)")
    sys.exit(1 if failed or stale else 0)


if __name__ == "__main__":
    main()

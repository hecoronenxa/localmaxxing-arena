#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Reproduce the SciCode-10-v2 task list in bench.toml.

    uv run benches/scicode-10-v2/select.py --upstream ~/dev/harbor/datasets/scicode

--upstream is the Harbor scicode adapter's output: one directory per SciCode problem (80).
SciCode's 15 dev problems ship public reference solutions (the adapter writes a real solve.sh for them),
so they are excluded to keep the bench uncontaminated. Of the remaining 65, sort by scicode_num_steps
desc, then problem id asc, and take 10 (the cutoff is 7 steps; the 11th has 6). Also checks that the
vendored dataset/ directory in this bench holds exactly those tasks with the same step counts, and that
none of them carries a reference solution.
"""
import argparse, tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
N = 10

# SciCode dev split: the problems whose reference solution is public. Cross-checked against solve.sh below.
DEV = {1, 3, 4, 6, 7, 10, 19, 29, 38, 44, 47, 49, 51, 70, 78}

PLACEHOLDER = "No oracle solution available for test split problems."


def meta(task_dir):
    m = tomllib.loads((task_dir / "task.toml").read_text())["metadata"]
    return int(m["scicode_problem_id"]), int(m["scicode_num_steps"])


def has_reference_solution(task_dir):
    return PLACEHOLDER not in (task_dir / "solution" / "solve.sh").read_text()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", type=Path, required=True, help="Harbor scicode adapter output (80 tasks)")
    args = ap.parse_args()

    dirs = [d for d in args.upstream.iterdir() if d.is_dir() and (d / "task.toml").exists()]
    rows = [meta(d) for d in dirs]
    if len(rows) != 80:
        raise SystemExit(f"expected 80 upstream tasks, found {len(rows)}")

    dev = {pid for (pid, _), d in zip(rows, dirs) if has_reference_solution(d)}
    if dev != DEV:
        raise SystemExit(f"dev split mismatch: solve.sh says {sorted(dev)}, script says {sorted(DEV)}")

    rows = [(pid, steps) for pid, steps in rows if pid not in DEV]
    rows.sort(key=lambda r: (-r[1], r[0]))
    chosen = rows[:N]
    print(f"{len(rows)} non-dev problems; cutoff {chosen[-1][1]} steps, next {rows[N][1]}")
    for pid, steps in chosen:
        print(f"  scicode-{pid}: {steps} steps")

    vendored = {d.name: meta(d) for d in (HERE / "dataset").iterdir() if d.is_dir()}
    expected = {f"scicode-{pid}": (pid, steps) for pid, steps in chosen}
    if vendored != expected:
        raise SystemExit(f"dataset/ does not match the selection:\n  missing {sorted(expected.keys() - vendored.keys())}\n"
                         f"  extra {sorted(vendored.keys() - expected.keys())}\n"
                         f"  differing {sorted(k for k in expected.keys() & vendored.keys() if expected[k] != vendored[k])}")
    if reference := sorted(d.name for d in (HERE / "dataset").iterdir() if has_reference_solution(d)):
        raise SystemExit(f"vendored tasks with a reference solution (dev split): {reference}")

    # v2 invariant: the ground truth is mounted only in the verifier container, and the verifier image
    # bundles /tests (Harbor skips the upload when the verifier env comes from the tests directory).
    broken = []
    for d in sorted((HERE / "dataset").iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        if (d / "environment" / "docker-compose.yaml").exists():
            broken.append(f"{name}: agent environment still has a docker-compose.yaml")
        if "/app/test_data.h5" in (d / "environment" / "Dockerfile").read_text():
            broken.append(f"{name}: agent Dockerfile references test_data.h5")
        if "test_data.h5" not in (d / "tests" / "docker-compose.yaml").read_text():
            broken.append(f"{name}: verifier docker-compose.yaml does not mount test_data.h5")
        if "COPY . /tests" not in (d / "tests" / "Dockerfile").read_text():
            broken.append(f"{name}: verifier Dockerfile does not bundle /tests")
        task_toml = (d / "task.toml").read_text()
        if 'environment_mode = "separate"' not in task_toml:
            broken.append(f"{name}: [verifier] environment_mode is not 'separate'")
        if 'artifacts = ["/app/solution.py"]' not in task_toml:
            broken.append(f"{name}: solution.py is not declared as an artifact")
    if broken:
        raise SystemExit("v2 layout broken:\n  " + "\n  ".join(broken))
    print("dataset/ matches and the verifier is isolated.")


if __name__ == "__main__":
    main()

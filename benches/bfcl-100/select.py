#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "inspect-ai==0.3.281",
#   "inspect_evals @ git+https://github.com/UKGovernmentBEIS/inspect_evals@8ef810f09930362302fc59b10a382d96dd767171",
# ]
# ///
"""Reproduce the BFCL-100 task list and content digests in bench.toml.

    uv run benches/bfcl-100/select.py

Loads inspect_evals' BFCL (which pins the upstream gorilla data at commit dac44e7) for the four hard
multi-turn categories, sorts each category's sample ids, draws 25 per category with random.Random(SEED),
and prints the [suite] task list plus a sha256 per task over its canonical content (input, target,
metadata without the run-time model_* fields; message ids and null fields dropped). scripts/collect.py computes the same digest from each
sample in a run's log, so CI can tell that a run answered exactly these tasks.
"""
import hashlib, json, random, re

SEED = 100
PER_CATEGORY = 25
CATEGORIES = ["multi_turn_miss_func", "multi_turn_miss_param", "multi_turn_long_context", "multi_turn_composite"]


def canon(x):
    if isinstance(x, dict):
        return {k: canon(v) for k, v in x.items() if k != "id" and v is not None}
    if isinstance(x, list):
        return [canon(v) for v in x]
    return x


def digest(input_, target, metadata):
    # The solver writes the model's own calls back into metadata (model_execution_*); not task content.
    metadata = {k: v for k, v in (metadata or {}).items() if not k.startswith("model_")}
    body = json.dumps(canon({"input": input_, "target": target, "metadata": metadata}), sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def main():
    from inspect_evals.bfcl import bfcl
    samples = {s.id: s for s in bfcl(categories=CATEGORIES).dataset}
    num = lambda i: int(re.search(r"(\d+)$", i).group(1))  # noqa: E731
    chosen = []
    for cat in CATEGORIES:
        ids = sorted((i for i in samples if i.rsplit("_", 1)[0] == cat), key=num)
        chosen += sorted(random.Random(f"{SEED}:{cat}").sample(ids, PER_CATEGORY), key=num)
    print("tasks = [")
    for i in chosen:
        print(f'  "{i}",')
    print("]\n\n[suite.task_refs]")
    for i in chosen:
        s = samples[i]
        inp = [m.model_dump(mode="json") for m in s.input] if isinstance(s.input, list) else s.input
        print(f'{i} = "{digest(inp, s.target, s.metadata)}"')


if __name__ == "__main__":
    main()

#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Measure a recipe's prefill (pp) and decode (tg) speed at a 32K-token context, single stream.

    uv run scripts/speed.py recipes/<id>.toml              # writes speed/<recipe id>/<date>[_<n>].json
    uv run scripts/speed.py recipes/<id>.toml --dry-run    # measure and print, write nothing

Each sample sends ~32,768 prompt tokens of English prose (calibrated with one probe request) and streams
up to 512 generated tokens:

    pp = prompt_tokens / time to first streamed token        (prefill, includes network latency)
    tg = (completion_tokens - 1) / (last token - first token) (decode; reasoning tokens count)

Every request starts with a fresh random tag, so no sample can reuse a cached prefix. The record keeps
every sample and the medians. Run it while the endpoint serves nothing else: other load slows both.
"""
import argparse, datetime, json, os, platform, random, statistics, sys, time, tomllib, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTEXT, GENERATE, SAMPLES = 32768, 512, 3

WORDS = {
    "subject": ["the old sailor", "a young teacher", "the village doctor", "my grandmother", "the stranger",
                "a tired engineer", "the baker's son", "our neighbour", "the night guard", "a curious child"],
    "verb": ["remembered", "carried", "described", "noticed", "repaired", "followed", "painted", "wrote about",
             "argued over", "searched for", "collected", "explained"],
    "object": ["the broken lantern", "a letter from the coast", "the winter harvest", "an unfinished map",
               "the bells of the church", "a box of photographs", "the river after the storm", "a forgotten song",
               "the price of bread", "the road to the mountains"],
    "tail": ["before the sun went down", "while the rain kept falling", "without telling anyone",
             "as the market closed", "late into the evening", "with surprising patience",
             "for the third time that week", "under the station clock"],
}


def prose(words, rng):
    out, n = [], 0
    while n < words:
        s = f"{rng.choice(WORDS['subject']).capitalize()} {rng.choice(WORDS['verb'])} {rng.choice(WORDS['object'])} {rng.choice(WORDS['tail'])}."
        out.append(s)
        n += len(s.split())
    return " ".join(out)


def stream(recipe, key, text, max_tokens):
    """One streamed chat request; returns (prompt_tokens, completion_tokens, t_first, t_last, t_start)."""
    body = {"model": recipe["model"].split("/", 1)[1], "stream": True, "stream_options": {"include_usage": True},
            "max_tokens": max_tokens, "temperature": 1.0,
            "messages": [{"role": "user", "content": text + "\n\nContinue this story in the same style. Write at least 2,000 words."}]}
    req = urllib.request.Request(recipe["api_base"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    t_start = time.perf_counter()
    t_first = t_last = None
    usage = None
    with urllib.request.urlopen(req, timeout=1800) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[5:])
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning"):
                    now = time.perf_counter()
                    t_first = t_first or now
                    t_last = now
    if not usage or t_first is None:
        sys.exit("the endpoint streamed no tokens or no usage (needs stream_options.include_usage)")
    return usage["prompt_tokens"], usage["completion_tokens"], t_first, t_last, t_start


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("recipe", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    recipe = tomllib.loads(args.recipe.read_text())
    key_env = recipe.get("api_key_env") or ""
    if key_env and not os.environ.get(key_env):
        sys.exit(f"{key_env} is not set")
    key = os.environ.get(key_env, "local") if key_env else "local"

    rng = random.Random()
    probe_words = 2000
    p_tokens, *_ = stream(recipe, key, f"[{rng.getrandbits(64):016x}] " + prose(probe_words, rng), 1)
    words = int(probe_words * CONTEXT / p_tokens)
    print(f"calibrated: {probe_words} words = {p_tokens} tokens; using {words} words for ~{CONTEXT} tokens", flush=True)

    samples = []
    for i in range(SAMPLES):
        tag = f"[{rng.getrandbits(64):016x}] "
        prompt, completion, t_first, t_last, t_start = stream(recipe, key, tag + prose(words, rng), GENERATE)
        ttft, decode = t_first - t_start, t_last - t_first
        s = {"prompt_tokens": prompt, "ttft_s": round(ttft, 3), "pp_tok_s": round(prompt / ttft, 1),
             "completion_tokens": completion, "decode_s": round(decode, 3),
             "tg_tok_s": round((completion - 1) / decode, 1) if decode > 0 and completion > 1 else None}
        samples.append(s)
        print(f"sample {i + 1}: {prompt} prompt tokens, pp {s['pp_tok_s']} tok/s (ttft {ttft:.2f} s); "
              f"{completion} generated, tg {s['tg_tok_s']} tok/s", flush=True)

    record = {
        "recipe_file": str(args.recipe.resolve().relative_to(ROOT)),
        "recipe": {k: recipe.get(k) for k in ("id", "model", "api_base")},
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "context_tokens": CONTEXT, "generate_tokens": GENERATE, "concurrency": 1,
        "host": {"os": platform.platform(), "machine": platform.machine()},
        "samples": samples,
        "pp_tok_s": statistics.median(s["pp_tok_s"] for s in samples),
        "tg_tok_s": statistics.median(s["tg_tok_s"] for s in samples if s["tg_tok_s"] is not None),
    }
    print(f"median: pp {record['pp_tok_s']} tok/s, tg {record['tg_tok_s']} tok/s")
    if args.dry_run:
        return
    out_dir = ROOT / "speed" / recipe["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    base = datetime.date.today().isoformat()
    out, n = out_dir / f"{base}.json", 1
    while out.exists():
        n += 1
        out = out_dir / f"{base}_{n}.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

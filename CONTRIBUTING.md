# Contributing

You run a recipe (a model served a specific way) on a benchmark, and submit the result as a pull
request. Every result lands this way, ours included. Want a recipe benchmarked but can't run it?
[Open a recipe request](https://github.com/kky42/localmaxxing-arena/issues/new?template=recipe-request.yml).

## Terms

- **Recipe**: one endpoint, described by a file in [recipes/](recipes/): model weights (repo @
  revision, quantization), serving engine @ commit with the flags that matter, hardware.
- **Benchmark**: a frozen task suite with fixed settings, defined by `benches/<bench>/bench.toml`.
- **Result**: one complete pass over a benchmark, one attempt per task.
- **Run**: one invocation of `scripts/run.py`, producing one or more results (`--attempts N` = N
  results). A run is stored as `runs/<bench>/<run>/`.
- **k**: the number of results a recipe has on a benchmark, summed over all its runs. Each benchmark
  sets a target (`n_attempts` in its `bench.toml`).

All runs of the same recipe on the same benchmark are **pooled** into one score. Any k is accepted: a
single result (k=1) is a useful PR. Below the benchmark's target the score is flagged *k=1/3* on the
leaderboard, and every further run, from anyone, narrows its interval.

## 1. Pick or write a recipe

To add results for an existing recipe, use its file unchanged and serve the model exactly as it
describes. To benchmark something new, copy an existing file to `recipes/<model>@<recipe>.toml`:

| Field | |
|---|---|
| `id` | the file name without `.toml`; never reused for a different setup |
| `role` | `local`, `reference` for an official API (tagged *API reference*), or `proxy` for an unofficial reverse proxy over a subscription (tagged *subscription proxy*) |
| `model_family` | ties a recipe to its API reference, e.g. `DeepSeek-V4.1-Flash-1017` |
| `anchor` | `true` only for the few recipes committed to running every benchmark (see below) |
| `display_name` | leaderboard name: model · engine, e.g. `DeepSeek-V4.1-Flash · TensorFold TP2` |
| `weights`, `hardware` | short leaderboard labels, e.g. `EXL3 2.9bpw`, `2× DGX Spark` |
| `weights_detail`, `serving`, `hardware_detail` | enough for someone else to rebuild it: repo @ revision, quant, engine @ commit, launch flags, hardware |
| `model`, `api_base`, `api_key_env` | any OpenAI-compatible endpoint: `model = "openai/<served name>"`; `api_key_env` names the environment variable holding the key (empty for none) |
| `protocol` | optional: `chat` (default, OpenAI `/v1/chat/completions`), `responses` (OpenAI `/v1/responses`) or `anthropic` (`/v1/messages`). `api_base` stays the `/v1` URL; for `anthropic` the runner strips `/v1`. Same benchmark, different inspect provider: `responses` sets `responses_store=true`, `anthropic` adds the `anthropic` package and inspect sends no `temperature`, so scores across protocols are not strictly like-for-like. Changing it later needs a new recipe id |
| `strict_tools` | optional, default `true`. With `protocol = "chat"` inspect marks every tool `"strict": true`: local engines ignore it, but OpenAI's API then rejects BFCL's optional parameters (400). Set `false` for OpenAI-hosted models; it adds `-M strict_tools=false`. Only valid for `chat`; changing it later needs a new recipe id |
| `n_concurrent` | parallel trials; no more than the server's parallel streams |
| `[pricing]` | optional, USD per 1M tokens (`input`, `cached_input`, `output`) for cost columns |

A recipe's identity is its `id`, `model` and `api_base`, and runs pool by it. **If anything that can
change results changes** (weights revision, quantization, engine commit, flags that affect outputs),
**make a new recipe id**; don't edit the old one. Labels (`display_name`, `weights`, `hardware`,
`*_detail`) can be corrected at any time.

Reasoning effort, sampling temperature, context budget and timeouts are set by the benchmark, not the
recipe, so every recipe runs under the same conditions. Your endpoint must accept them: each bench's
README lists the request settings it sends and how long a single request may run; make sure no proxy or
load balancer in between cuts long requests.

## 2. Run it

Needs [uv](https://docs.astral.sh/uv/); each bench README lists its own tools. A bench runs through one of
two runners (`runner` in its `bench.toml`): **inspect** benches (e.g. BFCL-100) need nothing else, `run.py`
runs [inspect](https://inspect.aisi.org.uk/) with `uvx` in a throwaway environment pinned by the bench;
**Harbor** benches run each task in a container and need Docker (Colima, OrbStack or Docker Desktop) and
Harbor. For Harbor, keep the clone in a directory Docker can mount (Colima shares only your home directory
by default, not `/tmp`): Harbor copies the agent's files from the task container into `evidence/` and on to
the verifier.

```bash
uv run scripts/run.py <bench> recipes/<id>.toml --attempts 1   # one result: the cheapest useful run
uv run scripts/run.py <bench> recipes/<id>.toml                # the bench's target k
uv run scripts/run.py <bench> recipes/<id>.toml --resume runs/<bench>/<run>   # continue an interrupted run
uv run scripts/collect.py runs/<bench>/<run>                   # refresh raw metrics mid-run
```

`--n-concurrent N` lowers parallelism when the runner host has less room than the endpoint. Don't change
anything else: no edits to `benches/`, no other flags. When the runner finishes, `run.py` extracts the raw
metrics and packs the evidence automatically.

## 2b. Measure speed (optional, recommended)

```bash
uv run scripts/speed.py recipes/<id>.toml    # writes speed/<recipe id>/<date>.json
```

It sends a ~32,768-token prose prompt and streams 512 tokens, single stream, three times, each with a
fresh random prefix so nothing comes from the prefix cache, and records the medians:

- **pp** (prompt processing, prefill) = prompt tokens / time to first token, network latency included
- **tg** (token generation, decode) = generated tokens / time from first to last token; reasoning tokens count

Run it while the endpoint serves nothing else, from a host on the same network as the server if you
can. The leaderboard shows each recipe's average over its speed records as *PP 32K* and *TG 32K*, for
information only: speed never enters the Rating or the ranking. Speed
records are small JSON files committed directly with the PR, no evidence needed; CI checks that the
recipe still matches and the settings and medians are intact.

## 3. What gets committed, and what doesn't

The repo keeps **raw metrics** only. `runs/<bench>/<run>/` holds:

| File | |
|---|---|
| `run.json` | what ran: bench, recipe, attempts, runner and harness version, runner host, evidence size and sha256 |
| `recipe.toml`, `bench.toml` | copies as of the run |
| `<runner>-config-p1.json` | the generated Harbor config or inspect command (no secrets) |
| `trials.jsonl` | one line per trial (an inspect sample epoch, or a Harbor trial): reward, exception, task content digest, tokens, LLM and tool calls, largest prompt and reply, replies cut at `max_tokens`, time; Harbor trials add cost, parse errors, context summarizations and a setup / agent / LLM / verifier split |

**Evidence**, the runner's full output (inspect logs with every message and model call, or Harbor's agent
trajectories, terminal recordings and verifier logs), never goes into git. It sits in `evidence/<bench>/<run>/` (git-ignored) and is packed to
`evidence/<bench>/<run>.tar.gz`, with its size and sha256 recorded in `run.json`. An archive over 24 MB
is also split into `<run>.part01.gz`, `<run>.part02.gz`, … so it fits GitHub's 25 MB attachment limit
(`cat <run>.part*.gz > <run>.tar.gz` restores it). Evidence travels with the pull request (below).

Speed records `speed/<recipe id>/<date>.json` are committed as they are.

The leaderboard in README.md, `benches/<bench>/RESULTS.md` and the recipe pages
`benches/<bench>/recipes/<id>.md` are **generated after every merge**; don't commit them.

## 4. Open the pull request

A PR may carry one run or several, for one recipe or more, and speed records.

```bash
uv run scripts/check.py                          # the same checks CI runs
uv run scripts/summarize.py                      # optional preview; don't commit its output
git checkout -b run/<bench>/<recipe id>
git add runs/<bench>/<run>/ [speed/<id>/<date>.json] [recipes/<id>.toml]  # recipe only if new
git commit -m "<bench>: <recipe id> (k=N)" && git push
```

Fill in the PR template (score and evidence for each run), then **attach each run's evidence** to the
PR description or a comment: the `.tar.gz`, or all of its `.partNN.gz` pieces. Anyone checking a run
compares the attachment's sha256 with `run.json`.

CI (`check.py`) verifies each run: its `bench.toml` is identical to the current one, the recipe's
`id` / `model` / `api_base` still match, the runner config is exactly what `run.py` generates, the harness is
the benchmark's version, every trial used the pinned task content, every task has `attempts` trials,
and the run is finished with its evidence recorded. A maintainer reviews the evidence (spot-checking
trajectories) and merges. After the merge, `.github/workflows/publish.yml` re-validates `main`, rebuilds
the leaderboard and pages, and commits them.

## Benchmarks are frozen

A benchmark is everything in `benches/<bench>/bench.toml`: upstream dataset and version, the task list
with each task's content digest, harness and agent settings, context budget, result target. Once it has
results it never changes. Any change (new upstream version, tasks added, removed or edited, different
agent settings) makes a **new benchmark** with a new id, e.g. `<bench>-r2`, and every recipe
runs it from scratch. Scores are never carried over, not even for tasks that look identical, because a
different harness, agent setting or task digest changes what a score means. CI enforces this: a run's
copy of `bench.toml` must equal the current file, and every trial's task digest must match the pin.

The README leaderboard shows the benchmarks listed in [leaderboard.toml](leaderboard.toml), one score
column each.

- **Add a benchmark:** add `benches/<bench>/` (README + `bench.toml`), get the anchor runs on it (below),
  then list it in `leaderboard.toml`.
- **Replace a benchmark:** add the new one as above, then swap it for the old id in `leaderboard.toml`.
- **Retire a benchmark:** remove it from `leaderboard.toml`. Its column goes away, but its runs stay in
  the repo, its RESULTS.md keeps updating, and its trials still count in the rating.

## Rating and anchors

Recipes are ranked by a rating fitted by [scripts/rating.py](scripts/rating.py): P(pass) =
logistic(skill[recipe] − difficulty[bench, task]), fitted jointly on every trial of every benchmark,
retired ones included, and shown on the Elo scale (1000 + 174 × skill; +100 ≈ 64% vs 36% odds on the
same task). Each task has its own difficulty, so a recipe is credited for how hard the tasks it ran
were, and a legacy recipe that never ran a newer benchmark keeps a fair rating without reruns. The fit
is redone from scratch on every merge, so ratings drift a little as data arrives; per-benchmark scores
don't. *Coverage* shows how many current tasks a recipe has run; fewer means a wider interval.

The rating can compare two benchmarks only through recipes that ran both. **Anchors** provide that
link: a few recipes marked `anchor = true` run every benchmark to its target. A benchmark can join
`leaderboard.toml` (after the first) only once **at least two anchors** have finished runs on it that
pool to the target, and CI enforces this. Keep a pinned-weights local recipe among the anchors, since
an official API can change or disappear. Current anchors: the DeepSeek-V4.1-Flash official API and the
local EXL3 2.9bpw recipe.

The rating assumes one overall skill. A recipe that is strong on one benchmark and weak on another is
averaged, and the per-benchmark columns show the split; read partial coverage together with the
interval.

## Layout

```
benches/<bench>/README.md            what the bench tests, tasks, how to run
benches/<bench>/select.py            inspect benches: reproduces the task list and the task content digest
benches/<bench>/bench.toml           frozen definition (suite + task digests, agent, context, result target)
benches/<bench>/RESULTS.md           generated: per-task results and agent behaviour, all recipes
benches/<bench>/recipes/<id>.md      generated: one recipe's runs, pooled per-task results and behaviour
leaderboard.toml                     benches shown as README leaderboard columns
recipes/<model>@<recipe>.toml        one endpoint per file
runs/<bench>/<date>_<recipe>[_<n>]/  one run: run.json, recipe.toml, bench.toml, <runner>-config-p1.json, trials.jsonl
speed/<recipe id>/<date>[_<n>].json  one speed measurement (pp / tg at 32K context)
evidence/<bench>/<run>/ + .tar.gz    git-ignored; attached to the PR
scripts/run.py                       bench + recipe -> inspect or Harbor job -> runs/ + evidence/
scripts/collect.py                   evidence/ -> trials.jsonl; --pack archives (and splits) the evidence
scripts/summarize.py                 runs/ -> README leaderboard, RESULTS.md, recipe pages (CI, after merge)
scripts/rating.py                    the rating fit (Rasch / Bradley-Terry, no dependencies)
scripts/speed.py                     pp / tg speed at 32K context -> speed/
scripts/check.py                     PR validation (CI)
.github/workflows/                   check.yml on PRs, publish.yml after merge
```

# Contributing

Every result lands through a pull request, one run per PR, ours included. A run is one recipe on one
benchmark.

## 1. Write a recipe

One file per endpoint in [recipes/](recipes/), named `<model>@<recipe>.toml`. Copy an existing one:

| Field | |
|---|---|
| `role` | `local`, or `reference` for an official API (tagged *API reference* on the leaderboard) |
| `model_family` | ties a recipe to its API reference, e.g. `DeepSeek-V4.1-Flash-1017` |
| `anchor` | `true` for the few recipes committed to running every benchmark (see below) |
| `display_name` | leaderboard name: model · engine, e.g. `DeepSeek-V4.1-Flash · TensorFold TP2` |
| `weights`, `hardware` | short leaderboard labels, e.g. `EXL3 2.9bpw`, `2× DGX Spark` |
| `weights_detail`, `serving`, `hardware_detail` | enough to rebuild it: repo @ revision, quant, engine @ commit, flags that matter |
| `model`, `api_base`, `api_key_env` | any OpenAI-compatible endpoint: `model = "openai/<served name>"` |
| `n_concurrent` | parallel trials; no more than the server's parallel streams |

## 2. Run it

Needs Docker (Colima, OrbStack or Docker Desktop) and [uv](https://docs.astral.sh/uv/); each bench
README lists its own tools.

```bash
uv run scripts/run.py <bench> recipes/<id>.toml --attempts 1   # one attempt per task: cheapest useful run
uv run scripts/run.py <bench> recipes/<id>.toml                # the bench's attempts (k=3)
uv run scripts/run.py <bench> recipes/<id>.toml --resume runs/<bench>/<run>   # continue an interrupted run
uv run scripts/collect.py runs/<bench>/<run>                   # refresh raw metrics mid-run
uv run scripts/summarize.py                                    # preview the generated tables
uv run scripts/check.py                                        # what CI checks on a PR
```

`--n-concurrent N` lowers parallelism when the runner host has less room than the endpoint.

**Any k is welcome; runs pool.** One *result* is one complete pass over the benchmark (one attempt per
task); `--attempts N` produces N results in one run. Every run is a standalone contribution, a PR may
carry one or several, and all runs of the same recipe on the same benchmark are pooled into one score,
with k = the total number of results. A single result (k=1) is a fine PR: the leaderboard shows the score flagged *k=1/3* until the pooled results reach the
benchmark's 3, and keeps tightening past that as more runs arrive. To add attempts to a recipe someone
else ran, run the same recipe file against an endpoint serving it the same way and open a PR with your
new run; you never edit other people's runs. Pooling is safe because CI requires every run to use the
same frozen benchmark, the same recipe `id` / `model` / `api_base` and the benchmark's Harbor version;
if the serving setup changes (engine commit, flags, weights), make a new recipe id instead. Each
recipe's page lists its runs with their own scores, so one that disagrees with the rest stands out.

## 3. What gets committed

The repo keeps **raw metrics** only: `runs/<bench>/<run>/trials.jsonl`, one line per trial with the
reward, exception, tokens (input / cached / output / estimated reasoning), cost, LLM calls, tool calls,
largest prompt and reply, parse errors, replies cut at `max_tokens`, context summarizations, and time
split into setup / agent / LLM / verifier. That is enough to rank recipes and to spot bugs or model
quirks (a recipe that keeps hitting the reply cap, fails to parse, or never declares completion).
Each recipe's generated page (`benches/<bench>/recipes/<id>.md`) breaks these down per task.

**Evidence** (Harbor's full output: agent trajectories with reasoning, terminal recordings, verifier
logs) stays out of git. `run.py` writes it to `evidence/<bench>/<run>/` (git-ignored) and packs it to
`evidence/<bench>/<run>.tar.gz`, recording size and sha256 in `run.json`. The archive goes in the PR.

## 4. Open the pull request

A PR carries raw data only: the recipe file (if new) and `runs/<bench>/<run>/` (`run.json`,
`recipe.toml`, `bench.toml`, `harbor-config-p1.json`, `trials.jsonl`). Don't commit the README leaderboard,
`benches/<bench>/RESULTS.md` or recipe pages; the publish workflow rebuilds them after the merge, so
concurrent PRs never conflict.

```bash
uv run scripts/check.py        # the same checks CI runs on the PR
uv run scripts/summarize.py    # optional local preview; don't commit its output
git checkout -b run/<bench>/<recipe id>
git add recipes/<id>.toml runs/<bench>/<run>/
```

Open the PR (the template asks for the score and the evidence), then attach
`evidence/<bench>/<run>.tar.gz` to the description or a comment. CI runs `check.py`: the benchmark is
unchanged, the recipe still matches, the Harbor config is exactly what `run.py` generates, every trial
used the pinned task content, every task ran `attempts` times, and the run is finished with its
evidence archive recorded.
Reviewers compare the attached archive's sha256 with `run.json` and spot-check trajectories. After the
merge, `.github/workflows/publish.yml` validates `main`, regenerates the tables and commits them.

## Benchmarks are frozen

A benchmark is everything in `benches/<bench>/bench.toml`: upstream dataset and version, the task list
with each task's content digest, harness and agent settings, context budget, attempts. Once it has
results it never changes. Any change (new upstream version, tasks added, removed or edited, different
agent settings) makes a **new benchmark** with a new id, e.g. `terminal-bench-4-r2`, and every recipe
runs it from scratch. Scores are never carried over, not even for tasks that look identical, because a
different harness, agent setting or task digest changes what a score means. CI enforces this: a run's
copy of `bench.toml` must equal the current file, and every trial's task digest must match the pin.

The README leaderboard shows the benchmarks listed in [leaderboard.toml](leaderboard.toml), one
score column each.

- **Replace a benchmark:** add the new one and get the anchor runs on it (below), then swap it for the
  old id in `leaderboard.toml`.
- **Add a benchmark:** add `benches/<bench>/` (README + `bench.toml`), run the anchors, list it in
  `leaderboard.toml`.
- **Retire a benchmark:** remove it from `leaderboard.toml`. Its column goes away, but its runs stay in
  the repo, its `benches/<bench>/RESULTS.md` keeps updating, and its trials still count in the rating.

## Rating and anchors

Recipes are ranked by a rating fitted by [scripts/rating.py](scripts/rating.py): P(pass) =
logistic(skill[recipe] − difficulty[bench, task]), fitted jointly on every trial of every benchmark
(retired included), shown on the Elo scale (1000 + 174 × skill; +100 ≈ 64% vs 36% odds on the same
task). Each task has its own difficulty, so a recipe is credited for how hard the tasks it ran were,
and a legacy recipe that never ran a newer benchmark keeps a fair rating without reruns. The fit is
redone from scratch on every merge, so ratings drift a little as data arrives; per-benchmark scores
don't.

The rating can compare two benchmarks only through recipes that ran both. **Anchors** provide that
link: a few recipes marked `anchor = true` run every benchmark in full. A benchmark can join
`leaderboard.toml` (after the first) only once **at least two anchors** have finished runs on it
pooling to the benchmark's attempts (k=3), and CI enforces this. Keep a pinned-weights local recipe among the anchors, since an official API can change
or disappear. Current anchors: the DeepSeek-V4.1-Flash official API and the local EXL3 2.9bpw recipe.

The rating assumes one overall skill. A recipe that is strong on one benchmark and weak on another is
averaged, and the per-benchmark columns show the split; read partial coverage together with the
interval.

## Layout

```
benches/<bench>/README.md          what the bench tests, tasks, how to run
benches/<bench>/bench.toml         frozen definition (suite + task digests, agent, context, attempts)
benches/<bench>/RESULTS.md         generated: per-task results and agent behaviour, all recipes
benches/<bench>/recipes/<id>.md    generated: one recipe's pooled runs, per-task results and behaviour
leaderboard.toml                   benches shown as README leaderboard columns
recipes/<model>@<recipe>.toml      one endpoint per file
scripts/run.py                     bench + recipe -> Harbor job -> runs/ + evidence/
scripts/collect.py                 evidence/ -> runs/<bench>/<run>/trials.jsonl; --pack archives evidence
scripts/summarize.py               runs/ -> README leaderboard, benches/*/RESULTS.md, recipe pages (CI, after merge)
scripts/rating.py                  the rating fit (Rasch / Bradley-Terry, no dependencies)
scripts/check.py                   PR validation (CI)
.github/workflows/                 check.yml on PRs, publish.yml after merge
runs/<bench>/<date>_<recipe>[_<n>]/      in git
  run.json                         what ran: bench, recipe, attempts, Harbor version, host, evidence sha256
  recipe.toml, bench.toml          copies as of the run
  harbor-config-p1.json            generated Harbor config (no secrets)
  trials.jsonl                     raw metrics, one trial per line
evidence/<bench>/<run>/ + .tar.gz  git-ignored; attached to the run's PR
```

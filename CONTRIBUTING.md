# Contributing

Every result lands through a pull request, one run per PR, ours included. A run is one recipe on one
bench under that bench's current protocol.

## 1. Write a recipe

One file per endpoint in [recipes/](recipes/), named `<model>@<recipe>.toml`. Copy an existing one:

| Field | |
|---|---|
| `role` | `local` (competes) or `reference` (official API, a reference line) |
| `model_family` | ties a recipe to its API reference, e.g. `DeepSeek-V4.1-Flash-1017` |
| `display_name` | leaderboard name: model · engine, e.g. `DeepSeek-V4.1-Flash · TensorFold TP2` |
| `weights`, `hardware` | short leaderboard labels, e.g. `EXL3 2.9bpw`, `2× DGX Spark` |
| `weights_detail`, `serving`, `hardware_detail` | enough to rebuild it: repo @ revision, quant, engine @ commit, flags that matter |
| `model`, `api_base`, `api_key_env` | any OpenAI-compatible endpoint: `model = "openai/<served name>"` |
| `n_concurrent` | parallel trials; no more than the server's parallel streams |

## 2. Run it

Needs Docker (Colima, OrbStack or Docker Desktop) and [uv](https://docs.astral.sh/uv/); each bench
README lists its own tools.

```bash
uv run scripts/run.py <bench> recipes/<id>.toml --attempts 1   # smoke
uv run scripts/run.py <bench> recipes/<id>.toml                # full, protocol attempts
uv run scripts/run.py <bench> recipes/<id>.toml --resume runs/<bench>/<run dir>
uv run scripts/collect.py runs/<bench>/<run dir>               # refresh raw metrics mid-run
uv run scripts/summarize.py                                    # rebuild leaderboard, RESULTS.md, STATS.md
uv run scripts/check.py                                        # what CI checks on a PR
```

`--n-concurrent N` lowers parallelism when the runner host has less room than the endpoint.

## 3. What gets committed

The repo keeps **raw metrics** only: `runs/<bench>/<run>/trials.jsonl`, one line per trial with the
reward, exception, tokens (input / cached / output / estimated reasoning), cost, LLM calls, tool calls,
largest prompt and reply, parse errors, replies cut at `max_tokens`, context summarizations, and time
split into setup / agent / LLM / verifier. That is enough to rank recipes and to spot bugs or model
quirks (a recipe that keeps hitting the reply cap, fails to parse, or never declares completion).
Each run's `STATS.md` breaks these down per task.

**Evidence** (Harbor's full output: agent trajectories with reasoning, terminal recordings, verifier
logs) stays out of git. `run.py` writes it to `evidence/<bench>/<run>/` (git-ignored) and packs it to
`evidence/<bench>/<run>.tar.gz`, recording size and sha256 in `run.json`. The archive goes in the PR.

## 4. Open the pull request

```bash
uv run scripts/summarize.py    # README leaderboard, RESULTS.md, runs/<bench>/<run>/STATS.md
uv run scripts/check.py        # the same checks CI runs
git checkout -b run/<bench>/<recipe id>
git add recipes/<id>.toml runs/<bench>/<run>/ README.md RESULTS.md
```

Open the PR (the template asks for the score and the evidence), then attach
`evidence/<bench>/<run>.tar.gz` to the description or a comment. CI runs `check.py`: the protocol and
recipe copies match the repo, the Harbor config is exactly what `run.py` generates, every task ran
`attempts` times, the evidence archive is recorded, and generated files are fresh. Reviewers compare the
attached archive's sha256 with `run.json` and spot-check trajectories.

## Benchmarks and protocols

The README leaderboard has one column per active benchmark, showing results under that benchmark's
latest protocol.

- **Change a benchmark's settings:** add `protocol-v<N+1>.toml`; never edit one that has results. The
  column switches to v<N+1>, and recipes reappear in it once rerun. Older results stay in RESULTS.md.
- **Add a benchmark:** add `benches/<bench>/` with a README and `protocol-v1.toml`; it becomes a new
  column. *Overall* then needs a result on it too.
- **Drop a benchmark:** set `retired = true` in its latest protocol. The column goes away; its results
  stay in RESULTS.md.

## Layout

```
benches/<bench>/README.md          what the bench tests, tasks, how to run
benches/<bench>/protocol-v<N>.toml fixed settings (suite, agent, context, attempts)
recipes/<model>@<recipe>.toml      one endpoint per file
scripts/run.py                     bench protocol + recipe -> Harbor job -> runs/ + evidence/
scripts/collect.py                 evidence/ -> runs/<bench>/<run>/trials.jsonl; --pack archives evidence
scripts/summarize.py               runs/ -> README leaderboard, RESULTS.md, runs/<bench>/<run>/STATS.md
scripts/check.py                   PR validation (CI)
runs/<bench>/<date>_<protocol>_<recipe>[_k<n>]/      in git
  run.json                         what ran: protocol, recipe, Harbor version, runner host, evidence sha256
  recipe.toml, protocol.toml       copies as of the run
  harbor-config.json               generated Harbor config (no secrets)
  trials.jsonl                     raw metrics, one trial per line
  STATS.md                         generated per-task breakdown
evidence/<bench>/<run>/ + .tar.gz  git-ignored; attached to the PR
```

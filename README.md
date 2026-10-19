# localmaxxing-arena

Finding the best LLM we can run at home. Local recipes (model × quantized weights × engine × hardware)
compete on the same benchmarks under fixed protocols. Each model's official API is run too, as the
reference line: it shows how much a recipe gives up, or doesn't.

**Leaderboards: [RESULTS.md](RESULTS.md)**

## Benchmarks

| Bench | What it measures | Protocol |
|---|---|---|
| [terminal-bench-4](benches/terminal-bench-4/) | Agentic terminal work: 10 Terminal-Bench 4.0.0 tasks with Terminus-2, max reasoning | [v1](benches/terminal-bench-4/protocol-v1.toml) |

## Recipes

One file per endpoint in [recipes/](recipes/), named `<model>@<recipe>.toml`:

| Field | |
|---|---|
| `role` | `local` (competes) or `reference` (official API, a reference line) |
| `model_family` | ties a recipe to its API reference, e.g. `DeepSeek-V4.1-Flash-1017` |
| `weights`, `serving`, `hardware` | enough to rebuild it: repo @ revision, quant, engine @ commit, flags that matter |
| `model`, `api_base`, `api_key_env` | any OpenAI-compatible endpoint: `model = "openai/<served name>"` |
| `n_concurrent` | parallel trials; no more than the server's parallel streams |

## Running

Needs Docker (Colima, OrbStack or Docker Desktop) and [uv](https://docs.astral.sh/uv/); each bench
README lists its own tools.

```bash
uv run scripts/run.py <bench> recipes/<id>.toml --attempts 1   # smoke
uv run scripts/run.py <bench> recipes/<id>.toml                # full, protocol attempts
uv run scripts/run.py <bench> recipes/<id>.toml --resume runs/<bench>/<run dir>
uv run scripts/collect.py runs/<bench>/<run dir>               # refresh raw metrics mid-run
uv run scripts/summarize.py                                    # rebuild RESULTS.md and STATS.md
uv run scripts/check.py                                        # what CI checks on a PR
```

`--n-concurrent N` lowers parallelism when the runner host has less room than the endpoint.

## Raw metrics and evidence

The repo keeps **raw metrics** only: `runs/<bench>/<run>/trials.jsonl`, one line per trial with the
reward, exception, tokens (input / cached / output / estimated reasoning), cost, LLM calls, tool calls,
largest prompt and reply, parse errors, replies cut at `max_tokens`, context summarizations, and time
split into setup / agent / LLM / verifier. That is enough to rank recipes and to spot bugs or model
quirks (a recipe that keeps hitting the reply cap, fails to parse, or never declares completion).
Each run's `STATS.md` breaks these down per task.

**Evidence** (Harbor's full output: agent trajectories with reasoning, terminal recordings, verifier
logs) stays out of git. `run.py` writes it to `evidence/<bench>/<run>/` (git-ignored) and packs it to
`evidence/<bench>/<run>.tar.gz`, recording size and sha256 in `run.json`. The archive goes in the PR.

## Adding a result

Every new run lands through a pull request, including ours:

1. New recipe: add `recipes/<model>@<recipe>.toml` (copy an existing one).
2. Run it unchanged under the bench's current protocol: `uv run scripts/run.py <bench> recipes/<id>.toml`.
3. `uv run scripts/summarize.py && uv run scripts/check.py`.
4. Open a PR with the recipe, `runs/<bench>/<run>/` and the regenerated `RESULTS.md` / `STATS.md`.
   Attach the evidence archive to the PR description or a comment (sha256 must match `run.json`).
   CI runs `check.py`: protocol and recipe copies match, generated Harbor config matches, every task
   ran `attempts` times, evidence recorded, generated files fresh.

Protocols don't change in place: a new setting means a new `protocol-v<N>.toml`, and its results form a
separate leaderboard.

## Layout

```
benches/<bench>/README.md          what the bench tests, tasks, how to run
benches/<bench>/protocol-v<N>.toml fixed settings (suite, agent, context, attempts)
recipes/<model>@<recipe>.toml      one endpoint per file
scripts/run.py                     bench protocol + recipe -> Harbor job -> runs/ + evidence/
scripts/collect.py                 evidence/ -> runs/<bench>/<run>/trials.jsonl; --pack archives evidence
scripts/summarize.py               runs/ -> RESULTS.md, runs/<bench>/<run>/STATS.md
scripts/check.py                   PR validation (CI)
runs/<bench>/<date>_<protocol>_<recipe>[_k<n>]/      in git
  run.json                         what ran: protocol, recipe, Harbor version, runner host, evidence sha256
  recipe.toml, protocol.toml       copies as of the run
  harbor-config.json               generated Harbor config (no secrets)
  trials.jsonl                     raw metrics, one trial per line
  STATS.md                         generated per-task breakdown
evidence/<bench>/<run>/ + .tar.gz  git-ignored; attached to the PR
```

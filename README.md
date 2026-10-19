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
uv run scripts/summarize.py                                    # rebuild RESULTS.md
```

`--n-concurrent N` lowers parallelism when the runner host has less room than the endpoint.

## Adding a recipe

1. Add `recipes/<model>@<recipe>.toml` (copy an existing one).
2. Run it unchanged under the bench's current protocol, then `scripts/summarize.py`.
3. Commit the recipe, `runs/<bench>/<run dir>/` and the regenerated `RESULTS.md`.

Protocols don't change in place: a new setting means a new `protocol-v<N>.toml`, and its results form a
separate leaderboard.

## Layout

```
benches/<bench>/README.md          what the bench tests, tasks, how to run
benches/<bench>/protocol-v<N>.toml fixed settings (suite, agent, context, attempts)
recipes/<model>@<recipe>.toml      one endpoint per file
scripts/run.py                     bench protocol + recipe -> Harbor job -> runs/
scripts/summarize.py               runs/ -> RESULTS.md
runs/<bench>/<date>_<protocol>_<recipe>[_k<n>]/
  run.json                         what ran: protocol, recipe, Harbor version, runner host
  recipe.toml, protocol.toml       copies as of the run
  harbor-config.json, harbor.log   generated config (no secrets) and runner log
  jobs/<job>/<trial>/              Harbor output: result.json, agent trajectory, verifier logs
```

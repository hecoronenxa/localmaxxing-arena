# BFCL-100

Multi-turn tool use. 100 tasks from the [Berkeley Function Calling Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html)
v3 multi-turn set, run with [inspect_evals](https://github.com/UKGovernmentBEIS/inspect_evals)' `bfcl` task.

In each task the model works through a multi-turn conversation against simulated backends (file system,
trading bot, travel booking, messaging, vehicle control, ticketing) using the endpoint's native tool calling.
A task passes when the backends' final state and the model's answers match the ground truth. Plain
`multi_turn_base` is left out (strong models are near the ceiling on it); the four harder categories get 25
tasks each:

| Category | What it adds |
|---|---|
| `multi_turn_miss_func` | the needed function is missing at first: the model must say so instead of faking it with another tool |
| `multi_turn_miss_param` | the request is underspecified: the model must ask instead of guessing a value |
| `multi_turn_long_context` | long, noisy tool outputs |
| `multi_turn_composite` | all three at once |

Because the tools go through the endpoint's tool-call parser, this also tests a recipe's serving layer
(chat template, tool-call parsing), not only the weights.

## Settings

Frozen in [bench.toml](bench.toml): inspect-ai 0.3.281, inspect_evals @ `8ef810f` (which pins the upstream
data, gorilla @ `dac44e7`), `reasoning_effort: low`, `temperature: 1.0`, at most 16,384 output tokens and
30 minutes per model call, 1 attempt per task. A sample that errors scores 0.

[select.py](select.py) reproduces the task list (25 per category, seeded) and the per-task content digests
CI checks every run against.

## Running

Needs only [uv](https://docs.astral.sh/uv/); no Docker. `scripts/run.py` runs inspect with `uvx` in a
throwaway environment pinned to the requirements above.

```bash
uv run scripts/run.py bfcl-100 recipes/<id>.toml
```

Measured on the local DeepSeek-V4.1-Flash recipe (2× DGX Spark, 4 parallel streams): a task takes 1–7
minutes (6–16 model calls, 1–7K output tokens at low effort), so a full run is roughly 2–3 hours.

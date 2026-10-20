# SciCode-10

Scientific computing, graded numerically. Each task is a Python module implementing a chain of 7–15
functions (Schrödinger solvers, Ewald summation, band structure, …). The verifier imports the functions
step by step and compares arrays against HDF5 targets, so a task scores the **fraction of sub-steps that
pass** (`sub_step_correct`); `problem_correct` is 1 only when every step passes.

Frozen definition: [bench.toml](bench.toml). Any change to the tasks, pins or agent settings needs a new
bench id. The bench is not yet a leaderboard column: add it to `leaderboard.toml` once two anchor recipes
(one local, one official API) have finished runs.

## Tasks

The 10 hardest problems of SciCode's test split. SciCode has 80 problems: 65 test and 15 dev; the dev
problems ship public reference solutions, so they are excluded to keep the bench uncontaminated. Of the
remaining 65, `select.py` takes the ones with the most sub-steps (ties: lower problem id), which lands on
7–15 steps:

| Task | Sub-steps | Task | Sub-steps |
|---|---|---|---|
| scicode-13 | 15 | scicode-72 | 9 |
| scicode-12 | 14 | scicode-73 | 9 |
| scicode-11 | 12 | scicode-68 | 8 |
| scicode-77 | 12 | scicode-69 | 8 |
| scicode-71 | 9 | scicode-80 | 7 |

The task directories are vendored in `dataset/` (60–96 KB each, ~740 KB total) and pinned by the
`sha256` digests in `bench.toml`. The environment is frozen too: the Dockerfile pins the base image digest
and numpy/scipy/matplotlib/sympy/h5py versions, so a package release cannot silently change scores.
Reproduce the selection with the Harbor adapter and `select.py`:

```bash
git clone https://github.com/harbor-framework/harbor ~/dev/harbor
git -C ~/dev/harbor checkout 77d2f15
(cd ~/dev/harbor/adapters/scicode && uv run --extra huggingface python -m scicode.main)  # writes datasets/scicode
uv run benches/scicode-10/select.py --upstream ~/dev/harbor/datasets/scicode
```

The adapter needs `test_data.h5` (see Setup) and the three pre-written SciCode files it fetches from
GitHub; the adapter README covers both.

## Setup

1. **Harbor 0.24.0** and Docker (Colima is fine). The bench runs `harbor run -c <config>`.
2. **SciCode ground truth** (`test_data.h5`, 1,049,345,865 bytes, md5 `96d5d815aee54434deba01eb27646f22`).
   The Google Drive copy the adapter points at is usually quota-limited; the Hugging Face mirror works:

   ```bash
   mkdir -p ~/.cache/harbor/scicode
   curl -L --retry 3 -o ~/.cache/harbor/scicode/test_data.h5 \
     "https://hf-mirror.com/datasets/Srimadh/Scicode-test-data-h5/resolve/main/test_data.h5"
   md5 -q ~/.cache/harbor/scicode/test_data.h5   # 96d5d815aee54434deba01eb27646f22
   ```

   Every task's `docker-compose.yaml` mounts it read-only at `/app/test_data.h5` from
   `$HARBOR_SCICODE_TEST_DATA_PATH`, so export that (absolute path) before running.

## Running

```bash
export HARBOR_SCICODE_TEST_DATA_PATH=$HOME/.cache/harbor/scicode/test_data.h5
uv run scripts/run.py scicode-10 recipes/<id>.toml          # n_attempts = 1
```

The bench is a single attempt per task (`n_attempts = 1`). The agent timeout in each `task.toml` is 30 min;
`bench.toml` multiplies it by 6 (3 h) because a 14-step problem took 35 min with `pi`. The verifier gets
60 min so one slow sub-step cannot consume the budget and turn a partial score into 0.

A recipe for a local endpoint:

```toml
id = "deepseek-v4.1-flash@exl3-eaq-2.9bpw-tensorfold-zig-2xspark"
model = "openai/DeepSeek-V4.1-Flash-1017"
api_base = "http://192.168.0.112:8888/v1"
provider = "local"
n_concurrent = 4
```

## Agents: pick the scaffold for the model

`bench.toml` maps each recipe's `provider` to a scaffold (versions pinned in `[agent.versions]`). This
mapping is part of the frozen bench, and `run.json` records which agent produced a result.

| Recipe `provider` | Scaffold | Use it for |
|---|---|---|
| `local` (default) | **pi** 1.3.0, `thinking=low` | local weights on any OpenAI-compatible endpoint |
| `openai` | **codex** 0.163.0 | OpenAI models via a Codex subscription (`CODEX_FORCE_AUTH_JSON=1` + auth.json) or the official API. Recipe `model` must be `openai/<name>` |
| `anthropic` | **claude-code** 2.1.304 | Claude models via a Claude subscription (`CLAUDE_FORCE_OAUTH=1` + `CLAUDE_CODE_OAUTH_TOKEN`) or the official API. Recipe `model` must be `anthropic/<name>` |

Why these three:

- **pi** is a file-oriented coding agent. With a free-form bash scaffold (`terminus-2`) the same local model
  spent 3 h exploring numerically and never wrote `/app/solution.py`; with `pi` the identical problems were
  solved in 15 and 35 min. Use `pi` for local recipes.
- **codex** and **claude-code** are the right scaffolds for subscription/official API models: they bring the
  provider's own tool loop, and Harbor installs and authenticates them natively. Note that Codex's ACP mode
  does **not** support `auth.json`, so keep the default mode.

Scores from different scaffolds answer the same tasks but are **not strictly comparable**: a Codex result
and a `pi` result are different agent configurations. Record the agent in the run metadata and compare
within a provider.

`scripts/run.py` sets `OPENAI_BASE_URL` from the recipe's `api_base` for `pi`, and for `codex` only when
the recipe names an `api_key_env` (official API); a subscription recipe keeps Codex's own backend. For
`claude-code` it sets `ANTHROPIC_BASE_URL` and, when the recipe names an `api_key_env`, `ANTHROPIC_API_KEY`.
Subscription recipes leave `api_key_env` empty and rely on the agent's own auth.

## Time

Measured on a 2× DGX Spark DeepSeek-V4.1-Flash EXL3 2.9 bpw endpoint (4 concurrent, `thinking=low`):

| Task | Sub-steps | Steps taken | Output tokens | Wall clock |
|---|---|---|---|---|
| scicode-10 (dev, smoke) | 11 | 42 | 30.5K | 15m27s |
| scicode-12 | 14 | 99 | 80.9K | 34m48s |

A 1-step problem solved in under 2 minutes. At `n_concurrent = 4` the 10-task bench is roughly **1–2 h**
wall clock when tasks converge. Worst case: 10 tasks in 3 rounds at 4 concurrent, each hitting the 3 h
agent timeout, is ~9 h plus verifier time — budget accordingly and prefer a model that writes the file
early.

## Caveats

- **No oracle solutions.** SciCode's test split has no public reference code, so `oracle` scores 0 on these
  tasks by design; the bench cannot be validated with the oracle agent. The verifier itself is exercised by
  every run (it prints each step's PASS/FAIL).
- **Difficulty.** These are the hardest SciCode problems; frontier models score roughly 50–67% **sub-problem
  accuracy** on the full SciCode set, and a weak local model will land near 0 on multi-step problems. The
  fractional reward keeps partial progress visible.
- **Vendored tasks.** The dataset lives in `dataset/` so the bench is self-contained; `test_data.h5` stays
  outside git and is fetched once per host.

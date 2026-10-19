# localmaxxing-arena

Finding the best LLM we can run at home. Local recipes (model × quantized weights × engine × hardware)
compete on the same benchmarks under fixed protocols. Each model's official API runs too, as a reference
line (`ref`): it shows how much a recipe gives up, or doesn't.

## Leaderboard

Recipe names link to the recipe (weights, engine, flags, hardware); scores link to the run's per-task
stats. Per-task results and agent behaviour (tokens, tool calls, parse errors, cut-off replies) are in
[RESULTS.md](RESULTS.md).

<!-- leaderboard:start -->
_No results yet._
<!-- leaderboard:end -->

## Benchmarks

| Bench | What it measures | Protocol |
|---|---|---|
| [terminal-bench-4](benches/terminal-bench-4/) | Agentic terminal work: 10 Terminal-Bench 4.0.0 tasks with Terminus-2, max reasoning | [v1](benches/terminal-bench-4/protocol-v1.toml) |

## Contributing

Ran a model on your own hardware? Add it. [CONTRIBUTING.md](CONTRIBUTING.md) covers writing a recipe,
running a bench, and submitting the result as a pull request.

# localmaxxing-arena

Finding the best LLM we can run at home. Local recipes (model × quantized weights × engine × hardware)
compete on the same frozen benchmarks. Each model's official API runs too, as a reference line: it shows
how much a recipe gives up, or doesn't.

## Leaderboard

One row per recipe and one column per benchmark. *Overall* is the mean over all benchmarks, shown once a recipe has run on every one of them. Recipe names
link to the recipe (exact weights, engine, flags, hardware), and scores link to the run's per-task stats.
Rows marked *API reference* are official APIs. Each benchmark header links to its per-task results and
agent behaviour (tokens, tool calls, parse errors, cut-off replies). The table is rebuilt automatically
after every merge.

<!-- leaderboard:start -->
_No results yet._
<!-- leaderboard:end -->

## Benchmarks

| Bench | What it measures | Definition | Results |
|---|---|---|---|
| [terminal-bench-4](benches/terminal-bench-4/) | Agentic terminal work: 10 Terminal-Bench 4.0.0 tasks with Terminus-2, max reasoning | [bench.toml](benches/terminal-bench-4/bench.toml) | [RESULTS.md](benches/terminal-bench-4/RESULTS.md) |

## Contributing

Ran a model on your own hardware? Add it. [CONTRIBUTING.md](CONTRIBUTING.md) covers writing a recipe,
running a bench, and submitting the result as a pull request.

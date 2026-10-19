# localmaxxing-arena

Finding the best LLM we can run at home. Local recipes (model × quantized weights × engine × hardware)
compete on the same frozen benchmarks. Each model's official API runs too, as a reference line: it shows
how much a recipe gives up, or doesn't.

## Leaderboard

One row per recipe, one score column per current benchmark, ranked by **Rating**: an Elo-scale
rating fitted on every trial of every benchmark, retired ones included, so recipes that ran only some
benchmarks still rank fairly against the rest. *Coverage* is how many current tasks a recipe has run;
fewer tasks means a wider interval. Recipe names link to the recipe (exact weights, engine, flags,
hardware), scores to the run's per-task stats, and benchmark headers to per-task results and agent
behaviour. *API reference* rows are official APIs; *anchor* recipes run every benchmark to keep ratings
comparable across them. The table is rebuilt automatically after every merge.

<!-- leaderboard:start -->
<!-- leaderboard:end -->

## Benchmarks

| Bench | What it measures | Definition | Results |
|---|---|---|---|
| [terminal-bench-4](benches/terminal-bench-4/) | Agentic terminal work: 10 Terminal-Bench 4.0.0 tasks with Terminus-2, max reasoning | [bench.toml](benches/terminal-bench-4/bench.toml) | [RESULTS.md](benches/terminal-bench-4/RESULTS.md) |

## Contributing

Ran a model on your own hardware? Add it. [CONTRIBUTING.md](CONTRIBUTING.md) covers writing a recipe,
running a bench, and submitting the result as a pull request.

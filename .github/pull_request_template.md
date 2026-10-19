<!-- Title: "<bench>: <recipe id> (k=N)". A PR may carry one run or several; see CONTRIBUTING.md. -->

## Runs

<!-- One row per run directory added by this PR. Score and k are printed by `uv run scripts/summarize.py`. -->

| Run (`runs/<bench>/<run>/`) | Recipe | k (results) | Score | Evidence | sha256 |
|---|---|---|---|---|---|
| | | | | <!-- attachment link(s) --> | <!-- shasum -a 256 evidence/<bench>/<run>.tar.gz --> |

- New recipe file? <!-- yes: recipes/<id>.toml / no -->

## Evidence

Attach each run's `evidence/<bench>/<run>.tar.gz` (or all of its `<run>.partNN.gz` pieces if it was
split) to this description or a comment. Its sha256 must match `evidence.sha256` in the run's
`run.json`.

## Checklist

- [ ] Each run was made with `uv run scripts/run.py <bench> recipes/<id>.toml [--attempts N]`, nothing else changed (no edits to `benches/`)
- [ ] The endpoint served the recipe exactly as its file describes (weights revision, engine commit, flags, hardware)
- [ ] New recipe: a new `id`, with `weights_detail`, `serving` and `hardware_detail` complete enough to rebuild it
- [ ] `uv run scripts/check.py` passes locally
- [ ] Only raw data committed (`runs/<bench>/<run>/`, plus the recipe file if new); no README, RESULTS.md or recipe pages
- [ ] Evidence attached for every run
- [ ] Anything unusual (server restarts, errors, retries, other load on the endpoint) is described below

## Notes

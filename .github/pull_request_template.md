<!-- One PR per run or extension. Title: "<bench>: <recipe id> (k=N)" or "<bench>: extend <recipe id> to k=N" -->

## Run

- Bench:
- Recipe: `recipes/<id>.toml` (new / existing)
- Score:
- Run dir: `runs/<bench>/<run>/`

## Evidence

Attach the new part's `evidence/<bench>/<job>.tar.gz` to this PR (drag it into this description or a comment; GitHub
takes files up to 25 MB, split larger archives or link to them). Its sha256 must match the part's
`evidence.sha256` in `run.json`.

- Archive: <!-- attachment or link -->
- sha256: <!-- shasum -a 256 evidence/<bench>/<job>.tar.gz -->

## Checklist

- [ ] Ran `uv run scripts/run.py <bench> recipes/<id>.toml` unchanged (no edits to `benches/`)
- [ ] `uv run scripts/check.py` passes locally
- [ ] Only raw data committed (recipe, `runs/<bench>/<run>/`); generated tables are rebuilt after merge
- [ ] Recipe file names exact weights (repo @ revision), engine @ commit, launch flags and hardware
- [ ] Anything unusual during the run (restarts, errors, server changes) is described below

## Notes

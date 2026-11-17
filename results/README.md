# results

The state of the inner loop, written by `run.py`.

- `state.json`: the iteration counter and a summary of every iteration; the planner reads it.
- `iter_NNN/`: one directory per iteration, always on the **dev** split.
  - `plan.json`: the hypothesis, method, parameters and target.
  - `literature.md`: notes from the literature search, with sources (iteration 1 only with `steps.literature: first_only`).
  - `execute.json`: the evaluator's metrics, per-image errors, errors per corruption level, or the error the method raised.
  - `outputs/predictions.csv`: every grain the method found (pixels), one row per grain.
  - `outputs/overlays/*.jpg`: red grain contours on three dev images (low, middle, high corruption); the reviewer sees them.
  - `outputs/labels/`: label images, not committed (regenerate them by rerunning the method with the same params).
  - `verify.json`: the automatic checks and the reviewer's verdict.
  - `summary.json`: one line of the history, as the planner sees it.
  - `usage.json`: tokens used by each Claude call.
  - `run.log`: the log of the iteration.
- `baseline/<dataset>/`: the watershed method with default params on dev, run without the API
  (`uv run python -m src.execute --dataset <dataset> --out results/baseline/<dataset>`).
- `final.json`, `final_<dataset>/`: the one-time holdout exam, `run.py --final`, per dataset.
- `_dry_run/`: output of `run.py --dry-run`, not committed.

## Flakes (results/flakes/)

- `state.json`, `iter_NNN/`: the research loop on the flake datasets (`run.py --dataset lamellar_synth | Ti64 | Ti64_scratched`).
  A flake iteration may contain `outputs/search_trials.json` when the planner asked for a local search.
- `baseline*/`: the flakes method with its defaults at successive code versions (JOURNAL says what changed).
- `scratched_test/`: blind tests with fake scratches pasted on real Ti64 images (`src/scratch_paste.py`).
- `adapt/<set>/`: the adapt tool on one image set: `report.md` / `report.html`, `flakes.csv`, `image_summaries.csv`,
  `review/` (original next to drawing), `params.json`, `adapt_log.json` (profile, twin, all search trials, agent plans and reviews).
- `search_cache/trials.jsonl`: every configuration ever evaluated on a dataset with truth (keyed by params, code version and dataset); never evaluated twice.
- `knowledge.jsonl`: one entry per adapted image set (profile, mode, chosen params, twin and real-image scores); new sets warm-start from the nearest successful entry.
- `stress_test_summary.json`: the adapt tool on 12 very different image sets (`scripts/stress_test.py`).


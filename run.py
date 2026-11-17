#!/usr/bin/env python3
"""Single entry point of the research loop.

Each iteration runs plan -> literature -> execute -> verify -> record
and leaves its state in results/iter_NNN/ and results/state.json.

    uv run python run.py --dry-run   # no API calls, writes to results/_dry_run/
    uv run python run.py             # one live iteration
    uv run python run.py -n 5        # five live iterations
    uv run python run.py --final     # once, at the end: the best dev iteration on the holdout split
    uv run python run.py --dataset lamellar_synth -n 3   # flakes: shape and alignment; state in results/flakes/
"""

import argparse
import json
import logging
from datetime import datetime

from src import execute, literature, plan, record, verify
from src.config import load_config, target_for, task_of, use_dataset
from src.splits import DATASETS
from src.llm import Claude
from src.state import State, write_json

log = logging.getLogger("run")


def iterate(cfg: dict, claude: Claude, state: State, fixed_plan: dict | None = None) -> dict:
    it_dir = state.start_iteration()
    root = logging.getLogger()
    root.setLevel(min(root.level, logging.INFO))
    handler = logging.FileHandler(it_dir / "run.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    claude.usage = []
    try:
        log.info("iteration %d", state.iteration)
        if fixed_plan:
            p = plan.fixed(cfg, fixed_plan["method"], fixed_plan["params"], fixed_plan["hypothesis"], it_dir)
        else:
            p = plan.run(cfg, claude, state, it_dir)
        lit = literature.run(cfg, claude, p, it_dir, state.history)
        ex = execute.run(cfg, p, it_dir)
        v = verify.run(cfg, claude, p, lit, ex, it_dir)
        return record.run(cfg, state, it_dir, p, lit, ex, v, claude.usage)
    finally:
        root.removeHandler(handler)
        handler.close()


def run_loop(cfg: dict, n: int, dry_run: bool, fixed_plan: dict | None = None) -> list[dict]:
    state = State.load(cfg["paths"]["results"])
    claude = Claude(cfg, dry_run=dry_run)
    summaries = []
    for _ in range(n):
        if state.iteration >= cfg["limits"]["max_iterations"]:
            log.info("max_iterations=%d reached, stopping", cfg["limits"]["max_iterations"])
            break
        s = iterate(cfg, claude, state, fixed_plan)
        summaries.append(s)
        print(f"iteration {s['iteration']}: {s['metric']} = {s['value']} (target {s['target']}) "
              f"{'pass' if s['passed'] else 'fail'}, review: {s['review']}")
    return summaries


def best_dev_iteration(results, history: list[dict], dataset: str, metric: str = "max_rel_error") -> dict:
    """The iteration with the lowest `metric` (the dataset's target) on the dev split of `dataset`.

    Read from each iteration's execute.json: the summary holds whichever metric the plan chose.
    """
    candidates = []
    for h in history:
        file = results / f"iter_{h['iteration']:03d}" / "execute.json"
        if h.get("dataset") != dataset or h.get("error") or not file.exists():
            continue
        value = json.loads(file.read_text()).get("metrics", {}).get(metric)
        if isinstance(value, (int, float)):
            candidates.append((value, h))
    if not candidates:
        raise SystemExit(f"no successful iteration with {metric} on {dataset!r} in state.json")
    return min(candidates, key=lambda c: c[0])[1]


def final(cfg: dict) -> dict:
    """Final exam: run the best dev method + params once on the holdout split. Never called by the inner loop."""
    results = cfg["paths"]["results"]
    dataset = cfg["eval"]["dataset"]
    final_file = results / "final.json"
    finals = json.loads(final_file.read_text()) if final_file.exists() else {}
    if dataset in finals:
        raise SystemExit(f"{final_file} already holds the holdout result for {dataset!r}; the holdout is used once")

    metric = target_for(cfg)["metric"]
    best = best_dev_iteration(results, State.load(results).history, dataset, metric)
    dev = json.loads((results / f"iter_{best['iteration']:03d}" / "execute.json").read_text())["metrics"]
    ex = execute.run(cfg, {"method": best["method"], "params": best["params"]}, results / f"final_{dataset}", split="holdout")
    if not ex["ok"]:
        raise SystemExit(f"holdout run failed: {ex.get('error')}")
    holdout = ex["metrics"]
    keys = [k for k, v in holdout.items() if isinstance(v, (int, float)) and isinstance(dev.get(k), (int, float))]
    finals[dataset] = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "best_iteration": best["iteration"],
        "method": best["method"],
        "params": best["params"],
        "dev": dev,
        "holdout": holdout,
        "gap": {k: holdout[k] - dev[k] for k in keys},
        "holdout_outputs": f"final_{dataset}/",
    }
    write_json(final_file, finals)
    print(json.dumps(finals[dataset], indent=2))
    return finals[dataset]


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", type=int, default=1, help="iterations to run (default 1)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="no API calls; canned answers, separate results dir")
    parser.add_argument("--final", action="store_true", help="run the best dev iteration once on the holdout split")
    parser.add_argument("--dataset", choices=DATASETS, help="override eval.dataset in config.yaml")
    parser.add_argument("--params", help="skip the planner: run the method with these params (JSON object)")
    parser.add_argument("--method", help="method for --params (default: watershed for grains, flakes for flakes)")
    parser.add_argument("--hypothesis", default="Set by the outer loop.", help="hypothesis recorded with --params")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    use_dataset(cfg, args.dataset or cfg["eval"]["dataset"])
    if args.dry_run:
        cfg["paths"]["results"] = cfg["paths"]["results"] / "_dry_run"
        cfg["paths"]["journal"] = cfg["paths"]["results"] / "JOURNAL.md"
    if args.final:
        final(cfg)
        return
    fixed_plan = None
    if args.params:
        method = args.method or ("flakes" if task_of(cfg["eval"]["dataset"]) == "flakes" else "watershed")
        fixed_plan = {"method": method, "params": json.loads(args.params), "hypothesis": args.hypothesis}
    run_loop(cfg, args.n, args.dry_run, fixed_plan)


if __name__ == "__main__":
    main()

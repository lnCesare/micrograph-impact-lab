"""Step 3, execute: run the planned method on the dev images, then score it with the evaluator.

A failing method is recorded, not raised.
The method sees image paths only; src/evaluate.py alone reads the ground truth.

    uv run python -m src.execute --dataset synthetic --out results/baseline/synthetic   # no API calls
"""

import argparse
import json
import logging
import time
from pathlib import Path

import pandas as pd

from src import consistency, evaluate, splits
from src.config import task_of, use_dataset
from src.methods import METHODS
from src.state import write_json

log = logging.getLogger(__name__)
SRC = Path(__file__).resolve().parent


def code_version() -> str:
    """Short hash of the code that measures and scores (methods, evaluator, consistency check).
    It changes when the outer loop changes a method or the evaluator, so results across versions are not comparable."""
    import hashlib

    files = sorted((SRC / "methods").glob("*.py")) + [SRC / "evaluate.py", SRC / "consistency.py", SRC / "axial.py"]
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()[:10]


def run(cfg: dict, plan: dict, it_dir: Path, split: str = "dev") -> dict:
    dataset = cfg["eval"]["dataset"]
    out_dir = it_dir / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    execution = {"ok": False, "dataset": dataset, "split": split, "method": plan["method"], "params": plan["params"],
                 "code_version": code_version()}
    if task_of(dataset) == "flakes":
        return _run_flakes(cfg, plan, it_dir, split, execution, started)
    try:
        images = splits.image_paths(cfg, dataset, split)
        overlay = evaluate.representative(cfg, dataset, split)
        result = METHODS[plan["method"]](images, plan["params"], out_dir, overlay=overlay)
        _write_predictions(result["predictions"], out_dir / "predictions.csv")
        scored = evaluate.evaluate(cfg, dataset, split, result["predictions"], result["exclude_edge"])
        _write_defects(result.get("defects", {}), out_dir / "defects.csv")
        defect_maps = result.get("defect_maps") or {}
        if defect_maps and dataset == "synthetic":
            defects = evaluate.score_defects(evaluate.defect_truth(cfg, dataset, list(defect_maps)), defect_maps)
            execution["defects"] = defects
            for kind, d in defects.items():
                scored["metrics"][f"{kind}_f1"] = d["f1"]
                scored["metrics"][f"{kind}_detection_rate"] = d["detection_rate"]
        execution.update(
            ok=True,
            n_images=len(images),
            metrics=scored["metrics"],
            by_corruption_level=scored["by_corruption_level"],
            by_family=scored["by_family"],
            per_image=scored["per_image"],
            checks=result.get("checks", {}),
            overlays=[str(Path(p).relative_to(it_dir)) for p in result.get("overlays", [])],
        )
    except Exception as e:  # the loop must survive a broken experiment and record why it broke
        log.exception("execute: %s failed", plan["method"])
        execution["error"] = f"{type(e).__name__}: {e}"
    execution.setdefault("metrics", {})
    execution.setdefault("checks", {})
    execution.setdefault("overlays", [])
    execution["seconds"] = round(time.monotonic() - started, 2)
    write_json(it_dir / "execute.json", execution)
    log.info("execute: ok=%s metrics=%s", execution["ok"], execution["metrics"])
    return execution


def _run_flakes(cfg: dict, plan: dict, it_dir: Path, split: str, execution: dict, started: float) -> dict:
    """Flake datasets: lamellar_synth is scored against its exact truth, Ti64 by self-consistency."""
    dataset = cfg["eval"]["dataset"]
    out_dir = it_dir / "outputs"
    try:
        images = splits.image_paths(cfg, dataset, split)
        overlay = evaluate.representative_flakes(cfg, dataset, split)
        method = METHODS[plan["method"]]
        params = plan["params"]
        space = plan.get("search_space") or {}
        if space and dataset == "lamellar_synth" and split == "dev":
            params = _search(cfg, [Path(p).stem for p in images], params, space, execution, out_dir)
        execution["params_final"] = params
        result = method(images, params, out_dir, overlay=overlay)
        _write_predictions(result["predictions"], out_dir / "flakes.csv")
        pd.DataFrame([{"image": n, **s} for n, s in result["image_summaries"].items()]).to_csv(
            out_dir / "image_summaries.csv", index=False, float_format="%.4g")
        _write_scratches(result.get("defects", {}), out_dir / "scratches.csv")
        if dataset == "lamellar_synth":
            names = [Path(p).stem for p in images]
            scored = evaluate.score_flakes(cfg, names, result, out_dir / "labels")
        elif dataset == "Ti64_scratched":
            # the same params on the clean originals (reference) and without scratch handling (what it is worth)
            names = [Path(p).stem for p in images]
            clean = method(splits.image_paths(cfg, "Ti64", split), plan["params"], out_dir / "clean")
            no_handling = method(images, {**plan["params"], "detect_scratches": False}, out_dir / "no_handling")
            scored = evaluate.score_scratched(cfg, names, result, clean, no_handling)
        else:
            scored = consistency.check(method, images, plan["params"], out_dir, result,
                                       cfg["flakes"]["consistency_tolerances"])
        execution.update(
            ok=True,
            n_images=len(images),
            metrics=scored["metrics"],
            by_family=scored["by_family"],
            per_image=scored["per_image"],
            checks=result.get("checks", {}),
            overlays=[str(Path(p).relative_to(it_dir)) for p in result.get("overlays", [])],
        )
    except Exception as e:  # the loop must survive a broken experiment and record why it broke
        log.exception("execute: %s failed", plan["method"])
        execution["error"] = f"{type(e).__name__}: {e}"
    execution.setdefault("metrics", {})
    execution.setdefault("checks", {})
    execution.setdefault("overlays", [])
    execution["seconds"] = round(time.monotonic() - started, 2)
    write_json(it_dir / "execute.json", execution)
    log.info("execute: ok=%s metrics=%s", execution["ok"], execution["metrics"])
    return execution


def _search(cfg, names, start, space, execution, out_dir) -> dict:
    """Local search on the lamellar_synth dev images from `start` over `space` (param -> values); returns the best params."""
    from src.search import Searcher

    space = {k: ("abs", list(v)[:8]) for k, v in space.items() if isinstance(v, list) and v}
    space = dict(list(space.items())[:6])
    s = Searcher(Path(cfg["paths"]["lamellar_synth"]), names, Path(cfg["paths"]["results"]) / "search_cache",
                 code_version(), tolerances=cfg["flakes"]["tolerances"], log=log.info)
    res = s.run([dict(start)], space, budget=16)
    execution["search"] = {"n_evaluated": res["n_evaluated"], "n_trials": res["n_trials"],
                           "start_objective": res["start_best"]["objective"], "best_objective": res["best"]["objective"],
                           "best_params": res["best"]["params"],
                           "trials": [{"params": {k: t["params"].get(k) for k in space}, "objective": round(t["objective"], 3),
                                       "worst_family": t["metrics"]["worst_family_tolerance_ratio"]} for t in res["trials"]]}
    (out_dir / "search_trials.json").write_text(json.dumps(res["trials"], indent=2, default=str))
    return res["best"]["params"]


def _write_scratches(defects: dict, path: Path) -> None:
    rows = [{"image": name, **{k: (" ".join(map(str, v)) if isinstance(v, list) else v) for k, v in d.items()}}
            for name, items in defects.items() for d in items]
    if rows:
        pd.DataFrame(rows).to_csv(path, index=False)


def _write_defects(defects: dict, path: Path) -> None:
    rows = [{"image": name, **d, "centroid": f"{d['centroid'][0]} {d['centroid'][1]}"}
            for name, items in defects.items() for d in items]
    if rows:
        pd.DataFrame(rows).to_csv(path, index=False)


def _write_predictions(predictions: dict, path: Path) -> None:
    rows = [{"image": name, "grain": i + 1, **g} for name, grains in predictions.items() for i, g in enumerate(grains)]
    pd.DataFrame(rows).to_csv(path, index=False, float_format="%.4g")


def main(argv=None) -> None:
    from src.config import load_config

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dataset", choices=splits.DATASETS)
    parser.add_argument("--method", choices=sorted(METHODS), help="default: watershed for grains, flakes for flakes")
    parser.add_argument("--params", default="{}", help="JSON object of method params")
    parser.add_argument("--out", required=True, help="directory for execute.json and outputs/")
    parser.add_argument("--scratched-dir", help="Ti64_scratched: use this folder of scratched copies instead of paths.Ti64_scratched")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    if args.dataset:
        use_dataset(cfg, args.dataset)
    if args.scratched_dir:
        cfg["paths"]["Ti64_scratched"] = Path(args.scratched_dir).resolve()
    method = args.method or ("flakes" if task_of(cfg["eval"]["dataset"]) == "flakes" else "watershed")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    execution = run(cfg, {"method": method, "params": json.loads(args.params)}, out, split="dev")
    print(json.dumps({k: execution.get(k) for k in ["ok", "dataset", "split", "metrics", "by_corruption_level", "error"]}, indent=2))


if __name__ == "__main__":
    main()

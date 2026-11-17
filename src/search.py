"""Local parameter search for the flakes method on a dataset with exact truth (a twin, or lamellar_synth).

One loop iteration can evaluate many configurations instead of one: images run in parallel worker processes,
and every evaluated configuration is cached (trials.jsonl, keyed by params, code version and dataset),
so no configuration is ever evaluated twice, across iterations and across runs.

Strategy, within a budget of evaluations:
1. evaluate the start configurations (e.g. the resolved defaults and a warm start from the knowledge memory);
2. coordinate pass: for each param in the search space, try its candidate values around the current best;
3. local random refinement: perturb 1-3 params of the best at a time.
The objective is the worst family's tolerance ratio plus a quarter of the pooled one: a family that fails is not
hidden by the others. Only the dev images are searched; the holdout images are scored once, at the end, for the
chosen configuration and the start, so the gap shows how much the search fitted the dev images.
"""

import hashlib
import json
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from src import evaluate
from src.methods import METHODS

TOLERANCES = {"mean_direction_deg": 5.0, "order_parameter": 0.05, "misalignment_deg": 5.0,
              "aspect_ratio_rel": 0.15, "flake_orientation_deg": 5.0}

# candidate multipliers (numeric) or values (choices), per param; the coordinate pass tries each around the best
SPACE_BOUNDARIES = {
    "boundary_sensitivity": ("abs", [0.7, 0.85, 1.0, 1.2, 1.4]),
    "bridge_gaps_px": ("mul", [0.0, 0.5, 1.5, 2.2]),
    "h_px": ("mul", [0.5, 2.0, 3.5]),
    "merge_saddle_ratio": ("abs", [0.35, 0.5, 0.65, 0.8]),
    "merge_min_fill": ("abs", [0.8, 0.9, 0.97]),
    "sigma": ("mul", [0.7, 1.4]),
    "boundary_width_px": ("mul", [0.7, 1.4]),
    "min_area_px": ("mul", [0.5, 2.0]),
    "scratch_min_contrast_fraction": ("abs", [0.25, 0.35, 0.5]),
}
SPACE_PHASE = {
    "phase_offset": ("abs", [-0.12, -0.06, 0.0, 0.06, 0.12]),
    "h_px": ("mul", [0.5, 2.0, 3.5]),
    "merge_saddle_ratio": ("abs", [0.35, 0.5, 0.65, 0.8]),
    "merge_min_fill": ("abs", [0.8, 0.9, 0.97]),
    "sigma": ("mul", [0.7, 1.4]),
    "min_area_px": ("mul", [0.5, 2.0]),
    "flatten_px": ("mul", [0.5, 2.0]),
}
SPACE_REGIONS = {  # boundary_sensitivity scales the threshold on the grey-level steps
    "boundary_sensitivity": ("abs", [0.6, 0.8, 1.0, 1.2, 1.5]),
    "sigma": ("mul", [0.6, 1.5]),
    "bridge_gaps_px": ("mul", [0.0, 0.5, 1.5]),
    "h_px": ("mul", [0.5, 2.0, 3.5]),
    "merge_saddle_ratio": ("abs", [0.35, 0.5, 0.65, 0.8]),
    "merge_min_fill": ("abs", [0.8, 0.9, 0.97]),
    "min_area_px": ("mul", [0.5, 2.0]),
}
# fields of coarse cells (src/synth_cells.py twins): the failure is merging, so the merge and seed params lead
SPACE_CELLS = {
    "h_px": ("mul", [0.3, 0.6, 1.5, 2.5]),
    "merge_saddle_ratio": ("abs", [0.5, 0.65, 0.8, 1.0]),
    "boundary_sensitivity": ("abs", [0.6, 0.8, 1.0, 1.2]),
    "merge_border_darkness": ("abs", [0.0, 0.06, 0.12, 0.2]),
    "bridge_gaps_px": ("mul", [0.0, 0.5, 1.5]),
    "bridge_ends_px": ("abs", [0, 10, 20]),
    "min_area_px": ("mul", [0.3, 0.6, 1.5]),
    "sigma": ("mul", [0.7, 1.4]),
}
INTEGER = {"bridge_gaps_px", "bridge_ends_px", "min_area_px", "scratch_line_gap_px", "scratch_min_length_px", "tile_px"}
SEGMENTATION_TOLERANCE = 0.2  # on a twin with masks (coarse cells): 1 - F1 over this is a tolerance ratio (F1 0.8 -> 1)


def default_space(mode: str, cells: bool = False) -> dict:
    if cells and mode == "boundaries":
        return dict(SPACE_CELLS)
    return dict({"boundaries": SPACE_BOUNDARIES, "phase": SPACE_PHASE, "regions": SPACE_REGIONS}[mode])


def _key(params: dict, code_version: str, dataset_id: str, names: list[str]) -> str:
    blob = json.dumps({"p": params, "c": code_version, "d": dataset_id, "n": sorted(names)}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _run_one(args):
    image, params, labels_root = args
    res = METHODS["flakes"]([Path(image)], params, Path(labels_root))
    name = Path(image).stem
    return name, res["predictions"][name], res["image_summaries"][name], res["defect_maps"][name]


def objective(metrics: dict) -> float:
    """Worst family's tolerance ratio + a quarter of the pooled one; on a twin with masks (coarse cells) the
    segmentation counts as one more ratio, (1 - F1) / SEGMENTATION_TOLERANCE: merged cells can keep direction and S
    near the truth (the private rerun: S error 0.04, F1 0.56), so the statistics alone do not see them."""
    worst = metrics.get("worst_family_tolerance_ratio")
    pooled = metrics.get("worst_tolerance_ratio")
    if worst is None or pooled is None:
        return float("inf")
    f1 = metrics.get("segmentation_f1")
    if f1 is not None:
        seg = (1.0 - f1) / SEGMENTATION_TOLERANCE
        worst, pooled = max(worst, seg), max(pooled, seg)
    return float(worst + 0.25 * pooled)


class Searcher:
    def __init__(self, root: Path, names: list[str], cache_dir: Path, code_version: str, workers: int = 6,
                 tolerances: dict | None = None, log=print):
        self.root, self.names = Path(root), list(names)
        self.cache_file = Path(cache_dir) / "trials.jsonl"
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.code_version, self.workers, self.log = code_version, workers, log
        self.cfg = {"flakes": {"tolerances": tolerances or TOLERANCES}}
        self.dataset_id = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:10]
        self.cache = {}
        if self.cache_file.exists():
            for line in self.cache_file.read_text().splitlines():
                t = json.loads(line)
                self.cache[t["key"]] = t
        self.trials: list[dict] = []
        masks = self.root / "truth" / "masks"
        self.masks_dir = masks if masks.exists() else None  # a coarse-cell twin: segmentation is scored too

    def evaluate(self, params: dict, names: list[str] | None = None, tag: str = "") -> dict:
        names = names or self.names
        params = {k: v for k, v in sorted(params.items())}
        key = _key(params, self.code_version, self.dataset_id, names)
        if key in self.cache:
            t = dict(self.cache[key], cached=True, tag=tag)
            self.trials.append(t)
            return t
        start = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            images = [self.root / "images" / f"{n}.png" for n in names]
            with ProcessPoolExecutor(self.workers) as ex:
                parts = list(ex.map(_run_one, [(str(i), params, tmp) for i in images]))
            result = {"predictions": {n: p for n, p, _, _ in parts}, "image_summaries": {n: s for n, _, s, _ in parts},
                      "defect_maps": {n: d for n, _, _, d in parts}}
            scored = evaluate.score_flakes(self.cfg, names, result, Path(tmp) / "labels", truth_root=self.root)
            m = scored["metrics"]
            if self.masks_dir is not None:
                from src import score_masks

                seg = score_masks.score_dirs(Path(tmp) / "labels", self.masks_dir)["summary"]
                m["segmentation_f1"], m["merges"], m["splits"] = seg.get("f1"), seg.get("merges"), seg.get("splits")
        keep = ("worst_tolerance_ratio", "worst_family_tolerance_ratio", "median_direction_error_deg",
                "median_order_abs_error", "median_misalignment_abs_error_deg", "median_aspect_ratio_rel_error",
                "median_aspect_ratio_bias", "median_flake_orientation_error_deg", "flake_recall", "flake_precision",
                "scratch_precision", "scratch_recall_crossing", "segmentation_f1", "merges", "splits")
        t = {"key": key, "params": params, "names": "dev" if names == self.names else f"{len(names)} images",
             "metrics": {k: m.get(k) for k in keep}, "objective": objective(m),
             "by_family": {f: round(v["worst_tolerance_ratio"], 3) for f, v in scored["by_family"].items()
                           if v["worst_tolerance_ratio"] is not None},
             "seconds": round(time.monotonic() - start, 1), "code_version": self.code_version}
        with self.cache_file.open("a") as f:
            f.write(json.dumps(t, default=str) + "\n")
        self.cache[key] = t
        self.trials.append(dict(t, cached=False, tag=tag))
        return dict(t, cached=False, tag=tag)

    def run(self, starts: list[dict], space: dict, budget: int, seed: int = 0) -> dict:
        rng = np.random.default_rng(seed)
        evaluated = 0
        t_start = time.monotonic()

        def ev(p, tag):
            nonlocal evaluated
            t = self.evaluate(p, tag=tag)
            evaluated += 0 if t.get("cached") else 1
            seg = t["metrics"].get("segmentation_f1")
            spent = time.monotonic() - t_start
            eta = (f", {evaluated}/{budget} new, ~{spent / evaluated * max(budget - evaluated, 0) / 60:.1f} min left"
                   if evaluated and budget else "")
            self.log(f"  [{tag}] objective {t['objective']:.3f} (worst family {t['metrics']['worst_family_tolerance_ratio']}, "
                     f"pooled {t['metrics']['worst_tolerance_ratio']}" + (f", F1 {seg:.3f}" if seg is not None else "")
                     + f"){' cached' if t.get('cached') else ''}{eta}")
            return t

        results = [ev(s, f"start {i}") for i, s in enumerate(starts)]
        best = min(results, key=lambda t: t["objective"])
        start_best = best
        # coordinate pass
        for name, (kind, values) in space.items():
            if evaluated >= budget:
                break
            for v in values:
                if evaluated >= budget:
                    break
                cur = best["params"].get(name)
                if cur is None:
                    continue
                new = cur * v if kind == "mul" else v
                if name in INTEGER:
                    new = int(round(new))
                if isinstance(new, float):
                    new = round(new, 3)
                if new == cur:
                    continue
                t = ev({**best["params"], name: new}, f"{name}={new}")
                if t["objective"] < best["objective"] - 1e-6:
                    best = t
        # local random refinement
        names = list(space)
        while evaluated < budget:
            p = dict(best["params"])
            for name in rng.choice(names, size=int(rng.integers(1, 4)), replace=False):
                kind, values = space[name]
                if not isinstance(p.get(name), (int, float)) or isinstance(p.get(name), bool):
                    continue  # (None, or a choice such as "auto": the coordinate pass sets it)
                if kind == "mul":
                    p[name] = p[name] * float(np.exp(rng.normal(0, 0.3)))
                else:
                    lo, hi = min(values), max(values)
                    p[name] = float(np.clip(p[name] + rng.normal(0, 0.25 * (hi - lo)), lo, hi))
                p[name] = int(round(p[name])) if name in INTEGER else round(float(p[name]), 3)
            t = ev(p, "random")
            if t["objective"] < best["objective"] - 1e-6:
                best = t
        return {"best": best, "start_best": start_best, "n_evaluated": evaluated,
                "n_trials": len(self.trials), "trials": self.trials}

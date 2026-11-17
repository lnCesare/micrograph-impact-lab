"""The evaluator: the only code that reads ground truth (CSV files, label images, masks).

Methods return predictions only; this module scores them.
Conventions, checked against the synthetic set:
- count: predicted grains vs all ground-truth grains, including those touching the image edge
  (as `n_grains` in ground_truth_images.csv); when the method excluded edge grains, vs interior grains only.
- size: mean equivalent diameter of interior grains on both sides
  (as `mean_eq_diameter_um`, which averages grains with touches_edge == 0 only).
"""

import statistics
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
from scipy.ndimage import binary_dilation
from skimage.measure import label, regionprops
from skimage.morphology import disk

from src.splits import STEEL_316L, STEEL_316L_FOLDER, SYNTHETIC, load

METRICS_DOC = """The evaluator reports, over the dev images (errors are relative, 0.10 = 10 %):
- max_rel_error: max(median_count_rel_error, median_size_rel_error); the main metric, target <= 0.10.
- median_count_rel_error: median |predicted grain count - true count| / true count.
- median_size_rel_error: median |predicted - true| / true mean equivalent diameter of interior grains.
- median_count_bias, median_size_bias: the same, signed (> 0: too many grains / too large).
- worst_family_rel_error (synthetic only): max over families of max(family median count error, family median size error);
  the pooled medians can pass while one morphology fails.
- n_images: images scored.
With detect_scratches / detect_pores on the synthetic set, also (reported, not the target):
- scratch_f1, pore_f1: pixel F1 against the defect ground truth (2 px tolerance for scratches, none for pores);
- scratch_detection_rate, pore_detection_rate: fraction of true scratches / pores the method touched.
Precision, recall and false objects per defect type are in execute.json under "defects".
Per image errors, and (synthetic) medians per corruption_level and per family are in execute.json;
by_family[family].by_level shows the noise trend within one morphology (corruption levels mix families)."""


def truth(cfg: dict, dataset: str, split: str, names: list[str]) -> dict:
    """Ground truth per image: n_all, n_interior, mean_eq_interior, scale (um per px; 1 for 316L), corruption_level."""
    root = Path(cfg["paths"]["dataset"])
    stems = [Path(n).stem if dataset == "316L" else n for n in names]
    if dataset == "synthetic":
        base = root / SYNTHETIC
        images = pd.read_csv(base / "ground_truth_images.csv").set_index("image")
        grains = pd.read_csv(base / "ground_truth_grains.csv")
        interior = grains[grains.touches_edge == 0].groupby("image").size()
        return {
            n: {
                "n_all": int(images.at[n, "n_grains"]),
                "n_interior": int(interior.get(n, 0)),
                "mean_eq_interior": float(images.at[n, "mean_eq_diameter_um"]),
                "scale": float(images.at[n, "um_per_px"]),
                "unit": "um",
                "corruption_level": float(images.at[n, "corruption_level"]),
                "family": str(images.at[n, "family"]),
            }
            for n in stems
        }
    if dataset == "316L":
        folder = root / STEEL_316L / STEEL_316L_FOLDER[split] / "masks"
        return {n: truth_from_mask(folder / f"{n}.png") for n in stems}
    raise ValueError(f"unknown dataset {dataset!r}")


def truth_from_mask(path: Path) -> dict:
    """316L: grains are the 4-connected components of mask == 1 (0 is boundary). Sizes in pixels."""
    mask = np.array(Image.open(path))
    regions = regionprops(label(mask == 1, connectivity=1))
    h, w = mask.shape
    interior = [r.equivalent_diameter_area for r in regions
                if not (r.bbox[0] == 0 or r.bbox[1] == 0 or r.bbox[2] == h or r.bbox[3] == w)]
    return {
        "n_all": len(regions),
        "n_interior": len(interior),
        "mean_eq_interior": float(np.mean(interior)) if interior else float("nan"),
        "scale": 1.0,
        "unit": "px",
        "corruption_level": None,
        "family": None,
    }


def _median(values: list):
    return statistics.median(values) if values else None


def _max(*values):
    return max(v for v in values if v is not None)


def score(truth_by_image: dict, predictions: dict, exclude_edge: bool) -> dict:
    per_image = {}
    for name, t in truth_by_image.items():
        grains = predictions.get(name)
        if grains is None:
            per_image[name] = {"count_rel_error": -1.0, "size_rel_error": 1.0, "missing": True}
            continue
        n_true = t["n_interior"] if exclude_edge else t["n_all"]
        interior = [g["eq_diameter_px"] for g in grains if not g["touches_edge"]]
        row = {
            "n_true": n_true,
            "n_pred": len(grains),
            "count_rel_error": (len(grains) - n_true) / n_true,
            "mean_eq_true": t["mean_eq_interior"] if np.isfinite(t["mean_eq_interior"]) else None,
        }
        if row["mean_eq_true"] is None:
            # every true grain touches the edge (316L: grains ~80 px in a 400x300 image): size is undefined
            row["size_rel_error"] = None
            row["size_undefined"] = True
        elif interior:
            mean_pred = float(np.mean(interior)) * t["scale"]
            row["mean_eq_pred"] = mean_pred
            row["size_rel_error"] = (mean_pred - t["mean_eq_interior"]) / t["mean_eq_interior"]
        else:
            row["size_rel_error"] = 1.0  # no interior grain predicted: counted as a 100 % size error
            row["no_interior_grains"] = True
        row["corruption_level"] = t["corruption_level"]
        row["family"] = t.get("family")
        per_image[name] = row

    def medians(rows):
        count = [r["count_rel_error"] for r in rows]
        size = [r["size_rel_error"] for r in rows if r["size_rel_error"] is not None]
        return {
            "median_count_rel_error": _median([abs(x) for x in count]),
            "median_size_rel_error": _median([abs(x) for x in size]),
            "median_count_bias": _median(count),
            "median_size_bias": _median(size),
            "n_images": len(rows),
            "n_images_size": len(size),
        }

    metrics = medians(list(per_image.values()))
    metrics["max_rel_error"] = _max(metrics["median_count_rel_error"], metrics["median_size_rel_error"])
    levels: dict[str, list] = {}
    for r in per_image.values():
        if r.get("corruption_level") is not None:
            levels.setdefault(f"{r['corruption_level']:g}", []).append(r)
    by_level = {k: medians(v) for k, v in sorted(levels.items(), key=lambda kv: float(kv[0]))}
    families: dict[str, list] = {}
    for r in per_image.values():
        if r.get("family"):
            families.setdefault(r["family"], []).append(r)
    by_family = {
        fam: {
            **medians(rows),
            "by_level": {  # the noise trend within one morphology
                f"{r['corruption_level']:g}": {"count_rel_error": r["count_rel_error"], "size_rel_error": r["size_rel_error"]}
                for r in sorted(rows, key=lambda r: r["corruption_level"])
            },
        }
        for fam, rows in sorted(families.items())
    }
    if by_family:  # the pooled medians can hide a failing morphology
        metrics["worst_family_rel_error"] = max(
            _max(f["median_count_rel_error"], f["median_size_rel_error"]) for f in by_family.values()
        )
    return {"metrics": metrics, "by_corruption_level": by_level, "by_family": by_family, "per_image": per_image}


def evaluate(cfg: dict, dataset: str, split: str, predictions: dict, exclude_edge: bool) -> dict:
    """Score predictions {image stem: [grain, ...]} against every image of the split."""
    names = load(cfg)[dataset][split]
    return score(truth(cfg, dataset, split, names), predictions, exclude_edge)


# Defect ground truth: artifact_mask/ of the synthetic set. 60 = scratch, 120 = pore;
# 180 and 240 (round bright spots, large blobs) are not scored, their meaning is unconfirmed.
DEFECT_CODES = {"scratch": 60, "pore": 120}
DEFECT_PREDICTED = {"scratch": 1, "pore": 2}  # values in the method's defect maps
DEFECT_TOLERANCE_PX = {"scratch": 2, "pore": 0}


def defect_truth(cfg: dict, dataset: str, names: list[str]) -> dict:
    """Defect masks per image (synthetic only; 316L has no defect ground truth)."""
    if dataset != "synthetic":
        return {}
    base = Path(cfg["paths"]["dataset"]) / SYNTHETIC / "artifact_mask"
    return {n: np.array(Image.open(base / f"{n}.png")) for n in names}


def score_defects(truth_masks: dict, predicted: dict) -> dict:
    """Pixel precision / recall / F1 (micro-averaged) and object detection rate, per defect type."""
    out = {}
    for kind, code in DEFECT_CODES.items():
        tol = DEFECT_TOLERANCE_PX[kind]

        def grow(m):
            return binary_dilation(m, structure=disk(tol)) if tol else m

        tp_p = n_pred = tp_r = n_true = 0
        objects_true = objects_found = objects_pred = false_objects = 0
        for name, truth_all in truth_masks.items():
            truth = truth_all == code
            pred = predicted.get(name, np.zeros(truth.shape, np.uint8)) == DEFECT_PREDICTED[kind]
            truth_grown, pred_grown = grow(truth), grow(pred)
            tp_p += int((pred & truth_grown).sum())
            n_pred += int(pred.sum())
            tp_r += int((truth & pred_grown).sum())
            n_true += int(truth.sum())
            t_lab, p_lab = label(truth), label(pred)
            objects_true += int(t_lab.max())
            objects_found += len(np.unique(t_lab[pred_grown & (t_lab > 0)]))
            objects_pred += int(p_lab.max())
            false_objects += int(p_lab.max()) - len(np.unique(p_lab[truth_grown & (p_lab > 0)]))
        precision = tp_p / n_pred if n_pred else 0.0
        recall = tp_r / n_true if n_true else 0.0
        out[kind] = {
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "objects_true": objects_true,
            "objects_detected": objects_found,
            "detection_rate": objects_found / objects_true if objects_true else None,
            "objects_predicted": objects_pred,
            "false_objects": false_objects,
            "tolerance_px": tol,
        }
    return out


def representative(cfg: dict, dataset: str, split: str, k: int = 3) -> list[str]:
    """Names (stems) of k images to draw overlays for: lowest, middle and highest corruption (316L: by name)."""
    names = [Path(n).stem for n in load(cfg)[dataset][split]]
    if dataset == "synthetic":
        t = truth(cfg, dataset, split, names)
        names = sorted(names, key=lambda n: (t[n]["corruption_level"], n))
    if len(names) <= k:
        return names
    return [names[round(i * (len(names) - 1) / (k - 1))] for i in range(k)]


# --- flakes: shape and alignment of laths, scored on the code-generated lamellar set --------------------------

FLAKE_METRICS_DOC = """The evaluator reports, over the dev images of lamellar_synth (exact per-flake truth, code-generated):
- worst_tolerance_ratio: the main metric, target <= 1. The largest of these errors, each divided by its tolerance
  (config.yaml flakes.tolerances; tolerances are the scientist's call):
  - median_direction_error_deg: |predicted - true image mean direction| (axial, 0-90), images with true S >= 0.3 only
    (an isotropic image has no mean direction);
  - median_order_abs_error: |predicted - true order parameter S|;
  - median_misalignment_abs_error_deg: |predicted - true area-weighted mean misalignment to the mean direction|;
  - median_aspect_ratio_rel_error: |predicted - true median aspect ratio| / true, interior flakes;
  - median_flake_orientation_error_deg: orientation error of each true flake matched by a predicted flake with
    IoU >= 0.5, median over all matched flakes.
- flake_recall: fraction of all true flakes (area >= 30 px), pooled over the images, matched with IoU >= 0.5;
  flake_precision: fraction of all predicted flakes used for orientation that match a true flake;
  median_image_flake_recall: the median of the per-image recalls.
  Low recall with good orientation numbers means flakes are merged or cut, which biases the aspect ratio.
- median_order_bias, median_aspect_ratio_bias: signed (> 0: predicted too high).
- shape_population: what the flakes used for shape statistics are (median share per image):
  matched (IoU >= 0.5 with a true flake), fragment (part of one true flake: the flake was cut),
  merged (covers two or more true flakes), gb_alpha (on grain-boundary alpha), on_boundary (mostly no flake).
  matched_aspect_ratio_ratio: median predicted / true aspect ratio of the matched flakes.
  An aspect-ratio bias with matched_aspect_ratio_ratio near 1 comes from fragments or merges, not from how
  each flake is measured: fragments lower the median, merges of side-by-side laths too.
- scratch_precision: fraction of predicted scratch pixels on a true scratch (2 px tolerance);
  scratch_recall_crossing: fraction of the pixels of true scratches that cross the laths by >= 15 deg found
  (scratches running along the laths cannot be told from them by geometry and are reported apart:
  scratch_recall_parallel).
- by_family: the same per family (alignment random / weak / moderate / strong x corruption clean / scratched / harsh);
  worst_family_tolerance_ratio: the worst family.
The real Ti-6Al-4V images are checked separately, by running the loop with --dataset Ti64: there the central
disk of each image is measured as it is and turned by 30 deg, and the result must turn with it
(worst_consistency_ratio). That test exists; it is not part of this dataset's numbers.
Truth protocol (fixed): orientation statistics over true flakes with area >= 30 px and aspect ratio >= 1.5,
area-weighted, edge flakes included; aspect ratio over true flakes with area >= 30 px not touching the edge."""

FLAKE_TRUTH_MIN_AREA = 30
FLAKE_TRUTH_MIN_AR = 1.5
DIRECTION_MIN_ORDER = 0.3
MATCH_IOU = 0.5


def flake_truth(cfg: dict, names: list[str], truth_root: Path | None = None) -> dict:
    """Per image: the true flakes (DataFrame), the image statistics by the fixed protocol, and the family.
    truth_root: the dataset folder (lamellar_synth by default; a twin folder for the adapt tool)."""
    from src import axial

    base = Path(truth_root or cfg["paths"]["lamellar_synth"]) / "truth"
    flakes = pd.read_csv(base / "flakes.csv")
    images = pd.read_csv(base / "images.csv").set_index("image")
    out = {}
    for n in names:
        rows = flakes[(flakes.image == n) & (flakes.area_px >= FLAKE_TRUTH_MIN_AREA)].reset_index(drop=True)
        orient = rows[rows.aspect_ratio >= FLAKE_TRUTH_MIN_AR]
        s = axial.summary(orient.orientation_deg.to_numpy(), orient.area_px.to_numpy())
        interior = rows[~rows.touches_edge.astype(bool)]
        out[n] = {
            "flakes": rows,
            "stats": {**s, "median_aspect_ratio": float(interior.aspect_ratio.median()), "n_flakes": int(len(rows))},
            "family": str(images.at[n, "family"]),
        }
    return out


def _match_flakes(true_ids: np.ndarray, pred: np.ndarray, gb: np.ndarray, true_rows: pd.DataFrame):
    """Best predicted label per true flake by IoU. Returns {true flake id: (pred label, iou)} and the pred labels matched."""
    keep = np.isin(true_ids, true_rows.flake.to_numpy())
    t, q = true_ids[keep].astype(np.int64), pred[keep].astype(np.int64)
    n_pred = int(pred.max()) + 1
    inter = pd.Series(1, index=t * n_pred + q).groupby(level=0).sum()
    t_area = dict(zip(true_rows.flake, true_rows.area_px))
    p_area = np.bincount(pred.ravel(), minlength=n_pred)
    best: dict[int, tuple[int, float]] = {}
    for key, i in inter.items():
        tid, pid = divmod(int(key), n_pred)
        if pid == 0:
            continue
        iou = i / (t_area[tid] + p_area[pid] - i)
        if iou > best.get(tid, (0, 0.0))[1]:
            best[tid] = (pid, float(iou))
    in_gb = np.bincount(pred.ravel(), weights=gb.ravel().astype(float), minlength=n_pred) / np.maximum(p_area, 1)
    return best, in_gb


def _population(true_ids, pred, gb, true_rows, preds, matched) -> dict:
    """What the flakes used for shape statistics are: matched to a true flake (IoU >= 0.5), a fragment of one,
    two or more true flakes merged, on grain-boundary alpha, or mostly on a boundary.
    Also how well the matched ones are measured (predicted / true aspect ratio)."""
    n_pred = int(pred.max()) + 1
    n_true = int(true_ids.max()) + 1
    m = pred > 0
    pair = pd.Series(1, index=pred[m].astype(np.int64) * n_true + true_ids[m]).groupby(level=0).sum()
    p_of, t_of = pair.index // n_true, pair.index % n_true
    p_area = np.bincount(pred.ravel(), minlength=n_pred)
    in_gb = np.bincount(pred.ravel(), weights=gb.ravel().astype(float), minlength=n_pred) / np.maximum(p_area, 1)
    df = pd.DataFrame({"p": p_of, "t": t_of, "px": pair.values})
    df = df[df.t > 0]
    df["share"] = df.px / p_area[df.p]
    big = df[df.share >= 0.2].groupby("p").size()  # true flakes covering >= 20 % of the predicted flake
    main = df.sort_values("px").groupby("p").tail(1).set_index("p")
    matched_p = {pid: tid for tid, pid in matched.items()}
    true_ar = dict(zip(true_rows.flake, true_rows.aspect_ratio))
    counts = {"matched": 0, "fragment": 0, "merged": 0, "gb_alpha": 0, "on_boundary": 0}
    ar_ratio = []
    for k, f in enumerate(preds, start=1):
        if not f.get("used_for_shape"):
            continue
        if k in matched_p:
            counts["matched"] += 1
            if matched_p[k] in true_ar:
                ar_ratio.append(f["aspect_ratio"] / true_ar[matched_p[k]])
        elif in_gb[k] >= 0.5:
            counts["gb_alpha"] += 1
        elif big.get(k, 0) >= 2:
            counts["merged"] += 1
        elif k in main.index and main.at[k, "share"] >= 0.5:
            counts["fragment"] += 1
        else:
            counts["on_boundary"] += 1
    total = max(sum(counts.values()), 1)
    return {**{f"shape_share_{k}": v / total for k, v in counts.items()},
            "matched_aspect_ratio_ratio": float(np.median(ar_ratio)) if ar_ratio else None}


def score_flakes(cfg: dict, names: list[str], result: dict, labels_dir: Path, truth_root: Path | None = None) -> dict:
    from src import axial

    tol = cfg["flakes"]["tolerances"]
    truth = flake_truth(cfg, names, truth_root)
    base = Path(truth_root or cfg["paths"]["lamellar_synth"]) / "truth"
    per_image, flake_errors = {}, {}
    for n in names:
        t, s = truth[n], result["image_summaries"].get(n)
        if s is None:
            per_image[n] = {"missing": True, "family": t["family"]}
            continue
        ts = t["stats"]
        if ts.get("order_parameter") is None:  # no truth flake usable for orientation (a degenerate generated image)
            per_image[n] = {"missing": True, "missing_truth": True, "family": t["family"]}
            continue
        row = {"family": t["family"], "true_order": ts["order_parameter"], "pred_order": s["order_parameter"],
               "true_direction": ts["mean_direction_deg"], "pred_direction": s["mean_direction_deg"]}
        row["order_error"] = s["order_parameter"] - ts["order_parameter"] if s["order_parameter"] is not None else None
        if ts["order_parameter"] >= DIRECTION_MIN_ORDER and s["mean_direction_deg"] is not None:
            row["direction_error_deg"] = float(axial.axial_diff(s["mean_direction_deg"], ts["mean_direction_deg"]))
        if s.get("mean_misalignment_deg") is not None and ts.get("mean_misalignment_deg") is not None:
            row["misalignment_error_deg"] = s["mean_misalignment_deg"] - ts["mean_misalignment_deg"]
        if s.get("median_aspect_ratio") and ts.get("median_aspect_ratio"):
            row["true_aspect_ratio"], row["pred_aspect_ratio"] = ts["median_aspect_ratio"], s["median_aspect_ratio"]
            row["aspect_ratio_rel_error"] = (s["median_aspect_ratio"] - ts["median_aspect_ratio"]) / ts["median_aspect_ratio"]

        # per flake: match by IoU on the label images
        true_ids = np.array(Image.open(base / "flake_ids" / f"{n}.png")).astype(np.int64)
        pred = np.array(Image.open(Path(labels_dir) / f"{n}.png")).astype(np.int64)
        gb = np.array(Image.open(base / "gb_alpha" / f"{n}.png")) > 0
        best, in_gb = _match_flakes(true_ids, pred, gb, t["flakes"])
        preds = result["predictions"][n]
        design = dict(zip(t["flakes"].flake, t["flakes"].orientation_deg))
        matched = {tid: pid for tid, (pid, iou) in best.items() if iou >= MATCH_IOU}
        errs = [float(axial.axial_diff(preds[pid - 1]["orientation_deg"], design[tid])) for tid, pid in matched.items()
                if pid - 1 < len(preds)]
        flake_errors[n] = errs
        used = [k + 1 for k, f in enumerate(preds) if f.get("used_for_orientation") and in_gb[k + 1] < 0.5]
        matched_pred = set(matched.values())
        row.update(_population(true_ids, pred, gb, t["flakes"], preds, matched))
        row.update(
            n_true=int(len(t["flakes"])), n_pred=len(preds), n_matched=len(matched),
            flake_recall=len(matched) / max(len(t["flakes"]), 1),
            flake_precision=sum(k in matched_pred for k in used) / max(len(used), 1),
            n_pred_used=len(used), n_pred_used_matched=sum(k in matched_pred for k in used),
            median_flake_orientation_error_deg=float(np.median(errs)) if errs else None,
        )

        # scratches: 255 crossing the laths, 128 along them
        code = np.array(Image.open(base / "scratch" / f"{n}.png"))
        found = (result.get("defect_maps", {}).get(n, np.zeros(code.shape, np.uint8)) == 1)
        grow = lambda m: binary_dilation(m, structure=disk(2))
        row.update(
            scratch_px_found=int(found.sum()),
            scratch_tp=int((found & grow(code > 0)).sum()),
            scratch_crossing_px=int((code == 255).sum()),
            scratch_crossing_found=int(((code == 255) & grow(found)).sum()),
            scratch_parallel_px=int((code == 128).sum()),
            scratch_parallel_found=int(((code == 128) & grow(found)).sum()),
        )
        per_image[n] = row

    def summarise(rows, errs):
        rows = [r for r in rows if not r.get("missing")]

        def med(key, absolute=True):
            v = [r[key] for r in rows if r.get(key) is not None]
            return _median([abs(x) for x in v] if absolute else v)

        all_errs = [e for es in errs for e in es]
        m = {
            "median_direction_error_deg": med("direction_error_deg"),
            "median_order_abs_error": med("order_error"),
            "median_order_bias": med("order_error", absolute=False),
            "median_misalignment_abs_error_deg": med("misalignment_error_deg"),
            "median_aspect_ratio_rel_error": med("aspect_ratio_rel_error"),
            "median_aspect_ratio_bias": med("aspect_ratio_rel_error", absolute=False),
            "median_flake_orientation_error_deg": float(np.median(all_errs)) if all_errs else None,
            # pooled over flakes (a median over images can land on two images of one family and mislead)
            "flake_recall": sum(r["n_matched"] for r in rows) / max(sum(r["n_true"] for r in rows), 1),
            "flake_precision": (sum(r["n_pred_used_matched"] for r in rows)
                                / max(sum(r["n_pred_used"] for r in rows), 1)),
            "median_image_flake_recall": _median([r["flake_recall"] for r in rows]),
            "n_images": len(rows),
            "n_images_direction": sum(r.get("direction_error_deg") is not None for r in rows),
            # what the shape statistics are made of (medians over images)
            "shape_population": {k: _median([r[f"shape_share_{k}"] for r in rows if f"shape_share_{k}" in r])
                                 for k in ("matched", "fragment", "merged", "gb_alpha", "on_boundary")},
            "matched_aspect_ratio_ratio": med("matched_aspect_ratio_ratio", absolute=False),
        }
        ratios = {
            "mean_direction_deg": m["median_direction_error_deg"],
            "order_parameter": m["median_order_abs_error"],
            "misalignment_deg": m["median_misalignment_abs_error_deg"],
            "aspect_ratio_rel": m["median_aspect_ratio_rel_error"],
            "flake_orientation_deg": m["median_flake_orientation_error_deg"],
        }
        m["tolerance_ratios"] = {k: (v / tol[k] if v is not None else None) for k, v in ratios.items()}
        present = [v for v in m["tolerance_ratios"].values() if v is not None]
        m["worst_tolerance_ratio"] = max(present) if present else None
        return m

    metrics = summarise(list(per_image.values()), list(flake_errors.values()))
    metrics["n_images_without_truth"] = sum(bool(r.get("missing_truth")) for r in per_image.values())
    rows = [r for r in per_image.values() if not r.get("missing")]
    tp, n_found = sum(r["scratch_tp"] for r in rows), sum(r["scratch_px_found"] for r in rows)
    cross, cross_found = sum(r["scratch_crossing_px"] for r in rows), sum(r["scratch_crossing_found"] for r in rows)
    par, par_found = sum(r["scratch_parallel_px"] for r in rows), sum(r["scratch_parallel_found"] for r in rows)
    metrics["scratch_precision"] = tp / n_found if n_found else None
    metrics["scratch_recall_crossing"] = cross_found / cross if cross else None
    metrics["scratch_recall_parallel"] = par_found / par if par else None
    families: dict[str, list[str]] = {}
    for n, r in per_image.items():
        families.setdefault(r["family"], []).append(n)
    by_family = {fam: summarise([per_image[n] for n in ns], [flake_errors.get(n, []) for n in ns])
                 for fam, ns in sorted(families.items())}
    worst = [f["worst_tolerance_ratio"] for f in by_family.values() if f["worst_tolerance_ratio"] is not None]
    metrics["worst_family_tolerance_ratio"] = max(worst) if worst else None
    return {"metrics": metrics, "by_family": by_family, "per_image": per_image}


def representative_flakes(cfg: dict, dataset: str, split: str) -> list[str]:
    """Three images for the drawings: random, moderate and strong alignment, the harder corruptions first."""
    names = [Path(n).stem for n in load(cfg)["Ti64" if dataset == "Ti64_scratched" else dataset][split]]
    if dataset != "lamellar_synth":
        return names[:: max(len(names) // 3, 1)][:3]
    picks = []
    for prefix in ("random_harsh", "moderate_scratched", "strong_harsh"):
        picks += [n for n in names if n.startswith(prefix)][:1]
    return picks


# --- flakes: pasted scratches on real Ti64 images ---------------------------------------------------------------

SCRATCHED_METRICS_DOC = """Ti64_scratched: copies of the real Ti64 dev images with fake polishing scratches pasted at random
(src/scratch_paste.py; positions known only to this evaluator). The method runs three times with the same params:
on the clean originals (the reference), on the scratched copies, and on the scratched copies with
detect_scratches off (what the scratch handling is worth). The evaluator reports:
- worst_scratch_robustness_ratio: the main metric, target <= 1. How far the scratches move the measurements,
  scratched vs clean, each divided by its tolerance (config.yaml flakes.scratch_tolerances), the worst of:
  - median_scratch_direction_shift_deg (images with clean S >= 0.3), median_scratch_order_shift,
    median_scratch_aspect_ratio_shift (relative).
- the same three shifts without scratch handling (..._no_handling) and their worst ratio
  (worst_scratch_robustness_ratio_no_handling): the gap is what detecting and inpainting scratches buys.
- scratch_precision: fraction of detected scratch pixels on a pasted scratch (2 px tolerance);
  scratch_recall_crossing / scratch_recall_parallel: fraction of pasted scratch pixels found, for scratches that
  cross the local laths by >= 15 deg / run along them (expected near 0: they cannot be told from lath boundaries).
- median_flakes_crossed_change: relative change in the number of flakes used, scratched vs clean.
The original images may hold real scratches; any found there count against precision only if not near a pasted one."""


def score_scratched(cfg: dict, names: list[str], scratched: dict, clean: dict, no_handling: dict) -> dict:
    from src import axial

    tol = cfg["flakes"]["scratch_tolerances"]
    base = Path(cfg["paths"]["Ti64_scratched"]) / "truth" / "scratch"
    per_image = {}
    tp = n_found = cross = cross_found = par = par_found = 0
    for n in names:
        c = clean["image_summaries"][n]
        row = {}
        for tag, res in (("", scratched), ("_no_handling", no_handling)):
            s = res["image_summaries"][n]
            if c["order_parameter"] is not None and s["order_parameter"] is not None:
                row[f"order_shift{tag}"] = abs(s["order_parameter"] - c["order_parameter"])
                if c["order_parameter"] >= 0.3:
                    row[f"direction_shift_deg{tag}"] = float(axial.axial_diff(s["mean_direction_deg"], c["mean_direction_deg"]))
            if c.get("median_aspect_ratio") and s.get("median_aspect_ratio"):
                row[f"aspect_ratio_shift{tag}"] = abs(s["median_aspect_ratio"] / c["median_aspect_ratio"] - 1)
        row["flakes_used_change"] = (scratched["image_summaries"][n]["n_used_orientation"]
                                     / max(c["n_used_orientation"], 1) - 1)
        row["n_scratches_found"] = scratched["image_summaries"][n].get("n_scratches")
        code = np.array(Image.open(base / f"{n}.png"))
        found = scratched.get("defect_maps", {}).get(n, np.zeros(code.shape, np.uint8)) == 1
        grow = lambda m: binary_dilation(m, structure=disk(2))
        img_tp = int((found & grow(code > 0)).sum())
        tp, n_found = tp + img_tp, n_found + int(found.sum())
        cross += int((code == 255).sum())
        cross_found += int(((code == 255) & grow(found)).sum())
        par += int((code == 128).sum())
        par_found += int(((code == 128) & grow(found)).sum())
        row["scratch_recall_crossing"] = (int(((code == 255) & grow(found)).sum()) / max(int((code == 255).sum()), 1))
        per_image[n] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}

    rows = list(per_image.values())

    def med(key):
        return _median([r[key] for r in rows if r.get(key) is not None])

    m = {}
    for tag in ("", "_no_handling"):
        shifts = {
            "mean_direction_deg": med(f"direction_shift_deg{tag}"),
            "order_parameter": med(f"order_shift{tag}"),
            "aspect_ratio_rel": med(f"aspect_ratio_shift{tag}"),
        }
        m[f"median_scratch_direction_shift_deg{tag}"] = shifts["mean_direction_deg"]
        m[f"median_scratch_order_shift{tag}"] = shifts["order_parameter"]
        m[f"median_scratch_aspect_ratio_shift{tag}"] = shifts["aspect_ratio_rel"]
        ratios = {k: (v / tol[k] if v is not None else None) for k, v in shifts.items()}
        present = [v for v in ratios.values() if v is not None]
        m[f"scratch_robustness_ratios{tag}"] = ratios
        m[f"worst_scratch_robustness_ratio{tag}"] = max(present) if present else None
    m.update(
        scratch_precision=tp / n_found if n_found else None,
        scratch_recall_crossing=cross_found / cross if cross else None,
        scratch_recall_parallel=par_found / par if par else None,
        median_flakes_used_change=med("flakes_used_change"),
        n_images=len(rows),
    )
    return {"metrics": m, "per_image": per_image, "by_family": {}}

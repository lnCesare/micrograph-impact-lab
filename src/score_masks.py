"""Score flake label images against hand-corrected masks, for any segmentation method.

    python -m src.score_masks --labels <dir of label PNGs> --masks <dir of mask PNGs> [--json out.json]

Masks: 0 boundary, 1 flake, 2 not a flake (e.g. textured pockets at junctions), 3 ignore (e.g. out of focus).
The true flakes are the 4-connected regions of value 1. Labels: 0 background, 1..N flakes, one PNG per image;
a label PNG and a mask PNG are paired by file name (a "_mask", "_label", "_gt" ... suffix is ignored), and a label
image of another size is resized to the mask (nearest neighbour).

Per image, and pooled over all images:
- precision, recall and F1 of the flakes, a predicted and a true flake matching when their IoU is above 0.5
  (strictly: then each flake has at most one match);
- merges: predicted flakes that cover at least half of each of two or more true flakes;
- splits: true flakes that hold two or more predicted flakes, each lying at least half inside it and covering at
  least a tenth of it;
- pocket false positives: predicted flakes lying at least half on value 2;
- the errors of the mean direction, the order parameter S and the median aspect ratio of the predicted flakes
  against the mask's flakes, both measured the same way from their regions: orientation and axes from the second
  moments; direction and S area-weighted over flakes with aspect ratio >= 1.5; median aspect ratio over flakes that
  touch neither the image edge nor an ignore region. The direction error is given when the true S >= 0.3.
Predicted flakes lying at least half on value 3 (ignore) are not scored; regions under min_area px are left out of
both sides.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from skimage.measure import label

from src import axial

SUFFIXES = ("_mask", "_masks", "-mask", "_label", "_labels", "-label", "_gt", "_truth", "_annotation", "_seg")
MIN_AR_ORIENTATION = 1.5
DIRECTION_MIN_ORDER = 0.3


def _key(path: Path) -> str:
    stem = path.stem.lower()
    for s in SUFFIXES:
        if stem.endswith(s):
            return stem[: -len(s)]
    return stem


def _resize_nearest(lab: np.ndarray, shape) -> np.ndarray:
    rr = (np.arange(shape[0]) * lab.shape[0] / shape[0]).astype(int)
    cc = (np.arange(shape[1]) * lab.shape[1] / shape[1]).astype(int)
    return lab[rr][:, cc]


def _regions(lab: np.ndarray, min_area: int):
    """Per region: area, orientation, aspect ratio, whether it touches the image edge; tiny regions dropped."""
    out = {}
    h, w = lab.shape
    for i, sl in enumerate(ndi.find_objects(lab), start=1):
        if sl is None:
            continue
        rr, cc = np.nonzero(lab[sl] == i)
        if rr.size < min_area:
            continue
        rr, cc = rr + sl[0].start, cc + sl[1].start
        theta, major, minor = axial.moments_orientation(rr, cc)
        out[i] = {"area": int(rr.size), "theta": float(theta), "ar": float(major / minor) if minor > 0 else float("inf"),
                  "edge": bool(rr.min() == 0 or cc.min() == 0 or rr.max() == h - 1 or cc.max() == w - 1),
                  "rows": rr, "cols": cc}
    return out


def _stats(regions: dict, near_ignore: np.ndarray) -> dict:
    orient = [r for r in regions.values() if r["ar"] >= MIN_AR_ORIENTATION]
    direction, S = axial.resultant([r["theta"] for r in orient], [r["area"] for r in orient]) if orient else (None, None)
    shape = [r["ar"] for r in regions.values() if not r["edge"] and not near_ignore[r["rows"], r["cols"]].any()]
    return {"mean_direction_deg": direction, "order_parameter": S,
            "median_aspect_ratio": float(np.median(shape)) if shape else None, "n_orientation": len(orient),
            "n_shape": len(shape)}


def score_image(labels: np.ndarray, mask: np.ndarray, iou: float = 0.5, min_area: int = 30) -> dict:
    labels = np.asarray(labels).astype(np.int64)
    resized = labels.shape != mask.shape
    if resized:
        labels = _resize_nearest(labels, mask.shape)
    true = label(mask == 1, connectivity=1)
    ignore = mask == 3
    near_ignore = ndi.binary_dilation(ignore, iterations=2) if ignore.any() else ignore
    t_reg = _regions(true, min_area)
    p_all = _regions(labels, min_area)
    # not scored: predicted flakes mostly in the ignore region
    p_reg = {i: r for i, r in p_all.items() if ignore[r["rows"], r["cols"]].mean() < 0.5}
    pocket_fp = {i for i, r in p_reg.items() if (mask[r["rows"], r["cols"]] == 2).mean() >= 0.5}

    # overlaps between true and predicted flakes
    t_ids = np.where(np.isin(true, list(t_reg)), true, 0)
    p_ids = np.where(np.isin(labels, list(p_reg)), labels, 0)
    both = (t_ids > 0) & (p_ids > 0)
    pairs, inter = np.unique(np.stack([t_ids[both], p_ids[both]]), axis=1, return_counts=True) if both.any() \
        else (np.zeros((2, 0), int), np.zeros(0, int))
    matched_t, matched_p = set(), set()
    covers = {}    # predicted -> true flakes it covers at least half of
    holds = {}     # true -> predicted flakes lying at least half inside it, each covering >= 10 % of it
    for (ti, pi), n in zip(pairs.T, inter):
        at, ap = t_reg[ti]["area"], p_reg[pi]["area"]
        if n / (at + ap - n) > iou:
            matched_t.add(int(ti))
            matched_p.add(int(pi))
        if n >= 0.5 * at:
            covers.setdefault(int(pi), []).append(int(ti))
        if n >= 0.5 * ap and n >= 0.1 * at:
            holds.setdefault(int(ti), []).append(int(pi))
    tp = len(matched_t)
    n_pred, n_true = len(p_reg), len(t_reg)
    precision = tp / n_pred if n_pred else None
    recall = tp / n_true if n_true else None
    f1 = 2 * tp / (n_pred + n_true) if (n_pred + n_true) else None
    ts, ps = _stats(t_reg, near_ignore), _stats(p_reg, near_ignore)
    out = {
        "n_true": n_true, "n_pred": n_pred, "tp": tp, "fp": n_pred - tp, "fn": n_true - tp,
        "precision": precision, "recall": recall, "f1": f1,
        "merges": sum(len(v) >= 2 for v in covers.values()),
        "true_flakes_in_merges": sum(len(v) for v in covers.values() if len(v) >= 2),
        "splits": sum(len(v) >= 2 for v in holds.values()),
        "pocket_fp": len(pocket_fp),
        "true": {k: v for k, v in ts.items()}, "pred": {k: v for k, v in ps.items()},
        "direction_error_deg": None, "order_error": None, "aspect_ratio_rel_error": None,
        "resized": resized,
    }
    if ts["order_parameter"] is not None and ps["order_parameter"] is not None:
        out["order_error"] = ps["order_parameter"] - ts["order_parameter"]
        if ts["order_parameter"] >= DIRECTION_MIN_ORDER:
            out["direction_error_deg"] = float(axial.axial_diff(ps["mean_direction_deg"], ts["mean_direction_deg"]))
    if ts["median_aspect_ratio"] and ps["median_aspect_ratio"]:
        out["aspect_ratio_rel_error"] = (ps["median_aspect_ratio"] - ts["median_aspect_ratio"]) / ts["median_aspect_ratio"]
    return out


def score_dirs(labels_dir: Path, masks_dir: Path, iou: float = 0.5, min_area: int = 30) -> dict:
    masks = {_key(p): p for p in sorted(Path(masks_dir).glob("*.png"))}
    images, unmatched = {}, []
    for lp in sorted(Path(labels_dir).glob("*.png")):
        mp = masks.get(_key(lp))
        if mp is None:
            unmatched.append(lp.name)
            continue
        images[lp.stem] = score_image(np.array(Image.open(lp)), np.array(Image.open(mp)), iou, min_area)
    rows = list(images.values())
    tp, n_pred, n_true = (sum(r[k] for r in rows) for k in ("tp", "n_pred", "n_true"))
    med = lambda k: float(np.median([abs(r[k]) for r in rows if r[k] is not None])) if any(r[k] is not None for r in rows) else None
    signed = lambda k: float(np.median([r[k] for r in rows if r[k] is not None])) if any(r[k] is not None for r in rows) else None
    summary = {
        "n_images": len(rows), "n_true": n_true, "n_pred": n_pred, "tp": tp,
        "precision": tp / n_pred if n_pred else None, "recall": tp / n_true if n_true else None,
        "f1": 2 * tp / (n_pred + n_true) if (n_pred + n_true) else None,
        "merges": sum(r["merges"] for r in rows), "splits": sum(r["splits"] for r in rows),
        "pocket_fp": sum(r["pocket_fp"] for r in rows),
        "median_abs_direction_error_deg": med("direction_error_deg"), "max_direction_error_deg":
            max((r["direction_error_deg"] for r in rows if r["direction_error_deg"] is not None), default=None),
        "median_abs_order_error": med("order_error"), "median_order_bias": signed("order_error"),
        "median_abs_aspect_ratio_rel_error": med("aspect_ratio_rel_error"),
        "median_aspect_ratio_bias": signed("aspect_ratio_rel_error"),
        "masks_without_labels": sorted(set(masks) - {_key(Path(n + ".png")) for n in images}),
        "labels_without_masks": unmatched,
    }
    return {"summary": summary, "images": images, "iou": iou, "min_area": min_area}


def _fmt(v, nd=3):
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--masks", type=Path, required=True)
    ap.add_argument("--json", type=Path, help="write the full result here")
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--min-area", type=int, default=30)
    a = ap.parse_args(argv)
    res = score_dirs(a.labels, a.masks, a.iou, a.min_area)
    print(f"{'image':<28} {'P':>6} {'R':>6} {'F1':>6} {'merge':>5} {'split':>5} {'pocket':>6} "
          f"{'dir err':>7} {'S err':>7} {'AR err':>7}")
    for n, r in res["images"].items():
        print(f"{n[:28]:<28} {_fmt(r['precision'], 3):>6} {_fmt(r['recall'], 3):>6} {_fmt(r['f1'], 3):>6} "
              f"{r['merges']:>5} {r['splits']:>5} {r['pocket_fp']:>6} {_fmt(r['direction_error_deg'], 1):>7} "
              f"{_fmt(r['order_error'], 3):>7} {_fmt(r['aspect_ratio_rel_error'], 3):>7}")
    s = res["summary"]
    print(f"\npooled over {s['n_images']} images: precision {_fmt(s['precision'])}, recall {_fmt(s['recall'])}, "
          f"F1 {_fmt(s['f1'])}; merges {s['merges']}, splits {s['splits']}, pocket false positives {s['pocket_fp']}")
    print(f"median |direction error| {_fmt(s['median_abs_direction_error_deg'], 2)} deg (max "
          f"{_fmt(s['max_direction_error_deg'], 2)}), median |S error| {_fmt(s['median_abs_order_error'])}, "
          f"median aspect ratio bias {_fmt(s['median_aspect_ratio_bias'])}")
    if s["labels_without_masks"] or s["masks_without_labels"]:
        print(f"unpaired: labels {s['labels_without_masks']}, masks {s['masks_without_labels']}", file=sys.stderr)
    if a.json:
        def clean(o):
            if isinstance(o, dict):
                return {k: clean(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [clean(v) for v in o]
            if isinstance(o, (np.floating, np.integer)):
                return o.item()
            return o
        a.json.write_text(json.dumps(clean(res), indent=2) + "\n")


if __name__ == "__main__":
    main()

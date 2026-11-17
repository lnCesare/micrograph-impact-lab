"""Label-free checks for flake measurements on real images, which have no ground truth.

The central disk of each image is measured twice: as it is, and rotated by 30 degrees (bilinear resampling;
outside the disk is black, which the method's automatic region of interest leaves out).
The mean direction must turn by 30 degrees; the order parameter and the aspect ratio must not change.
A 90 degree turn would be no test: every pixel operation commutes with it exactly.
A method that fails this is biased by the pixel grid (or by anything else tied to the image axes),
or too sensitive to resampling. Passing it is necessary, not sufficient: a method can be
consistently wrong. The accuracy evidence comes from the lamellar_synth set and from the reviewer's reading
of the drawings.
"""

import statistics
from pathlib import Path

import numpy as np
from PIL import Image

from src import axial

DIRECTION_MIN_ORDER = 0.3
ANGLE_DEG = 30.0


def disk_pair(image: np.ndarray, angle_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """The central disk of an image (black outside), and the same disk turned counterclockwise by angle_deg."""
    from scipy import ndimage as ndi

    h, w = image.shape[:2]
    r = min(h, w) // 2 - 2
    rows, cols = np.ogrid[:h, :w]
    inside = (rows - h / 2) ** 2 + (cols - w / 2) ** 2 <= r**2
    if image.ndim == 3:
        image = image[..., :3].mean(axis=-1).astype(image.dtype)
    a = np.where(inside, image, 0)
    b = ndi.rotate(image.astype(float), angle_deg, reshape=False, order=1, mode="reflect")
    b = np.where(inside, np.clip(b, 0, 255 if image.dtype == np.uint8 else b.max()), 0).astype(image.dtype)
    sl = (slice(h // 2 - r, h // 2 + r), slice(w // 2 - r, w // 2 + r))
    return a[sl], b[sl]

METRICS_DOC = """The real Ti64 images have no ground truth. The loop reports, over the dev images:
- worst_consistency_ratio: the main metric, target <= 1. The central disk of each image is measured as it is
  and turned by 30 deg (resampled); the largest of these differences divided by its tolerance
  (config.yaml flakes.consistency_tolerances):
  - median_rotation_direction_diff_deg: |direction(turned) - (direction + 30)|, images with S >= 0.3;
  - median_rotation_order_abs_diff: |S(rotated) - S|;
  - median_rotation_aspect_ratio_rel_diff: |AR(rotated) - AR| / AR.
  Passing it is necessary, not sufficient: it catches pixel-grid bias, not a consistently wrong segmentation.
- median_estimator_gap: |S from the flakes - S from the image structure tensor|, two independent estimates;
  a large gap means one of them is not measuring the laths (report, not a target).
- the measurements themselves, per image (per_image) and their medians: order_parameter, local_order_parameter,
  mean_misalignment_deg, median_aspect_ratio, n_used_orientation, n_scratches.
The reviewer's reading of the drawings is the only check that the flakes and boundaries are right."""


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def check(method, images: list[Path], params: dict, out_dir: Path, result: dict, tolerances: dict) -> dict:
    rot_dir = Path(out_dir) / "rotated"
    rot_dir.mkdir(parents=True, exist_ok=True)
    straight, turned = [], []
    for path in map(Path, images):
        a, b = disk_pair(np.array(Image.open(path)), ANGLE_DEG)
        straight.append(rot_dir / f"{path.stem}_disk.png")
        turned.append(rot_dir / f"{path.stem}_disk_turned.png")
        Image.fromarray(a).save(straight[-1])
        Image.fromarray(b).save(turned[-1])
    p_disk = {**params, "roi": "auto"}
    ref = method(straight, p_disk, rot_dir / "out_straight")["image_summaries"]
    rot = method(turned, p_disk, rot_dir / "out_turned")["image_summaries"]
    disk_pairs = {Path(p).stem: (ref[f"{Path(p).stem}_disk"], rot[f"{Path(p).stem}_disk_turned"]) for p in images}

    per_image = {}
    for path in map(Path, images):
        n = path.stem
        full = result["image_summaries"][n]
        a, b = disk_pairs[n]
        row = {k: full.get(k) for k in ("mean_direction_deg", "order_parameter", "local_order_parameter",
                                     "mean_misalignment_deg", "circular_spread_deg", "median_aspect_ratio",
                                     "median_length_px", "median_width_px", "n_used_orientation", "n_scratches",
                                     "tensor_order_parameter", "polarity")}
        if a["order_parameter"] is not None and b["order_parameter"] is not None:
            row["rotation_order_diff"] = abs(b["order_parameter"] - a["order_parameter"])
            if a["order_parameter"] >= DIRECTION_MIN_ORDER:
                row["rotation_direction_diff_deg"] = float(
                    axial.axial_diff(b["mean_direction_deg"], a["mean_direction_deg"] + ANGLE_DEG))
        if a.get("median_aspect_ratio") and b.get("median_aspect_ratio"):
            row["rotation_aspect_ratio_rel_diff"] = abs(b["median_aspect_ratio"] - a["median_aspect_ratio"]) / a["median_aspect_ratio"]
        if full["order_parameter"] is not None:
            row["estimator_gap"] = abs(full["order_parameter"] - full["tensor_order_parameter"])
        per_image[n] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}

    rows = list(per_image.values())
    m = {
        "median_rotation_direction_diff_deg": _median([r.get("rotation_direction_diff_deg") for r in rows]),
        "median_rotation_order_abs_diff": _median([r.get("rotation_order_diff") for r in rows]),
        "median_rotation_aspect_ratio_rel_diff": _median([r.get("rotation_aspect_ratio_rel_diff") for r in rows]),
        "median_estimator_gap": _median([r.get("estimator_gap") for r in rows]),
        "median_order_parameter": _median([r["order_parameter"] for r in rows]),
        "median_local_order_parameter": _median([r["local_order_parameter"] for r in rows]),
        "median_mean_misalignment_deg": _median([r["mean_misalignment_deg"] for r in rows]),
        "median_aspect_ratio": _median([r["median_aspect_ratio"] for r in rows]),
        "median_n_flakes_used": _median([r["n_used_orientation"] for r in rows]),
        "total_scratches": sum(r["n_scratches"] or 0 for r in rows),
        "n_images": len(rows),
    }
    ratios = {
        "mean_direction_deg": m["median_rotation_direction_diff_deg"],
        "order_parameter": m["median_rotation_order_abs_diff"],
        "aspect_ratio_rel": m["median_rotation_aspect_ratio_rel_diff"],
    }
    m["consistency_ratios"] = {k: (v / tolerances[k] if v is not None else None) for k, v in ratios.items()}
    present = [v for v in m["consistency_ratios"].values() if v is not None]
    m["worst_consistency_ratio"] = max(present) if present else None
    return {"metrics": m, "per_image": per_image, "by_family": {}}

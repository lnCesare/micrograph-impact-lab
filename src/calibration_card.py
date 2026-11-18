"""Calibration card: numbers that describe a set of micrographs of cells, measured from the images and their masks,
so a twin can be fitted to images that cannot be shared (only the card crosses over, never pixels).

    python -m src.calibration_card --images DIR --masks DIR --out card.json [--scale 0.5]

Masks as in src.score_masks: 0 boundary, 1 cell, 2 not a cell (pockets, inclusions), 3 ignore (unreadable).
An image and its mask are paired by file name (a "_mask" ... suffix is ignored). Images are read as grey levels in
[0, 1] (an RGB image through the ITU-R 601 luma, as PIL's "L"); `--scale` resizes images (Lanczos) and masks
(nearest) first, e.g. 0.5 for originals that are analysed at half size. All pixel numbers are at the analysed size.

Per image (g: grey level; I: interior level; "cells": the 4-connected regions of mask 1 of at least 30 px):
- interior_level I: median of g over the cells eroded by 3 px (off the boundaries).
- n_cells: number of cells. cell_width_px, cell_length_px, cell_aspect_ratio: medians over the cells that touch
  neither the image edge nor a pocket or ignore region (2 px margin) of the minor and major axis of the ellipse with
  the same second moments (4 sqrt(eigenvalue), as scikit-image), and of their ratio.
- mean_direction_deg, order_parameter S: area-weighted axial mean and resultant length of the cell orientations,
  over cells with aspect ratio >= 1.5 (the measure of src.score_masks).
- boundary_contrast {p25, median, p75}: per boundary pixel (mask 0), (I - m) / I, where m is the darkest boundary
  pixel within the 5 x 5 window around it (the drawn boundary is 2-3 px wide and the dark line may sit off its
  centre).
- invisible_fraction: share of boundary pixels with contrast < 0.05; _along and _across: the same for boundary
  pixels whose tangent lies within 45 deg of the mean cell direction, or beyond (the tangent is normal to the
  gradient of the cell indicator, smoothed with sigma 1.5 px, from its structure tensor with sigma 2 px).
- boundary_fraction: share of the image that is boundary (mask 0).
- noise: standard deviation of g - gaussian(g, sigma 3 px) over the cells eroded by 10 px, without dark dots,
  divided by I.
- dots_per_megapixel: 4-connected spots of g < 0.75 I with 3-2000 px inside the cells eroded by 2 px, per 10^6 px
  of image.
- pocket_fraction, ignore_fraction: share of the image with mask 2, mask 3.
- illumination_range: (95th - 5th percentile) of gaussian(g, sigma 100 px) over the image, divided by median(g).
- sharpness: |Laplacian of gaussian(g, sigma 1 px)| averaged in non-overlapping 128 x 128 tiles;
  sharpness_raw_median, _raw_p10: median and 10th percentile of the tiles; sharpness_median, _p10: the same
  divided by the largest sharpness_raw_median of the set ("relative to the sharpest image").
- soft: ignore_fraction >= 0.10 (an image partly unreadable from blur).

Card version 2 (round 3) adds, because the 5 x 5 window jumps across the short gaps of beaded boundaries (the
private images: invisible 1-3 % with it, 13-21 % with the scientist's 3 x 3 measure):
- contrast_local {p25, median, p75}: per boundary pixel, (I_loc - m3) / I_loc, where m3 is the darkest boundary
  pixel within the 3 x 3 window around it and I_loc the local interior level: the median of the cell pixels eroded
  by 2 px within the 31 x 31 window around it (computed on an 8 px grid and interpolated bilinearly; where a window
  holds fewer than 30 such pixels, I is used).
- invisible_fraction_local, _local_along, _local_across: share of boundary pixels with contrast_local < 0.05
  (the along / across split of v1).
- gap runs along the boundary skeleton (skeletonize(mask == 0)): a skeleton pixel is invisible when contrast_local
  < 0.05 there; a run is an 8-connected group of invisible skeleton pixels, its length its pixel count.
  gap_run_p50_px, gap_run_p90_px: median and 90th percentile of the run lengths (0 without runs);
  gap_share_gt5, _gt10, _gt20: share of the skeleton's length in runs longer than 5, 10, 20 px.

Summary: [min, max] over the images of each key in SUMMARY_KEYS, over the sharp images (contrast, invisible
fractions and sharpness) or all images (the rest); "soft": [min, max] over the soft images of SOFT_KEYS.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from skimage.measure import label

from src import axial
from src.score_masks import _key, _regions, _stats

CARD_VERSION = 2
SOFT_IGNORE = 0.10
SUMMARY_KEYS_V1 = ("interior_level", "cell_width_px", "cell_length_px", "cell_aspect_ratio", "order_parameter",
                   "n_cells", "contrast_p25", "contrast_median", "contrast_p75", "invisible_fraction_along",
                   "invisible_fraction_across", "boundary_fraction", "noise", "dots_per_megapixel", "pocket_fraction",
                   "illumination_range", "sharpness_median", "sharpness_p10")
SUMMARY_KEYS_V2 = ("contrast_local_p25", "contrast_local_median", "contrast_local_p75", "invisible_fraction_local_along",
                   "invisible_fraction_local_across", "gap_run_p50_px", "gap_run_p90_px", "gap_share_gt5",
                   "gap_share_gt10", "gap_share_gt20")
SUMMARY_KEYS = SUMMARY_KEYS_V1 + SUMMARY_KEYS_V2
SHARP_ONLY = {"contrast_p25", "contrast_median", "contrast_p75", "invisible_fraction_along",
              "invisible_fraction_across", "sharpness_median", "sharpness_p10", *SUMMARY_KEYS_V2}
SOFT_KEYS = ("contrast_median", "invisible_fraction", "sharpness_median", "sharpness_p10", "ignore_fraction",
             "contrast_local_median", "invisible_fraction_local")


def _flat(row: dict) -> dict:
    out = dict(row)
    for k, v in row.get("boundary_contrast", {}).items():
        out[f"contrast_{k}"] = v
    for k, v in (row.get("contrast_local") or {}).items():
        out[f"contrast_local_{k}"] = v
    return out


def local_interior_level(g: np.ndarray, cells: np.ndarray, fallback: float, win: int = 31, step: int = 8,
                         min_count: int = 30) -> np.ndarray:
    """Median of the cell pixels (eroded by 2 px) in the win x win window around each pixel, on a step px grid,
    interpolated bilinearly; fallback where a window holds fewer than min_count of them."""
    from numpy.lib.stride_tricks import sliding_window_view

    h, w = g.shape
    inner = ndi.binary_erosion(cells, iterations=2)
    vals = np.pad(np.where(inner, g, np.nan), win // 2, constant_values=np.nan)
    view = sliding_window_view(vals, (win, win))
    rows, cols = np.arange(0, h, step), np.arange(0, w, step)
    grid = np.full((rows.size, cols.size), fallback)
    for i, r in enumerate(rows):
        block = view[r, cols].reshape(cols.size, -1)
        n = np.sum(~np.isnan(block), axis=1)
        with np.errstate(all="ignore"):
            med = np.nanmedian(np.where(n[:, None] > 0, block, 0.0), axis=1)
        grid[i] = np.where(n >= min_count, med, fallback)
    return ndi.zoom(grid, (h / grid.shape[0], w / grid.shape[1]), order=1, mode="nearest")[:h, :w]


def boundary_tangent_deg(mask: np.ndarray) -> np.ndarray:
    """Tangent direction (deg, project convention: x right, y up) of the cell boundaries, from the structure tensor of
    the smoothed cell indicator (its gradient is normal to the boundary)."""
    f = ndi.gaussian_filter((mask == 1).astype(float), 1.5)
    gr, gc = np.gradient(f)
    gx, gy = gc, -gr
    jxx, jyy, jxy = (ndi.gaussian_filter(a, 2.0) for a in (gx * gx, gy * gy, gx * gy))
    normal = 0.5 * np.degrees(np.arctan2(2 * jxy, jxx - jyy))
    return (normal + 90.0) % 180


def sharpness_tiles(g: np.ndarray, tile: int = 128) -> np.ndarray:
    lap = np.abs(ndi.laplace(ndi.gaussian_filter(g, 1.0)))
    h, w = g.shape
    vals = [lap[r:r + tile, c:c + tile].mean() for r in range(0, h - tile + 1, tile) for c in range(0, w - tile + 1, tile)]
    return np.asarray(vals if vals else [lap.mean()])


def image_stats(g: np.ndarray, mask: np.ndarray) -> dict:
    """The per-image numbers of the card (sharpness not yet relative to the set)."""
    g = np.asarray(g, float)
    mask = np.asarray(mask)
    h, w = g.shape
    cells = mask == 1
    boundary = mask == 0
    I = float(np.median(g[ndi.binary_erosion(cells, iterations=3)])) if cells.any() else float(np.median(g))

    lab = label(cells, connectivity=1)
    regions = _regions(lab, 30)
    blocked = ndi.binary_dilation((mask == 2) | (mask == 3), iterations=2)
    widths, lengths, ars = [], [], []
    for r in regions.values():
        if r["edge"] or blocked[r["rows"], r["cols"]].any():
            continue
        _, major, minor = axial.moments_orientation(r["rows"], r["cols"])
        widths.append(minor)
        lengths.append(major)
        ars.append(major / minor if minor > 0 else np.nan)
    st = _stats(regions, np.zeros_like(cells))

    darkest = ndi.grey_erosion(np.where(boundary, g, 1.0), size=(5, 5))
    contrast = (I - darkest[boundary]) / I
    # v2: 3 x 3 window, local interior level
    I_loc = local_interior_level(g, cells, I)
    m3 = ndi.grey_erosion(np.where(boundary, g, 1.0), size=(3, 3))
    c_loc_map = np.where(boundary, (I_loc - m3) / np.maximum(I_loc, 1e-6), np.nan)
    c_loc = c_loc_map[boundary]
    tangent = boundary_tangent_deg(mask)[boundary]
    mean_dir = st["mean_direction_deg"]
    if mean_dir is not None and np.isfinite(mean_dir):
        along = axial.axial_diff(tangent, mean_dir) < 45
    else:
        along = np.ones(contrast.shape, bool)
    inv = contrast < 0.05
    inv_loc = c_loc < 0.05
    from skimage.morphology import skeletonize

    skel = skeletonize(boundary)
    gaps, n_gaps = ndi.label(skel & (np.nan_to_num(c_loc_map, nan=1.0) < 0.05), structure=np.ones((3, 3)))
    runs = np.bincount(gaps.ravel())[1:] if n_gaps else np.zeros(0, int)
    skel_len = max(int(skel.sum()), 1)

    dark = g < 0.75 * I
    inside2 = ndi.binary_erosion(cells, iterations=2)
    spots, n = ndi.label(dark & inside2)
    sizes = np.bincount(spots.ravel())[1:] if n else np.zeros(0)
    n_dots = int(((sizes >= 3) & (sizes <= 2000)).sum())
    quiet = ndi.binary_erosion(cells, iterations=10) & ~ndi.binary_dilation(dark, iterations=3)
    hp = g - ndi.gaussian_filter(g, 3.0)
    noise = float(hp[quiet].std() / I) if quiet.sum() > 100 else None

    illum = ndi.gaussian_filter(g, 100.0, mode="reflect")
    tiles = sharpness_tiles(g)
    q = lambda a, p: float(np.percentile(a, p)) if a.size else None
    return {
        "shape": [h, w], "interior_level": round(I, 4), "n_cells": len(regions),
        "cell_width_px": float(np.median(widths)) if widths else None,
        "cell_length_px": float(np.median(lengths)) if lengths else None,
        "cell_aspect_ratio": float(np.nanmedian(ars)) if ars else None,
        "mean_direction_deg": mean_dir, "order_parameter": st["order_parameter"],
        "boundary_contrast": {"p25": q(contrast, 25), "median": q(contrast, 50), "p75": q(contrast, 75)},
        "contrast_local": {"p25": q(c_loc, 25), "median": q(c_loc, 50), "p75": q(c_loc, 75)},
        "invisible_fraction_local": float(inv_loc.mean()) if inv_loc.size else None,
        "invisible_fraction_local_along": float(inv_loc[along].mean()) if along.any() else None,
        "invisible_fraction_local_across": float(inv_loc[~along].mean()) if (~along).any() else None,
        "gap_run_p50_px": float(np.percentile(runs, 50)) if runs.size else 0.0,
        "gap_run_p90_px": float(np.percentile(runs, 90)) if runs.size else 0.0,
        "gap_share_gt5": float(runs[runs > 5].sum() / skel_len),
        "gap_share_gt10": float(runs[runs > 10].sum() / skel_len),
        "gap_share_gt20": float(runs[runs > 20].sum() / skel_len),
        "invisible_fraction": float(inv.mean()) if inv.size else None,
        "invisible_fraction_along": float(inv[along].mean()) if along.any() else None,
        "invisible_fraction_across": float(inv[~along].mean()) if (~along).any() else None,
        "boundary_fraction": float(boundary.mean()),
        "noise": noise, "dots_per_megapixel": n_dots / (h * w / 1e6),
        "pocket_fraction": float((mask == 2).mean()), "ignore_fraction": float((mask == 3).mean()),
        "illumination_range": float((np.percentile(illum, 95) - np.percentile(illum, 5)) / np.median(g)),
        "sharpness_raw_median": float(np.median(tiles)), "sharpness_raw_p10": float(np.percentile(tiles, 10)),
    }


def make_card(items, scale: float = 1.0, soft_ignore: float = SOFT_IGNORE, source: str = "") -> dict:
    """items: iterable of (name, grey image in [0, 1], mask). Returns the card (per image and summary)."""
    rows = []
    for name, g, mask in items:
        s = image_stats(g, mask)
        s["image"] = name
        rows.append(s)
    top = max(r["sharpness_raw_median"] for r in rows) if rows else 1.0
    for r in rows:
        r["sharpness_median"] = r["sharpness_raw_median"] / top
        r["sharpness_p10"] = r["sharpness_raw_p10"] / top
        r["soft"] = bool(r["ignore_fraction"] >= soft_ignore)
    flat = [_flat(r) for r in rows]
    sharp = [f for f in flat if not f["soft"]] or flat
    soft = [f for f in flat if f["soft"]]

    def rng(group, key):
        v = [f[key] for f in group if f.get(key) is not None]
        return [round(float(min(v)), 4), round(float(max(v)), 4)] if v else None

    card = {"card_version": CARD_VERSION, "made_by": "src.calibration_card", "source": source, "scale": scale,
            "n_images": len(rows), "n_soft": len(soft),
            "image_shape": [int(np.median([r["shape"][0] for r in rows])), int(np.median([r["shape"][1] for r in rows]))]
            if rows else None,
            "summary": {k: rng(sharp if k in SHARP_ONLY else flat, k) for k in SUMMARY_KEYS},
            "soft": {k: rng(soft, k) for k in SOFT_KEYS} if soft else None,
            "per_image": rows}
    return card


def load_card(path: Path) -> dict:
    """A card of any version: version 1 cards lack the v2 keys, and a card may leave keys out on purpose (a card
    shared with only its imaging statistics); the twin fit uses the keys present."""
    card = json.loads(Path(path).read_text())
    if not isinstance(card.get("summary"), dict):
        raise ValueError(f"calibration card {path} has no summary")
    card.setdefault("card_version", 1)
    return card


def _load_pair(img_path: Path, mask_path: Path, scale: float):
    im = Image.open(img_path).convert("L")
    mk = Image.open(mask_path)
    if mk.mode not in ("L", "P", "I", "I;16"):
        mk = mk.convert("L")
    if scale != 1.0:
        size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
        im = im.resize(size, Image.LANCZOS)
        mk = mk.resize(size, Image.NEAREST)
    g = np.asarray(im, float) / 255.0
    mask = np.asarray(mk).astype(np.uint8)
    if mask.shape != g.shape:
        raise ValueError(f"{mask_path.name}: mask {mask.shape} and image {g.shape} differ in size")
    return g, mask


def card_from_dirs(images: Path, masks: Path, scale: float = 1.0, soft_ignore: float = SOFT_IGNORE) -> dict:
    exts = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
    imgs = {_key(p): p for p in sorted(Path(images).iterdir()) if p.suffix.lower() in exts}
    msks = {_key(p): p for p in sorted(Path(masks).iterdir()) if p.suffix.lower() in exts}
    pairs = [(k, imgs[k], msks[k]) for k in sorted(imgs) if k in msks]
    if not pairs:
        raise SystemExit("no image has a mask of the same name")
    items = ((k, *_load_pair(i, m, scale)) for k, i, m in pairs)
    card = make_card(items, scale=scale, soft_ignore=soft_ignore, source=f"{len(pairs)} images with masks")
    card["unpaired_images"] = sorted(set(imgs) - set(msks))
    for r, (k, i, _) in zip(card["per_image"], pairs):
        r["image"] = Path(i).stem
    return card


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--masks", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scale", type=float, default=1.0, help="resize factor applied first (e.g. 0.5)")
    ap.add_argument("--soft-ignore", type=float, default=SOFT_IGNORE,
                    help="an image whose mask marks at least this share 'ignore' counts as soft")
    a = ap.parse_args(argv)
    card = card_from_dirs(a.images, a.masks, a.scale, a.soft_ignore)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(card, indent=2, default=float) + "\n")
    print(f"wrote {a.out}: {card['n_images']} images ({card['n_soft']} soft)")
    for k, v in card["summary"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()

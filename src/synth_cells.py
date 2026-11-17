"""Calibrated coarse-cell twin: code-generated optical micrographs of a field filled with elongated cells (no matrix),
with exact per-cell truth, fitted to a calibration card (src/calibration_card.py) of images that cannot be shared.

Calibrated to data/cards/coarse_cells_example.json (8 private images, 2026-11-17), which replaced the first,
description-based family (src/synth_cells_v1.py: boundaries 3x too dark, noise 2x, illumination 5x too strong).

Geometry: an anisotropic, multiplicatively weighted Voronoi tessellation of seeds in staggered rows, in coordinates
that follow curved rows (waviness). Cells tile the field like flattened pebbles: lens or brick shaped, tapered or
rounded ends, widths varying ~2x within an image (thin slivers to wide lenses), some boundaries curved, cells cut by
the image edge. Boundaries are thin (2-4 px), faint and beaded (chains of darker beads, gaps where they vanish),
transverse ones as visible as longitudinal ones. Elongated inclusions lie in some boundaries; speckled grey pockets
sit at some triple junctions; dark pores and a fine speckle lie inside the cells. Interiors are near white and flat;
noise and illumination are weak. Scratches: faint hairlines (~45 deg to the cells, long), thick dark bars at any angle,
short debris marks. "soft" images have an out-of-focus region (34-55 % of the field) and are softer overall.

Truth per image: flake_ids (cells 1..N), mask (0 boundary, 1 cell, 2 not a cell: pocket or inclusion, 3 ignore:
out of focus; cells touching it are ignore too), scratch map, per-cell table (second moments).

    python -m src.synth_cells --out data/coarse_cells_cal [--seed 0 | --seed entropy] [--card card.json]
"""

import argparse
import io
import json
import secrets
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from skimage.measure import label as cc_label

from src.axial import moments_orientation

PARALLEL_DEG = 15  # a scratch within this angle of the local cells is drawn as "along" in the scratch truth


@dataclass
class CellSpec:
    name: str
    family: str
    seed: int
    shape: tuple = (1101, 1376)
    mean_direction_deg: float = 90.0
    cell_width_px: float = 68.0         # row width scale of the seeds
    width_spread: float = 0.22          # log-normal sigma of row widths, and again of cell widths within a row
    length_spread: float = 0.26         # log-normal sigma of cell lengths
    aspect_ratio: float = 3.0           # seed length / width
    waviness_px: float = 40.0           # curvature of the rows (local direction +-10-20 deg)
    boundary_px: int = 3                # drawn width of a boundary (truth and line), 2-4
    interior_level: float = 0.975
    contrast_median: float = 0.12       # per boundary segment, before blur
    contrast_spread: float = 0.40       # log-normal sigma between segments
    bead_amplitude: float = 0.6         # darkness modulation along a boundary (beads)
    gap_fraction_along: float = 0.27    # share of boundary (by kind) where the line vanishes
    gap_fraction_across: float = 0.22
    gap_corr_px: float = 6.0            # correlation length of the gaps along a boundary (sigma)
    inclusions_per_mpx: float = 12.0
    pocket_fraction: float = 0.012      # area share of speckled junction pockets
    dots_per_mpx: float = 25.0
    speckle_per_mpx: float = 600.0
    n_hairline: int = 0
    thick_probability: float = 0.0
    thick_angle_deg: float | None = None  # angle of a thick scratch to the cells (None: any)
    n_debris: int = 0
    blur_px: float = 0.6                # optical blur everywhere (sigma)
    soft: bool = False                  # out-of-focus region and a softer readable part
    soft_fraction: tuple = (0.27, 0.45)   # unreadable share; with the cells touching it, 34-55 % is 'ignore'
    soft_transition: tuple = (0.15, 0.35)  # share of the field over which the blur grows to unreadable (readable)
    unreadable_blur_px: float = 3.0     # blur from which the field is unreadable ("ignore")
    soft_base_blur_px: float = 1.9      # blur at the best-focused side of a soft image
    oof_blur_px: tuple = (5.0, 8.0)     # blur at the far side of the focus gradient
    illumination: float = 0.03
    noise: float = 0.012
    noise_corr_px: float = 1.8          # noise correlation (JPEG, downscaling): sigma of its smoothing
    jpeg_quality: int = 92


def _rotated(rows, cols, angle_deg):
    t = np.deg2rad(angle_deg)
    u = cols * np.cos(t) - rows * np.sin(t)    # along the cells (y up)
    v = cols * np.sin(t) + rows * np.cos(t)    # across them
    return u, v


def _seeds(rng, spec, u_lo, u_hi, v_lo, v_hi):
    W, A = spec.cell_width_px, spec.aspect_ratio
    seeds = []  # u, v, half length, half width, row
    v, row = v_lo - 2 * W, 0
    while v < v_hi + 2 * W:
        wr = W * np.exp(rng.normal(0, spec.width_spread))
        vc = v + wr / 2
        u = u_lo - 2 * A * W - rng.uniform(0, A * wr)
        while u < u_hi + 2 * A * W:
            ln = A * wr * np.exp(rng.normal(0, spec.length_spread))
            ws = wr / 2 * np.exp(rng.normal(0, spec.width_spread))
            seeds.append((u + ln / 2, vc + rng.normal(0, 0.06 * wr), ln / 2, ws, row))
            u += ln
        v += wr
        row += 1
    return np.array(seeds)


def _tessellate(u, v, seeds, W, A):
    """Index of the seed that owns each pixel: argmin of ((u - u_k) / Ls_k)^2 + ((v - v_k) / Ws_k)^2."""
    tree = cKDTree(np.column_stack([seeds[:, 0] / (A * W), seeds[:, 1] / W]))
    pts = np.column_stack([u.ravel() / (A * W), v.ravel() / W])
    owner = np.empty(pts.shape[0], np.int32)
    k = min(12, len(seeds))
    for s in range(0, pts.shape[0], 250_000):
        _, idx = tree.query(pts[s:s + 250_000], k=k)
        uu, vv = u.ravel()[s:s + 250_000, None], v.ravel()[s:s + 250_000, None]
        d = ((uu - seeds[idx, 0]) / seeds[idx, 2]) ** 2 + ((vv - seeds[idx, 1]) / seeds[idx, 3]) ** 2
        owner[s:s + 250_000] = idx[np.arange(idx.shape[0]), np.argmin(d, axis=1)]
    return owner.reshape(u.shape)


def _one_piece_per_seed(lab):
    """A weighted tessellation can leave a seed's region in pieces: keep the largest, give the rest to neighbours."""
    comp = cc_label(lab + 1, connectivity=1)
    sizes = np.bincount(comp.ravel())
    owner = np.zeros(sizes.size, np.int64)
    owner[comp.ravel()] = lab.ravel()
    best = {}
    for c in np.argsort(sizes)[::-1]:
        if c == 0:
            continue
        best.setdefault(int(owner[c]), int(c))
    keep = np.zeros(sizes.size, bool)
    keep[list(best.values())] = True
    bad = ~keep[comp]
    if bad.any():
        _, (ir, ic) = ndi.distance_transform_edt(bad, return_indices=True)
        lab = lab[ir, ic]
    return lab


def _blob(rng, h, w, y, x, rad):
    out = np.zeros((h, w), bool)
    for _ in range(int(rng.integers(2, 5))):
        cy, cx = y + rng.normal(0, 0.35 * rad), x + rng.normal(0, 0.35 * rad)
        r_ = rad * rng.uniform(0.5, 0.9)
        y0, y1, x0, x1 = int(max(cy - r_, 0)), int(min(cy + r_ + 1, h)), int(max(cx - r_, 0)), int(min(cx + r_ + 1, w))
        if y1 > y0 and x1 > x0:
            yy, xx = np.mgrid[y0:y1, x0:x1]
            out[y0:y1, x0:x1] |= (yy - cy) ** 2 + (xx - cx) ** 2 <= r_ ** 2
    return out


def _line(rows, cols, r0, c0, angle_deg, half_len, width):
    t = np.deg2rad(angle_deg)
    dist = np.abs((cols - c0) * np.sin(t) + (rows - r0) * np.cos(t))
    along = (cols - c0) * np.cos(t) - (rows - r0) * np.sin(t)
    on = np.abs(along) < half_len
    return np.exp(-0.5 * (dist / max(width / 2, 0.5)) ** 2) * on, (dist <= width / 2 + 0.5) & on


def generate(spec: CellSpec) -> dict:
    rng = np.random.default_rng(spec.seed)
    h, w = spec.shape
    rows, cols = np.mgrid[0:h, 0:w].astype(float)
    W, A = spec.cell_width_px, spec.aspect_ratio

    # 1. geometry: curved rows of seeds, weighted anisotropic tessellation
    u, v = _rotated(rows, cols, spec.mean_direction_deg)
    lam, ph = rng.uniform(500, 900), rng.uniform(0, 2 * np.pi)
    lam2, ph2, a2 = rng.uniform(1200, 2000), rng.uniform(0, 2 * np.pi), rng.uniform(0.5, 1.2)
    wave = spec.waviness_px * (np.sin(2 * np.pi * u / lam + ph) + a2 * np.sin(2 * np.pi * u / lam2 + ph2))
    slope = spec.waviness_px * (2 * np.pi / lam * np.cos(2 * np.pi * u / lam + ph)
                                + a2 * 2 * np.pi / lam2 * np.cos(2 * np.pi * u / lam2 + ph2))
    local_dir = (spec.mean_direction_deg + np.degrees(np.arctan(slope))) % 180   # (rows v' = const turn by +atan(slope))
    vw = v + wave
    seeds = _seeds(rng, spec, u.min(), u.max(), vw.min(), vw.max())
    lab = _one_piece_per_seed(_tessellate(u, vw, seeds, W, A))

    # 2. boundaries (drawn width b), and which seeds each boundary pixel lies between
    b = int(np.clip(spec.boundary_px, 2, 4))
    lo = ndi.minimum_filter(lab, size=b + 1)
    hi = ndi.maximum_filter(lab, size=b + 1)
    boundary = lo != hi
    row_of = seeds[:, 4].astype(int)
    across = boundary & (row_of[lo] == row_of[hi])          # between cells of the same row: end to end

    # 3. inclusions in boundaries along the cells, pockets at triple junctions (both "not a cell")
    not_cell = np.zeros((h, w), bool)
    incl = np.zeros((h, w), bool)
    along_pix = np.flatnonzero((boundary & ~across).ravel())
    n_incl = int(rng.poisson(spec.inclusions_per_mpx * h * w / 1e6))
    incl_dark = np.zeros((h, w))
    for p in rng.choice(along_pix, size=min(n_incl, along_pix.size), replace=False) if n_incl and along_pix.size else []:
        cy, cx = divmod(int(p), w)
        ln, wd = rng.uniform(10, 60), rng.uniform(3, 10)
        t = np.deg2rad(local_dir[cy, cx])
        y0, y1, x0, x1 = max(cy - 32, 0), min(cy + 33, h), max(cx - 32, 0), min(cx + 33, w)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        a_ = (xx - cx) * np.cos(t) - (yy - cy) * np.sin(t)
        c_ = (xx - cx) * np.sin(t) + (yy - cy) * np.cos(t)
        ell = (a_ / (ln / 2)) ** 2 + (c_ / (wd / 2)) ** 2 <= 1
        incl[y0:y1, x0:x1] |= ell
        incl_dark[y0:y1, x0:x1] = np.where(ell, rng.uniform(0.2, 0.55), incl_dark[y0:y1, x0:x1])
    nb = ndi.maximum_filter(lab, size=3) != ndi.minimum_filter(lab, size=3)
    mid = (ndi.maximum_filter(lab, size=3) != lab) & (ndi.minimum_filter(lab, size=3) != lab)
    junctions = np.flatnonzero((nb & mid).ravel())
    pocket = np.zeros((h, w), bool)
    target = spec.pocket_fraction * h * w
    for p in rng.permutation(junctions)[:400] if junctions.size else []:
        if pocket.sum() >= target:
            break
        cy, cx = divmod(int(p), w)
        if pocket[max(cy - 60, 0):cy + 60, max(cx - 60, 0):cx + 60].any():
            continue
        pocket |= _blob(rng, h, w, cy, cx, rng.uniform(10, 40))
    not_cell = incl | pocket

    # 4. cells: what is neither boundary nor "not a cell"; remnants cut off by pockets go to the pocket
    ids = np.where(~boundary & ~not_cell, lab + 1, 0)
    comp = cc_label(ids, connectivity=1)
    sizes = np.bincount(comp.ravel())
    full = np.bincount((lab + 1).ravel(), minlength=ids.max() + 1)
    owner = np.zeros(sizes.size, np.int64)
    owner[comp.ravel()] = ids.ravel()
    # per seed: the largest piece is the cell; small pieces (< 150 px or < 25 % of the seed region) are absorbed
    keep = np.zeros(sizes.size, bool)
    seen = set()
    for c in np.argsort(sizes)[::-1]:
        if c == 0 or owner[c] == 0:
            continue
        if owner[c] in seen:
            continue
        seen.add(int(owner[c]))
        keep[c] = sizes[c] >= 150 and sizes[c] >= 0.25 * full[owner[c]] if full[owner[c]] else False
    dropped = (comp > 0) & ~keep[comp]
    pocket |= dropped & ndi.binary_dilation(pocket, iterations=6)
    boundary |= dropped & ~pocket            # a sliver cut off by a boundary: drawn as boundary
    comp = np.where(keep[comp], comp, 0)
    uniq = np.unique(comp[comp > 0])
    remap = np.zeros(comp.max() + 1, np.int32)
    remap[uniq] = np.arange(1, uniq.size + 1)
    ids = remap[comp]
    not_cell = incl | pocket

    # 5. render: near-white cells, faint beaded boundaries with gaps
    I = spec.interior_level
    img = np.full((h, w), I)
    pair = lo.astype(np.int64) * (len(seeds) + 1) + hi
    codes, inv = np.unique(pair[boundary], return_inverse=True)
    c_seg = spec.contrast_median * np.exp(rng.normal(0, spec.contrast_spread, codes.size))
    c_pix = np.zeros((h, w))
    c_pix[boundary] = c_seg[inv]
    bead = ndi.gaussian_filter(rng.normal(0, 1, (h, w)), 1.5)
    bead /= bead.std()
    c_pix *= np.exp(spec.bead_amplitude * bead - spec.bead_amplitude ** 2 / 2)
    gapf = ndi.gaussian_filter(rng.normal(0, 1, (h, w)), spec.gap_corr_px)
    for kind_mask, frac in ((boundary & ~across, spec.gap_fraction_along), (boundary & across, spec.gap_fraction_across)):
        if kind_mask.any() and frac > 0:
            cut = np.quantile(gapf[kind_mask], frac)
            gap = kind_mask & (gapf < cut)
            c_pix[gap] *= rng.uniform(0.0, 0.2, int(gap.sum()))
    img[boundary] = I * (1 - np.clip(c_pix[boundary], 0, 0.85))
    img[incl] = I * (1 - incl_dark[incl])
    if pocket.any():
        speck = ndi.gaussian_filter(rng.normal(0, 1, (h, w)), 1.2)
        speck /= speck.std()
        base = rng.uniform(0.2, 0.35)
        grey = I * (1 - base - 0.08 * speck)
        dots_p = ndi.binary_dilation(rng.uniform(size=(h, w)) < 0.015)
        grey = np.where(dots_p, I * (1 - base - 0.25), grey)
        img[pocket] = grey[pocket]

    # pores and black blobs inside cells, and a fine speckle
    cell_pix = np.flatnonzero(ids.ravel() > 0)
    n_dots = int(rng.poisson(spec.dots_per_mpx * h * w / 1e6))
    for p in rng.choice(cell_pix, size=min(n_dots, cell_pix.size), replace=False) if n_dots and cell_pix.size else []:
        cy, cx = divmod(int(p), w)
        big = rng.uniform() < 0.05
        rad = rng.uniform(6, 12.5) if big else rng.uniform(1.5, 7.5)
        y0, y1, x0, x1 = max(cy - 14, 0), min(cy + 15, h), max(cx - 14, 0), min(cx + 15, w)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= rad ** 2
        img[y0:y1, x0:x1][disc] = I * (1 - (rng.uniform(0.85, 0.97) if big else rng.uniform(0.35, 0.8)))
    n_speck = int(rng.poisson(spec.speckle_per_mpx * h * w / 1e6))
    for p in rng.choice(cell_pix, size=min(n_speck, cell_pix.size), replace=False) if n_speck and cell_pix.size else []:
        cy, cx = divmod(int(p), w)
        img[cy:cy + int(rng.integers(1, 3)), cx:cx + int(rng.integers(1, 3))] = I * (1 - rng.uniform(0.08, 0.3))

    # 6. scratches: hairlines (~45 deg to the cells or any angle), a thick bar, debris marks
    scratch = np.zeros((h, w), bool)
    crossing = np.zeros((h, w), bool)
    lines = []
    n_thick = int(rng.uniform() < spec.thick_probability)
    for k in range(spec.n_hairline + n_thick + spec.n_debris):
        thick, debris = spec.n_hairline <= k < spec.n_hairline + n_thick, k >= spec.n_hairline + n_thick
        if thick and spec.thick_angle_deg is not None:
            rel = rng.choice([-1, 1]) * spec.thick_angle_deg
        elif thick or debris or rng.uniform() < 0.3:
            rel = rng.uniform(-90, 90)
        else:
            rel = rng.choice([-45, 45]) + rng.normal(0, 6)
        angle = (spec.mean_direction_deg + rel) % 180
        r0, c0 = rng.uniform(0.2 * h, 0.8 * h), rng.uniform(0.2 * w, 0.8 * w)
        if thick:
            half, width, delta = rng.uniform(50, 200), rng.uniform(5, 10), -rng.uniform(0.3, 0.6)
        elif debris:
            half, width, delta = rng.uniform(8, 30), rng.uniform(2, 3), -rng.uniform(0.2, 0.5)
        else:
            half, width = rng.uniform(150, 600), rng.uniform(1.0, 2.0)
            delta = rng.choice([-1, 1], p=[0.75, 0.25]) * rng.uniform(0.03, 0.08)
        prof, this = _line(rows, cols, r0, c0, angle, half, width)
        img = img * (1 + delta * prof) if delta < 0 else np.minimum(img + delta * prof, 1.0)
        ys, xs = np.nonzero(this)
        length = float(np.hypot(ys.max() - ys.min(), xs.max() - xs.min())) if ys.size else 0.0
        scratch |= this
        dd = np.abs((angle - local_dir) % 180)
        crossing |= this & (np.minimum(dd, 180 - dd) >= PARALLEL_DEG)
        lines.append({"direction_deg": float(angle), "angle_to_cells_deg": float(abs(rel) if abs(rel) <= 90 else 180 - abs(rel)),
                      "length_px": round(length, 1), "thick": bool(thick), "debris": bool(debris),
                      "bright": bool(delta > 0), "width_px": round(float(width), 2)})

    # 7. illumination, optics (blur; a soft image has an out-of-focus region), noise, 8 bits, mild JPEG
    field = ndi.gaussian_filter(rng.normal(0, 1, (h // 8 + 1, w // 8 + 1)), 25)
    field = ndi.zoom(field, (h / field.shape[0], w / field.shape[1]), order=1)[:h, :w]
    spread = np.percentile(field, 95) - np.percentile(field, 5)
    img = img * (1 + spec.illumination * (field - field.mean()) / (spread + 1e-12))
    ignore = np.zeros((h, w), bool)
    if spec.soft:
        # a focus gradient (a tilted specimen): the blur grows across the field, from the sharp level through a
        # readable transition band to an unreadable part (blur sigma >= unreadable_blur_px, "ignore" in the truth)
        b_angle = rng.uniform(0, 2 * np.pi)
        proj = (cols - w / 2) * np.cos(b_angle) + (rows - h / 2) * np.sin(b_angle)
        q = (np.argsort(np.argsort(proj.ravel())) / proj.size).reshape(h, w)   # area quantile along the gradient
        f = rng.uniform(*spec.soft_fraction)
        q_u = 1 - f                                                         # where the blur reaches unreadable
        q_0 = max(q_u - rng.uniform(*spec.soft_transition), 0.0)            # where it starts to grow
        s_u, s_max = spec.unreadable_blur_px, rng.uniform(*spec.oof_blur_px)
        s_0 = max(spec.soft_base_blur_px, spec.blur_px)
        sig = np.where(q < q_0, s_0,
                       np.where(q < q_u, s_0 + (s_u - s_0) * (q - q_0) / max(q_u - q_0, 1e-6),
                                s_u + (s_max - s_u) * (q - q_u) / max(1 - q_u, 1e-6)))
        levels = np.array([spec.blur_px, 1.0, 1.5, 2.2, 3.0, 4.0, 5.5, 7.5, 10.0])
        levels = np.unique(np.clip(levels, spec.blur_px, None))
        k = np.clip(np.searchsorted(levels, sig) - 1, 0, levels.size - 2)
        t = np.clip((sig - levels[k]) / (levels[k + 1] - levels[k]), 0, 1)
        out = np.zeros((h, w))
        for i, lv in enumerate(levels):
            wgt = np.where(k == i, 1 - t, 0.0) + np.where(k == i - 1, t, 0.0)
            if wgt.any():
                out += wgt * ndi.gaussian_filter(img, lv)
        img = out
        ignore = sig >= s_u
    else:
        img = ndi.gaussian_filter(img, spec.blur_px)
    nz = ndi.gaussian_filter(rng.normal(0, 1, img.shape), spec.noise_corr_px) if spec.noise_corr_px > 0 else rng.normal(0, 1, img.shape)
    img = img + spec.noise * I * nz / nz.std()
    image = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
    if spec.jpeg_quality and spec.jpeg_quality < 100:
        buf = io.BytesIO()
        Image.fromarray(image).save(buf, format="JPEG", quality=int(spec.jpeg_quality))
        image = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("L"))

    # 8. truth: cells touching the out-of-focus region are "ignore" (no partial cells in the truth)
    if ignore.any():
        touched = np.unique(ids[ignore & (ids > 0)])
        drop = np.isin(ids, touched) & (ids > 0)
        ignore = ignore | drop
        ids = np.where(drop, 0, ids)
        uniq = np.unique(ids[ids > 0])
        remap = np.zeros(ids.max() + 1, np.int32)
        remap[uniq] = np.arange(1, uniq.size + 1)
        ids = remap[ids]
    mask = np.zeros((h, w), np.uint8)
    mask[ids > 0] = 1
    mask[not_cell & ~ignore] = 2
    mask[ignore] = 3
    kind = np.where(mask == 0, np.where(across, 2, 1), 0).astype(np.uint8)

    flakes = []
    for fid, sl in enumerate(ndi.find_objects(ids), start=1):
        if sl is None:
            continue
        rr, cc = np.nonzero(ids[sl] == fid)
        rr, cc = rr + sl[0].start, cc + sl[1].start
        theta, major, minor = moments_orientation(rr, cc)
        flakes.append({
            "flake": fid, "orientation_deg": round(float(theta), 3), "area_px": int(rr.size),
            "major_px": round(float(major), 3), "minor_px": round(float(minor), 3),
            "aspect_ratio": round(float(major / minor), 4) if minor > 0 else float("nan"),
            "touches_edge": bool(rr.min() == 0 or cc.min() == 0 or rr.max() == h - 1 or cc.max() == w - 1),
            "crossed_by_scratch": bool(scratch[rr, cc].any()),
        })
    return {"image": image, "flake_ids": ids, "mask": mask, "scratch": scratch, "scratch_crossing": crossing,
            "scratch_lines": lines, "flakes": flakes, "boundary_kind": kind, "pocket": mask == 2, "ignore": mask == 3}


FAMILIES = {
    "clean": dict(),
    "scratched": dict(thick_probability=0.7),
    "soft": dict(soft=True),
}

# the base spec: CellSpec's defaults, calibrated by hand to data/cards/coarse_cells_example.json (JOURNAL 2026-11-18);
# src.adapt --calibration refits it to another card (fit_to_card)
BASE = {}


def default_specs(seed: int = 0, per_family: int = 6, holdout: int = 2, shape=(1101, 1376),
                  base: dict | None = None) -> list[CellSpec]:
    """Per family (clean, scratched, soft) `per_family` images; the last `holdout` of each are the holdout. Each image
    draws its own values around the base spec (fitted to a card), spread like the images of the card."""
    rng = np.random.default_rng(seed)
    b = {**BASE, **(base or {})}
    proto = replace(CellSpec("p", "p", 0), **b)
    specs = []
    for fam, extra in FAMILIES.items():
        for k in range(per_family):
            s = replace(
                proto, name=f"{fam}_{k}", family=fam, seed=int(rng.integers(0, 2 ** 31)), shape=shape,
                mean_direction_deg=float(rng.choice([90.0, rng.uniform(0, 180)])),
                cell_width_px=proto.cell_width_px * float(np.exp(rng.normal(0, 0.05))),
                aspect_ratio=proto.aspect_ratio * float(np.exp(rng.normal(0, 0.03))),
                waviness_px=float(rng.uniform(0.6, 1.4) * proto.waviness_px),
                boundary_px=int(rng.integers(3, 5)),
                interior_level=float(rng.uniform(0.96, 0.99)),
                contrast_median=proto.contrast_median * float(np.exp(rng.normal(0, 0.10))),
                gap_fraction_along=float(np.clip(proto.gap_fraction_along * np.exp(rng.normal(0, 0.10)), 0, 0.6)),
                gap_fraction_across=float(np.clip(proto.gap_fraction_across * np.exp(rng.normal(0, 0.10)), 0, 0.6)),
                pocket_fraction=float(proto.pocket_fraction * np.exp(rng.normal(0, 0.4))),
                dots_per_mpx=float(proto.dots_per_mpx * np.exp(rng.normal(0, 0.4))),
                n_hairline=int(rng.integers(0, 2)) if fam != "scratched" else int(rng.integers(2, 5)),
                n_debris=int(rng.integers(0, 3)),
                thick_angle_deg=(float(rng.uniform(15, 25)) if rng.uniform() < 0.5 else None),
                blur_px=float(proto.blur_px * rng.uniform(0.8, 1.3)),
                illumination=float(rng.uniform(0.7, 1.3) * proto.illumination),
                noise=float(proto.noise * np.exp(rng.normal(0, 0.2))), **extra)
            specs.append(s)
    return specs


def write_dataset(out: Path, specs: list[CellSpec], holdout: int = 2, per_family: int = 6) -> Path:
    """images/, truth/{flake_ids,masks,scratch,gb_alpha}/, truth/flakes.csv, truth/images.csv, split.json
    (the layout of src/synth_lamellar.py, so src/evaluate.py scores it, plus the masks of src/score_masks.py)."""
    out = Path(out)
    for sub in ("images", "truth/flake_ids", "truth/masks", "truth/scratch", "truth/gb_alpha"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    flake_rows, image_rows = [], []
    for spec in specs:
        g = generate(spec)
        Image.fromarray(g["image"]).save(out / "images" / f"{spec.name}.png")
        Image.fromarray(g["flake_ids"].astype(np.uint16)).save(out / "truth" / "flake_ids" / f"{spec.name}.png")
        Image.fromarray(g["mask"]).save(out / "truth" / "masks" / f"{spec.name}.png")
        code = np.where(g["scratch_crossing"], 255, np.where(g["scratch"], 128, 0)).astype(np.uint8)
        Image.fromarray(code).save(out / "truth" / "scratch" / f"{spec.name}.png")
        Image.fromarray(np.zeros(g["mask"].shape, np.uint8)).save(out / "truth" / "gb_alpha" / f"{spec.name}.png")
        flake_rows += [{"image": spec.name, **f} for f in g["flakes"]]
        row = {k: (str(v) if isinstance(v, tuple) else v) for k, v in asdict(spec).items()}
        row.update(image=row.pop("name"), n_scratch_lines=len(g["scratch_lines"]),
                   ignore_fraction=round(float(g["ignore"].mean()), 4), pocket_fraction_area=round(float(g["pocket"].mean()), 4))
        image_rows.append(row)
    pd.DataFrame(flake_rows).to_csv(out / "truth" / "flakes.csv", index=False)
    pd.DataFrame(image_rows).to_csv(out / "truth" / "images.csv", index=False)
    hold = sorted(s.name for s in specs if int(s.name.rsplit("_", 1)[1]) >= per_family - holdout)
    dev = sorted(s.name for s in specs if s.name not in hold)
    (out / "split.json").write_text(json.dumps({"dev": dev, "holdout": hold}, indent=2) + "\n")
    return out


# --- fitting to a calibration card, and a twin for src.adapt ------------------------------------------------------

def _mid(rng):
    return None if not rng else 0.5 * (float(rng[0]) + float(rng[1]))


FIT_KEYS = {  # card key -> (spec field, exponent of the damped update: field *= (target / measured) ** exponent)
    "cell_width_px": ("cell_width_px", 0.6),
    "cell_aspect_ratio": ("aspect_ratio", 0.6),
    "contrast_median": ("contrast_median", 0.6),
    "invisible_fraction_along": ("gap_fraction_along", 0.6),
    "invisible_fraction_across": ("gap_fraction_across", 0.6),
    "noise": ("noise", 0.6),
    "dots_per_megapixel": ("dots_per_mpx", 0.5),
    "pocket_fraction": ("pocket_fraction", 0.5),
    "illumination_range": ("illumination", 0.6),
}
FIT_LIMITS = {"cell_width_px": (15.0, 400.0), "aspect_ratio": (1.5, 8.0), "contrast_median": (0.02, 0.6),
              "gap_fraction_along": (0.0, 0.7), "gap_fraction_across": (0.0, 0.7), "noise": (0.002, 0.08),
              "dots_per_mpx": (0.0, 400.0), "pocket_fraction": (0.0, 0.1), "illumination": (0.0, 0.3),
              "soft_base_blur_px": (0.6, 4.0)}


RANGE_SLACK = 0.25  # a card range widened by this share of its width on each side: the twin's median must lie inside


def _outside(m: float, rng) -> float:
    """|log| distance of m to the card range widened by RANGE_SLACK (0 inside)."""
    lo, hi = float(rng[0]), float(rng[1])
    pad = RANGE_SLACK * (hi - lo)
    lo, hi = max(lo - pad, 1e-9), hi + pad
    return 0.0 if lo <= m <= hi else float(abs(np.log(max(m, 1e-9) / (lo if m < lo else hi))))


def card_mismatch(twin_card: dict, target: dict) -> dict:
    """Per card key: the twin's median over its images (sharp ones for the sharp-only keys, soft ones for the soft
    keys), the target's range, |log| of the twin median over the range's middle, and "outside": its |log| distance to
    the range widened by RANGE_SLACK (0 inside). Card ranges are wide (dots 5-53 per megapixel): a value inside the
    range is a match, however far from the middle."""
    from src import calibration_card as cc

    rows = [cc._flat(r) for r in twin_card["per_image"]]
    out = {}
    for k in cc.SUMMARY_KEYS:
        group = [r for r in rows if not r["soft"]] if k in cc.SHARP_ONLY else rows
        vals = [r[k] for r in group if r.get(k) is not None]
        t = target["summary"].get(k)
        if vals and t:
            m = float(np.median(vals))
            out[k] = {"twin": round(m, 4), "target": t, "log_ratio": round(abs(float(np.log(max(m, 1e-9) / max(_mid(t), 1e-9)))), 3),
                      "inside": bool(t[0] <= m <= t[1]), "outside": round(_outside(m, t), 3)}
    soft_rows = [r for r in rows if r["soft"]]
    for k in ("contrast_median", "ignore_fraction"):
        t = (target.get("soft") or {}).get(k)
        vals = [r[k] for r in soft_rows if r.get(k) is not None]
        if vals and t:
            m = float(np.median(vals))
            out[f"soft_{k}"] = {"twin": round(m, 4), "target": t, "inside": bool(t[0] <= m <= t[1]),
                                "log_ratio": round(abs(float(np.log(max(m, 1e-9) / max(_mid(t), 1e-9)))), 3),
                                "outside": round(_outside(m, t), 3)}
    return out


def fit_to_card(card: dict, seed: int = 0, iters: int = 3, log=print) -> tuple[dict, list]:
    """A base spec whose images, measured by src.calibration_card with their own masks, match the card: previews
    (two clean, two scratched, two soft images) are generated and measured, and each fitted field outside the card's
    range (widened by RANGE_SLACK) is scaled by a damped power of the ratio of the range's middle to the measured
    value (FIT_KEYS), within FIT_LIMITS (undamped steps on three previews overshot: x1.43 -> x1.84). The iteration
    with the smallest worst distance to the ranges is returned."""
    from src import calibration_card as cc

    shape = tuple(card.get("image_shape") or CellSpec("p", "p", 0).shape)
    base = {k: v for k, v in asdict(CellSpec("p", "p", 0)).items() if k not in ("name", "family", "seed", "shape")}
    base.update(BASE)
    history, best = [], None
    for it in range(iters):
        specs = default_specs(seed + 101 * it, per_family=2, holdout=0, shape=shape, base=base)
        items = []
        for sp in specs:
            g = generate(sp)
            items.append((sp.name, g["image"] / 255.0, g["mask"]))
        mis = card_mismatch(cc.make_card(items), card)
        worst = max((v["outside"] for k, v in mis.items() if k in FIT_KEYS or k == "soft_contrast_median"), default=0.0)
        history.append({"iter": it, "worst_log_ratio": round(worst, 3), "mismatch": mis,
                        "base": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in base.items()
                                 if k in FIT_LIMITS}})
        log(f"    twin fit {it + 1}/{iters}: worst distance to the card's ranges x{np.exp(worst):.2f}")
        if best is None or worst < best[0]:
            best = (worst, dict(base))
        for key, (field, power) in FIT_KEYS.items():
            if key in mis and mis[key]["twin"] > 0 and _mid(card["summary"].get(key)) and mis[key]["outside"] > 0:
                f = (_mid(card["summary"][key]) / mis[key]["twin"]) ** power
                lo, hi = FIT_LIMITS[field]
                base[field] = float(np.clip(base[field] * f, lo, hi))
        if "soft_contrast_median" in mis and mis["soft_contrast_median"]["twin"] > 0 \
                and mis["soft_contrast_median"]["outside"] > 0:
            t = _mid(card["soft"]["contrast_median"])  # softer best-focused side -> fainter readable lines
            base["soft_base_blur_px"] = float(np.clip(base["soft_base_blur_px"] * (mis["soft_contrast_median"]["twin"] / t) ** 0.7,
                                                      *FIT_LIMITS["soft_base_blur_px"]))
    return best[1], history


def generator_version() -> str:
    import hashlib

    here = Path(__file__).resolve().parent
    return hashlib.sha256(b"".join((here / f).read_bytes() for f in ("synth_cells.py", "calibration_card.py"))).hexdigest()[:10]


def build_cells_twin(out: Path, card: dict | None = None, width_px: float | None = None, shape=None,
                     per_family: int = 3, holdout: int = 1, seed: int = 0, log=print) -> dict:
    """A coarse-cell twin for src.adapt: fitted to a calibration card when there is one (fit_to_card), otherwise the
    calibrated base spec with the cells drawn at the measured width. Writes the dataset (as write_dataset) and
    twin_info.json; realism: the twin's own card against the target card (the twin's median of each fitted key inside
    the card's range widened by RANGE_SLACK, within x1.35), or, without a card, the width alone (within x1.35)."""
    from src import calibration_card as cc

    out = Path(out)
    history = []
    if card:
        base, history = fit_to_card(card, seed=seed, log=log)
        shape = tuple(card.get("image_shape") or shape or CellSpec("p", "p", 0).shape)
    else:
        base = dict(BASE)
        if width_px:
            base["cell_width_px"] = float(width_px)
    shape = tuple(shape or CellSpec("p", "p", 0).shape)
    specs = default_specs(seed, per_family=per_family, holdout=holdout, shape=shape, base=base)
    write_dataset(out, specs, holdout=holdout, per_family=per_family)
    split = json.loads((out / "split.json").read_text())
    twin_card = cc.card_from_dirs(out / "images", out / "truth" / "masks")
    realism, worst = {}, 0.0
    if card:
        realism = card_mismatch(twin_card, card)
        worst = max((v["outside"] for k, v in realism.items() if k in FIT_KEYS), default=0.0)
    elif width_px:
        tw = _mid(twin_card["summary"]["cell_width_px"])
        worst = abs(float(np.log(tw / width_px))) if tw else float("inf")
        realism = {"cell_width_px": {"twin": tw, "target": width_px, "log_ratio": round(worst, 3)}}
    info = {"family": "cells", "mode": "boundaries", "mode_from_profile": "boundaries", "flake_phase": "bright",
            "shape": list(shape), "n_images": len(specs), "dev": split["dev"], "holdout": split["holdout"],
            "base_spec": base, "fit_history": history, "realism": realism,
            "realism_worst_log_ratio": round(float(worst), 3), "structure_worst_log_ratio": 0.0,
            "realism_ok": bool(worst <= np.log(1.35)), "card": bool(card), "upsample": 1, "overrides": {},
            "generator_version": generator_version(), "twin_card_summary": twin_card["summary"],
            "twin_card_soft": twin_card["soft"], "width_px": width_px}
    (out / "twin_info.json").write_text(json.dumps(info, indent=2, default=float) + "\n")
    return info


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", default="0", help='an integer, or "entropy" for a blind set (the seed is not printed)')
    ap.add_argument("--per-family", type=int, default=6)
    ap.add_argument("--holdout", type=int, default=2)
    a = ap.parse_args(argv)
    seed = secrets.randbits(63) if a.seed == "entropy" else int(a.seed)
    write_dataset(a.out, default_specs(seed, a.per_family, a.holdout), a.holdout, a.per_family)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()

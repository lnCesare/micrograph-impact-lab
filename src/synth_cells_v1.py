"""First coarse-cell twin family (2026-11-17, uncalibrated; superseded by src/synth_cells.py, kept for the tests
and the results made with it): code-generated optical micrographs of a coarse, elongated cell structure, with exact
per-cell truth. Built from the description of the scientist's private test images (which cannot be shared):

- grayscale, ~1400 x 1100 px, ~100-150 bright cells separated by thin dark boundaries (2-5 px);
- cells ~60-70 px wide, aspect ratio ~2.5-3.5, brick / lens shaped with rounded ends, well aligned (S ~0.75-0.9),
  stacked end to end in rows; boundaries curved in places;
- boundaries faint and broken (dotted, gaps); the transverse boundaries between cells stacked end to end the faintest;
- grey speckled "pockets" at triple junctions (~0.5-2 cell widths): not cells;
- small dark round dots / pores inside cells;
- polishing scratches: hairline, faint, straight, hundreds of px long, at any angle (often ~45 deg to the cells),
  crossing many cells; occasionally a thick dark one;
- uneven illumination; some images with an out-of-focus region (up to half the image).

Truth per image: flake_ids (cells 1..N, 0 elsewhere), mask (0 boundary, 1 cell, 2 pocket, 3 ignore = out of focus;
the convention of the private test's hand-corrected masks), scratch (255 crossing the cells, 128 along them), and a
per-cell table (orientation and axes from the cell's second moments). Cells inside the out-of-focus region are left
out of the truth (mask 3), as the private masks mark such regions "ignore".

    python -m src.synth_cells_v1 --out data/coarse_cells [--seed 0 | --seed entropy]
"""

import argparse
import json
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
from scipy import ndimage as ndi

from src.axial import moments_orientation

PARALLEL_DEG = 15  # a scratch within this angle of the local cells cannot be told from a boundary by geometry


@dataclass
class CellSpec:
    name: str
    family: str
    seed: int
    shape: tuple = (1100, 1400)
    mean_direction_deg: float = 20.0
    n_domains: int = 1                  # >1: Voronoi domains of slightly different direction (straight edges)
    domain_kappa: float = 10.0          # concentration of the domains' directions (axial von Mises)
    cell_width_px: tuple = (55, 75)
    aspect_ratio: tuple = (2.4, 3.6)
    corner_radius: tuple = (0.3, 0.8)   # rounded ends: corner radius as a fraction of half the cell width
    boundary_px: float = 3.0
    waviness_px: float = 25.0           # curvature of the rows (sets most of the spread of directions)
    long_darkness: tuple = (0.30, 0.45)  # boundaries along the cells: how much darker than the cells
    trans_darkness: tuple = (0.10, 0.22)  # boundaries across the cells (end to end): fainter
    long_gap_fraction: float = 0.2      # broken boundaries: fraction of boundary drawn at (nearly) cell level
    trans_gap_fraction: float = 0.45
    pocket_fraction: float = 0.12       # fraction of the triple junctions that hold a pocket
    pocket_size: tuple = (0.25, 1.0)    # pocket radius in cell widths (diameter ~0.5-2 cell widths)
    dots_per_cell: float = 1.5
    n_hairline: int = 0
    thick_probability: float = 0.0
    blur: bool = False
    illumination: float = 0.2
    noise: float = 0.025


def _partition(rng, lo, hi, size_range, gap):
    edges = [lo - rng.uniform(0, size_range[1])]
    while edges[-1] < hi:
        edges.append(edges[-1] + rng.uniform(*size_range) + gap)
    return np.array(edges)


def _axial_von_mises(rng, mean_deg, kappa, n):
    return (mean_deg + np.rad2deg(rng.vonmises(0.0, kappa, n)) / 2) % 180


def generate(spec: CellSpec) -> dict:
    rng = np.random.default_rng(spec.seed)
    h, w = spec.shape
    rows, cols = np.mgrid[0:h, 0:w].astype(float)
    b = float(spec.boundary_px)

    # domains (a few, with nearby directions)
    seeds = np.column_stack([rng.uniform(0, h, spec.n_domains), rng.uniform(0, w, spec.n_domains)])
    d2 = (rows[..., None] - seeds[:, 0]) ** 2 + (cols[..., None] - seeds[:, 1]) ** 2
    domain = np.argmin(d2, axis=-1)
    del d2
    angles = _axial_von_mises(rng, spec.mean_direction_deg, spec.domain_kappa, spec.n_domains)

    key = np.full((h, w), -1, np.int64)      # cell key (domain, row, segment) for cell pixels
    kind = np.zeros((h, w), np.uint8)         # 1 boundary along the cells, 2 across them (end to end)
    junctions = []                            # (row, col) of triple junctions: cell ends on a row edge
    local_dir = np.zeros((h, w))
    for d in range(spec.n_domains):
        sel = domain == d
        r, q = rows[sel], cols[sel]
        t = np.deg2rad(angles[d])
        u = q * np.cos(t) - r * np.sin(t)          # along the cells (y up)
        v = q * np.sin(t) + r * np.cos(t)          # across them
        lam, phase = rng.uniform(450, 800), rng.uniform(0, 2 * np.pi)
        lam2, phase2, amp2 = rng.uniform(1100, 1800), rng.uniform(0, 2 * np.pi), rng.uniform(0.5, 1.5)
        wave = lambda uu: (spec.waviness_px * np.sin(2 * np.pi * uu / lam + phase)
                           + amp2 * spec.waviness_px * np.sin(2 * np.pi * uu / lam2 + phase2))
        v = v + wave(u)
        slope = (spec.waviness_px * 2 * np.pi / lam * np.cos(2 * np.pi * u / lam + phase)
                 + amp2 * spec.waviness_px * 2 * np.pi / lam2 * np.cos(2 * np.pi * u / lam2 + phase2))
        local_dir[sel] = (angles[d] + np.degrees(np.arctan(slope))) % 180  # (sign fixed 2026-11-18)
        v_edges = _partition(rng, v.min(), v.max(), spec.cell_width_px, b)
        stripe = np.searchsorted(v_edges, v) - 1
        dv = np.minimum(v - v_edges[stripe], v_edges[stripe + 1] - v) - b / 2  # > 0 inside the row
        k_sel = np.full(r.size, -1, np.int64)
        kd = np.zeros(r.size, np.uint8)
        for s in np.unique(stripe):
            m = stripe == s
            width = v_edges[s + 1] - v_edges[s] - b
            length_range = (spec.aspect_ratio[0] * width, spec.aspect_ratio[1] * width)
            u_edges = _partition(rng, u[m].min(), u[m].max(), length_range, b)
            seg = np.searchsorted(u_edges, u[m]) - 1
            du = np.minimum(u[m] - u_edges[seg], u_edges[seg + 1] - u[m]) - b / 2
            rad = rng.uniform(*spec.corner_radius, len(u_edges)) * width / 2
            rc = rad[seg]
            dvm = dv[m]
            corner = (du < rc) & (dvm < rc)
            inside = (du > 0) & (dvm > 0) & (~corner | ((rc - du) ** 2 + (rc - dvm) ** 2 <= rc ** 2))
            k_sel_m = np.where(inside, (d * 1000 + int(s)) * 10000 + seg, -1)
            k_sel[np.flatnonzero(m)] = k_sel_m
            kd[np.flatnonzero(m)] = np.where(inside, 0, np.where(dvm <= 0, 1, np.where(du <= 0, 2, 1)))
            # junctions: where a cell end (u edge) meets this row's edges, in image coordinates
            v_lo, v_hi = v_edges[s], v_edges[s + 1]
            for ue in u_edges[1:-1]:
                for ve in (v_lo, v_hi):
                    if rng.uniform() < 0.5:  # one side is enough, the other row's ends are staggered
                        continue
                    vt = ve - wave(ue)  # undo the waviness
                    x = ue * np.cos(t) + vt * np.sin(t)
                    y = -ue * np.sin(t) + vt * np.cos(t)
                    if 0 <= y < h and 0 <= x < w:
                        junctions.append((y, x, d))
        key[sel] = k_sel
        kind[sel] = kd
    # domain edges are boundaries along the cells
    edge = (ndi.grey_dilation(domain, size=3) != ndi.grey_erosion(domain, size=3))
    key[edge] = -1
    kind[edge] = 1

    # pockets: textured blobs at some triple junctions; they take the place of cells and boundaries
    pocket = np.zeros((h, w), bool)
    mean_width = float(np.mean(spec.cell_width_px))
    for (y, x, d) in junctions:
        if domain[int(y), int(x)] != d or rng.uniform() >= spec.pocket_fraction:
            continue
        rad = rng.uniform(*spec.pocket_size) * mean_width
        blob = np.zeros((h, w), bool)
        for _ in range(int(rng.integers(2, 5))):  # an irregular blob: a few overlapping discs
            cy, cx = y + rng.normal(0, 0.35 * rad), x + rng.normal(0, 0.35 * rad)
            rr_ = rad * rng.uniform(0.5, 0.9)
            y0, y1 = int(max(cy - rr_, 0)), int(min(cy + rr_ + 1, h))
            x0, x1 = int(max(cx - rr_, 0)), int(min(cx + rr_ + 1, w))
            if y1 <= y0 or x1 <= x0:
                continue
            yy, xx = np.mgrid[y0:y1, x0:x1]
            blob[y0:y1, x0:x1] |= (yy - cy) ** 2 + (xx - cx) ** 2 <= rr_ ** 2
        pocket |= blob
    # cells: unique ids; what a pocket leaves of a cell is absorbed by the pocket when it is a small remnant
    cells = (key >= 0) & ~pocket
    _, inv = np.unique(np.where(cells, key, -1), return_inverse=True)
    ids = inv.reshape(h, w).astype(np.int32)  # 0 = the -1 key (not a cell)
    ids = np.where(cells, ids, 0)
    area = np.bincount(ids.ravel())
    # area each id had before the pockets
    before = np.zeros(area.size)
    key_of = np.zeros(area.size, np.int64)
    key_of[ids[cells]] = key[cells]
    keys_all, counts_all = np.unique(key[key >= 0], return_counts=True)
    lookup = dict(zip(keys_all.tolist(), counts_all.tolist()))
    for i in range(1, area.size):
        before[i] = lookup.get(int(key_of[i]), area[i])
    remnant = (area < 0.25 * np.maximum(before, 1)) | (area < 150)
    remnant[0] = False
    absorbed = remnant[ids] & (ids > 0)
    pocket |= absorbed & ndi.binary_dilation(pocket, iterations=8)
    ids[absorbed] = 0
    # a cell split in two by a pocket: keep the largest piece as the cell, the rest goes to the pocket
    for i, sl in enumerate(ndi.find_objects(ids), start=1):
        if sl is None:
            continue
        sub = ids[sl] == i
        lab, n = ndi.label(sub)
        if n > 1:
            sizes = np.bincount(lab.ravel())[1:]
            drop = sub & (lab != np.argmax(sizes) + 1)
            ids[sl][drop] = 0
            pocket[sl] |= drop
    # relabel 1..N
    uniq = np.unique(ids[ids > 0])
    remap = np.zeros(ids.max() + 1, np.int32)
    remap[uniq] = np.arange(1, uniq.size + 1)
    ids = remap[ids]

    # render
    cell_level = 0.78
    shade = cell_level + rng.normal(0, 0.03, ids.max() + 1)
    img = np.full((h, w), cell_level)
    img[ids > 0] = shade[ids[ids > 0]]
    boundary = (ids == 0) & ~pocket
    # boundary darkness: along the cells darker, across fainter; broken by gaps (dotted)
    long_dark = rng.uniform(*spec.long_darkness)
    trans_dark = rng.uniform(*spec.trans_darkness)
    gap_noise = ndi.gaussian_filter(rng.normal(0, 1, (h, w)), 3.0)
    gap_noise = (gap_noise - gap_noise.mean()) / gap_noise.std()
    long_gap = gap_noise > np.quantile(gap_noise, 1 - spec.long_gap_fraction)
    trans_gap = gap_noise > np.quantile(gap_noise, 1 - spec.trans_gap_fraction)
    bk = np.where(boundary, np.maximum(kind, 1), 0).astype(np.uint8)
    darkness = np.where(bk == 2, trans_dark, long_dark)
    # the small wedges left between rounded cell ends: junction nodes, a little darker than the faint boundaries
    rad_w = int(np.ceil(b / 2)) + 1
    yy_, xx_ = np.mgrid[-rad_w:rad_w + 1, -rad_w:rad_w + 1]
    wedge = ndi.binary_opening(boundary, structure=(yy_ ** 2 + xx_ ** 2 <= rad_w ** 2))  # thicker than a line
    darkness = np.where(wedge & (bk != 1), 0.5 * (long_dark + trans_dark), darkness)
    gap = np.where(bk == 2, trans_gap, long_gap)
    darkness = np.where(gap, 0.04, darkness)
    img[boundary] = cell_level - darkness[boundary]
    # pockets: grey, speckled
    if pocket.any():
        speckle = ndi.gaussian_filter(rng.normal(0, 1, (h, w)), 1.2)
        speckle = speckle / speckle.std()
        grey = cell_level - 0.45 * long_dark + 0.10 * speckle
        specks = ndi.binary_dilation(rng.uniform(size=(h, w)) < 0.012)
        grey = np.where(specks, cell_level - 0.8 * long_dark, grey)
        img[pocket] = grey[pocket]
    # dots / pores inside cells
    n_cells = int(ids.max())
    cell_pixels = np.flatnonzero(ids.ravel() > 0)
    n_dots = int(rng.poisson(spec.dots_per_cell * n_cells)) if cell_pixels.size else 0
    for p_ in rng.choice(cell_pixels, size=min(n_dots, cell_pixels.size), replace=False) if n_dots else []:
        cy, cx = divmod(int(p_), w)
        rad = rng.uniform(1.5, 4.0)
        y0, y1, x0, x1 = max(cy - 5, 0), min(cy + 6, h), max(cx - 5, 0), min(cx + 6, w)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= rad ** 2
        img[y0:y1, x0:x1][disc] = rng.uniform(0.2, 0.4)
    img = ndi.gaussian_filter(img, 0.9)

    # scratches
    scratch = np.zeros((h, w), bool)
    crossing = np.zeros((h, w), bool)
    lines = []
    n_thick = int(rng.uniform() < spec.thick_probability)
    for k in range(spec.n_hairline + n_thick):
        thick = k >= spec.n_hairline
        if rng.uniform() < 0.5:
            angle = (spec.mean_direction_deg + rng.choice([-45, 45]) + rng.normal(0, 8)) % 180
        else:
            angle = rng.uniform(0, 180)
        t = np.deg2rad(angle)
        r0, c0 = rng.uniform(0.25 * h, 0.75 * h), rng.uniform(0.25 * w, 0.75 * w)
        dist = np.abs((cols - c0) * np.sin(t) + (rows - r0) * np.cos(t))
        along = (cols - c0) * np.cos(t) - (rows - r0) * np.sin(t)
        half = rng.uniform(150, 650) if not thick else rng.uniform(250, 700)
        on = np.abs(along) < half
        width = rng.uniform(4.0, 6.0) if thick else rng.uniform(1.0, 1.6)
        profile = np.exp(-0.5 * (dist / (width / 2)) ** 2) * on
        delta = -rng.uniform(0.25, 0.35) if thick else rng.choice([-1, 1], p=[0.75, 0.25]) * rng.uniform(0.05, 0.12)
        img = img + delta * profile
        this = (dist <= width / 2 + 0.5) & on
        length = float(2 * half)
        # inside the image only
        ys, xs = np.nonzero(this)
        if ys.size:
            length = float(np.hypot(ys.max() - ys.min(), xs.max() - xs.min()))
        scratch |= this
        dd = np.abs((angle - local_dir) % 180)
        crossing |= this & (np.minimum(dd, 180 - dd) >= PARALLEL_DEG)
        lines.append({"direction_deg": float(angle), "length_px": round(length, 1), "thick": bool(thick),
                      "bright": bool(delta > 0), "width_px": round(float(width), 2)})

    # uneven illumination (gradient + vignetting), out-of-focus region, noise
    g_angle = rng.uniform(0, 2 * np.pi)
    gradient = (cols / w - 0.5) * np.cos(g_angle) + (rows / h - 0.5) * np.sin(g_angle)
    vignette = -((cols / w - 0.5) ** 2 + (rows / h - 0.5) ** 2)
    img = img * (1 + spec.illumination * (gradient + 0.8 * vignette))
    ignore = np.zeros((h, w), bool)
    if spec.blur:
        b_angle = rng.uniform(0, 2 * np.pi)
        proj = (cols - w / 2) * np.cos(b_angle) + (rows - h / 2) * np.sin(b_angle)
        cut = np.quantile(proj, 1 - rng.uniform(0.2, 0.5))  # 20-50 % of the image out of focus
        weight = np.clip((proj - cut) / 40 + 0.5, 0, 1)
        blurred = ndi.gaussian_filter(img, rng.uniform(3.5, 6.0))
        img = (1 - weight) * img + weight * blurred
        ignore = weight > 0.5
    img = img + rng.normal(0, spec.noise, img.shape)
    image = (np.clip(img, 0, 1) * 255).astype(np.uint8)

    # truth: cells mostly in the out-of-focus region are left out (mask 3 there)
    if ignore.any():
        frac = ndi.mean(ignore.astype(float), ids, index=np.arange(ids.max() + 1))
        drop = np.asarray(frac) > 0.0
        drop[0] = False
        ids = np.where(drop[ids], 0, ids)
        uniq = np.unique(ids[ids > 0])
        remap = np.zeros(ids.max() + 1, np.int32)
        remap[uniq] = np.arange(1, uniq.size + 1)
        ids = remap[ids]
    mask = np.zeros((h, w), np.uint8)
    mask[ids > 0] = 1
    mask[pocket & ~ignore] = 2
    mask[ignore] = 3
    # cells cut by the out-of-focus border: their in-focus part is "ignore" as well (no partial cells in the truth)
    removed = (ids == 0) & ~pocket & ~ignore & (bk == 0)
    mask[removed] = 3

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
            "scratch_lines": lines, "flakes": flakes, "boundary_kind": np.where(mask == 0, bk, 0).astype(np.uint8),
            "pocket": mask == 2, "ignore": mask == 3, "domain_angles": angles}


FAMILIES = {
    "clean": dict(),
    "scratched": dict(n_hairline=3, thick_probability=0.5),
    "blurred": dict(n_hairline=1, blur=True),
}


def default_specs(seed: int = 0, per_family: int = 6, holdout: int = 2, shape=(1100, 1400)) -> list[CellSpec]:
    """Per family (clean, scratched, blurred) `per_family` images; the last `holdout` of each are the holdout."""
    rng = np.random.default_rng(seed)
    specs = []
    for fam, extra in FAMILIES.items():
        for k in range(per_family):
            specs.append(CellSpec(
                name=f"{fam}_{k}", family=fam, seed=int(rng.integers(0, 2 ** 31)), shape=shape,
                mean_direction_deg=float(rng.uniform(0, 180)), boundary_px=float(rng.uniform(2.0, 5.0)),
                waviness_px=float(rng.uniform(25, 45)), long_gap_fraction=float(rng.uniform(0.1, 0.3)),
                trans_gap_fraction=float(rng.uniform(0.3, 0.6)), pocket_fraction=float(rng.uniform(0.08, 0.2)),
                illumination=float(rng.uniform(0.1, 0.3)), noise=float(rng.uniform(0.015, 0.035)), **extra))
    return specs


def write_dataset(out: Path, specs: list[CellSpec], holdout: int = 2, per_family: int = 6) -> Path:
    """images/, truth/{flake_ids,masks,scratch,gb_alpha}/, truth/flakes.csv, truth/images.csv, split.json.
    The layout of src/synth_lamellar.py (so src/evaluate.py scores it), plus the masks of the private test."""
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

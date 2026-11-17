"""Code-generated lamellar micrographs with exact per-flake ground truth: the flake testbed.

Unlike the AI-generated synthetic grain set, every image here comes from this code and a seed,
so the set can be regenerated anywhere and the truth is exact by construction.
It is a testbed, not evidence about real metals.

Each image mimics a lamellar (colony / basketweave) alpha + beta structure such as Ti-6Al-4V:
- prior grains ("colonies") are Voronoi cells, each with one lath direction drawn from an axial
  von Mises distribution around a common mean direction (concentration kappa: 0 = random, large = aligned);
- inside a colony, bright laths (the flakes) of random width and length, separated by thin dark boundaries;
- along some colony boundaries, a continuous grain-boundary alpha layer (thick, long, curved; not a flake, not scored);
- corruptions: gaps in the boundaries (incomplete etching), straight polishing scratches (dark or light)
  that cross flakes, small dark pits, blur, uneven illumination, noise, and optionally inverted contrast
  (bright boundaries, as in backscattered-electron images).

Writes, under the output folder:
    images/<name>.png                  the micrograph (the only thing a method sees)
    truth/flake_ids/<name>.png         uint16 flake id per pixel, 0 = boundary / not a flake
    truth/scratch/<name>.png           255 where a scratch crosses the laths (>= 15 deg), 128 where it runs along them
    truth/gb_alpha/<name>.png          255 on grain-boundary alpha (excluded from scoring)
    truth/flakes.csv                   one row per flake: image, flake, orientation_deg (designed), area_px,
                                       major_px, minor_px, aspect_ratio, touches_edge, crossed_by_scratch
    truth/images.csv                   one row per image: generation parameters and family

    python -m src.synth_lamellar --out data/lamellar_synth
"""

import argparse
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
from scipy import ndimage as ndi

from src.axial import moments_orientation

PARALLEL_DEG = 15.0  # scratch pixels closer than this to the local lath direction are scored apart


@dataclass
class Spec:
    name: str
    family: str
    seed: int
    shape: tuple = (768, 1024)
    kappa: float = 4.0            # concentration of colony directions (axial von Mises); 0 = isotropic
    mean_direction_deg: float = 30.0
    n_colonies: int = 12
    lath_width_px: tuple = (6, 14)
    lath_length_px: tuple = (40, 180)
    boundary_px: float = 2.0
    waviness_px: float = 2.0      # amplitude of a slow sinusoidal bend of the laths (keeps the mean direction)
    gap_fraction: float = 0.08    # fraction of boundary length bridged by etching gaps
    gb_alpha_fraction: float = 0.4  # fraction of colony boundaries carrying a grain-boundary alpha layer
    n_scratches: int = 0
    scratch_width_px: float = 1.5
    n_pits: int = 30
    noise: float = 0.03
    blur_px: float = 0.8
    illumination: float = 0.15
    inverted: bool = False
    style: str = "network"        # "network": laths separated by thin boundaries; "islands": laths of one phase in a matrix;
                                  # "regions": touching laths told apart only by their grey level (BSE of TiAl lamellae)
    n_levels: int = 4             # regions: grey levels (even: neighbouring stripes use different pairs)
    island_fill: float = 0.6      # islands: fraction of the lath positions that hold a flake
    matrix_gap_px: float = 4.0    # islands: extra matrix between neighbouring flakes
    flake_dark: bool = True       # islands: flakes darker than the matrix
    tags: dict = field(default_factory=dict)


def _axial_von_mises(rng, mean_deg, kappa, n):
    if kappa <= 0:
        return rng.uniform(0, 180, n)
    return (mean_deg + np.rad2deg(rng.vonmises(0.0, kappa, n)) / 2) % 180


def _partition(rng, lo, hi, size_range, boundary):
    """Cut edges along [lo, hi]: random piece sizes, each followed by a boundary of `boundary` px."""
    edges = [lo - rng.uniform(0, size_range[1])]
    while edges[-1] < hi:
        edges.append(edges[-1] + rng.uniform(*size_range) + boundary)
    return np.array(edges)


def generate(spec: Spec) -> dict:
    rng = np.random.default_rng(spec.seed)
    h, w = spec.shape
    rows, cols = np.mgrid[0:h, 0:w]

    # colonies: Voronoi cells
    seeds = np.column_stack([rng.uniform(0, h, spec.n_colonies), rng.uniform(0, w, spec.n_colonies)])
    d2 = (rows[..., None] - seeds[:, 0]) ** 2 + (cols[..., None] - seeds[:, 1]) ** 2
    colony = np.argmin(d2, axis=-1)
    angles = _axial_von_mises(rng, spec.mean_direction_deg, spec.kappa, spec.n_colonies)

    flake_ids = np.zeros((h, w), np.int32)
    design = {}
    stripe_of = {}  # lath id -> (colony, stripe index), for the alternating levels of the "regions" style
    next_id = 1
    half_b = spec.boundary_px / 2
    for c in range(spec.n_colonies):
        sel = colony == c
        r, q = rows[sel].astype(float), cols[sel].astype(float)
        t = np.deg2rad(angles[c])
        u = q * np.cos(t) - r * np.sin(t)       # along the laths (y up)
        v = q * np.sin(t) + r * np.cos(t)       # across the laths
        v = v + spec.waviness_px * np.sin(2 * np.pi * u / rng.uniform(250, 500) + rng.uniform(0, 6.3))
        v_edges = _partition(rng, v.min(), v.max(), spec.lath_width_px, spec.boundary_px)
        stripe = np.searchsorted(v_edges, v) - 1
        dv = np.minimum(v - v_edges[stripe], v_edges[stripe + 1] - v)
        ids = np.zeros(r.size, np.int32)
        for s in np.unique(stripe):
            m = stripe == s
            u_edges = _partition(rng, u[m].min(), u[m].max(), spec.lath_length_px, spec.boundary_px)
            seg = np.searchsorted(u_edges, u[m]) - 1
            du = np.minimum(u[m] - u_edges[seg], u_edges[seg + 1] - u[m])
            # rounded lath ends: a cap of radius = half the lath width
            hw = np.maximum((v_edges[s + 1] - v_edges[s] - spec.boundary_px) / 2, 1.0)
            cap = hw - np.sqrt(np.maximum(hw**2 - np.maximum(hw + half_b - dv[m], 0) ** 2, 0))
            inside = (dv[m] > half_b) & (du > half_b + cap)
            local = np.where(inside, seg + 1, 0)
            for k in np.unique(local[local > 0]):
                design[next_id] = float(angles[c])
                stripe_of[next_id] = (c, int(s), int(k))
                local[local == k] = -next_id
                next_id += 1
            ids[m] = -np.minimum(local, 0)
        flake_ids[sel] = ids

    # colony boundaries: 1 px wide dark line everywhere, a thick bright alpha layer on some of them
    colony_edge = (ndi.grey_dilation(colony, size=3) != ndi.grey_erosion(colony, size=3))
    flake_ids[colony_edge] = 0
    gb_alpha = np.zeros((h, w), bool)
    pairs = {}
    for a, b in zip(colony[:, :-1][colony_edge[:, :-1]], colony[:, 1:][colony_edge[:, :-1]]):
        if a != b:
            pairs[(min(a, b), max(a, b))] = True
    for a, b in pairs:
        if rng.uniform() < spec.gb_alpha_fraction:
            between = ndi.binary_dilation(colony == a, iterations=4) & ndi.binary_dilation(colony == b, iterations=4)
            gb_alpha |= between
    flake_ids[ndi.binary_dilation(gb_alpha, iterations=1)] = 0

    # drop tiny clipped pieces from the truth (they stay in the image as part of the boundary network)
    areas = np.bincount(flake_ids.ravel())
    tiny = areas < 12
    tiny[0] = False
    flake_ids[tiny[flake_ids]] = 0

    if spec.style == "islands":
        # keep a fraction of the laths as flakes of one phase, shrunk so that matrix separates them
        keep = rng.uniform(size=next_id) < spec.island_fill
        keep[0] = False
        kept = keep[flake_ids]
        half_gap = int(round(spec.matrix_gap_px / 2))
        if half_gap > 0:
            kept = ndi.binary_erosion(kept, iterations=half_gap)
        flake_ids = np.where(kept, flake_ids, 0)
        gb_alpha[:] = False
        areas = np.bincount(flake_ids.ravel())
        tiny = areas < 12
        tiny[0] = False
        flake_ids[tiny[flake_ids]] = 0

    # render
    if spec.style == "islands":
        flake_level, matrix_level = (0.3, 0.78) if spec.flake_dark else (0.8, 0.35)
        shade = flake_level + rng.normal(0, 0.04, next_id)
        img = np.full((h, w), matrix_level) + ndi.gaussian_filter(rng.normal(0, 0.03, (h, w)), 6)
        img[flake_ids > 0] = shade[flake_ids[flake_ids > 0]]
    elif spec.style == "regions":
        # neighbouring laths alternate between a few grey levels; boundaries take the level of the nearest lath,
        # so they are steps between levels, not lines
        # stripes alternate between two sets of levels and laths along a stripe alternate within the set, so
        # touching laths always differ (in BSE images of TiAl, neighbouring lamellae differ in phase or variant)
        levels = np.linspace(0.2, 0.9, max(2 * (spec.n_levels // 2), 2))
        half = len(levels) // 2
        shade = np.full(next_id, 0.5)
        for k, (c, st, sg) in stripe_of.items():
            shade[k] = levels[half * ((st + c) % 2) + (sg % half if half > 1 else 0)] + rng.normal(0, 0.02)
        img = np.full((h, w), 0.5)
        img[flake_ids > 0] = shade[flake_ids[flake_ids > 0]]
        gb_core = gb_alpha & ~ndi.binary_dilation(~gb_alpha, iterations=1)
        img[gb_core] = levels[-1]
        empty = (flake_ids == 0) & ~gb_core
        if empty.any() and (~empty).any():
            _, (ir, ic) = ndi.distance_transform_edt(empty, return_indices=True)
            img[empty] = img[ir[empty], ic[empty]]
    else:
        shade = 0.78 + rng.normal(0, 0.04, next_id)
        img = np.full((h, w), 0.18)
        img[flake_ids > 0] = shade[flake_ids[flake_ids > 0]]
        gb_core = gb_alpha & ~ndi.binary_dilation(~gb_alpha, iterations=1)
        img[gb_core] = 0.85
        boundary = (flake_ids == 0) & ~gb_core
        if spec.gap_fraction > 0:
            centres = boundary & (rng.uniform(size=(h, w)) < spec.gap_fraction / 12)
            gaps = ndi.binary_dilation(centres, iterations=2) & boundary
            img[gaps] = 0.72
    for _ in range(spec.n_pits):
        r0, c0, rad = rng.uniform(0, h), rng.uniform(0, w), rng.uniform(1.5, 4)
        img[(rows - r0) ** 2 + (cols - c0) ** 2 < rad**2] = 0.08
    img = ndi.gaussian_filter(img, spec.blur_px)

    scratch = np.zeros((h, w), bool)
    scratch_lines = []
    for _ in range(spec.n_scratches):
        angle = rng.uniform(0, 180)
        t = np.deg2rad(angle)
        r0, c0 = rng.uniform(0.2 * h, 0.8 * h), rng.uniform(0.2 * w, 0.8 * w)
        dist = np.abs((cols - c0) * np.sin(t) + (rows - r0) * np.cos(t))  # distance to the line
        along = (cols - c0) * np.cos(t) - (rows - r0) * np.sin(t)
        half_len = rng.uniform(0.35, 0.8) * np.hypot(h, w)
        on = np.abs(along) < half_len
        profile = np.exp(-0.5 * (dist / (spec.scratch_width_px / 2)) ** 2) * on
        delta = rng.choice([-0.35, 0.3])
        img = img + delta * profile
        this = (dist <= spec.scratch_width_px / 2 + 0.5) & on
        scratch |= this
        scratch_lines.append({"direction_deg": float(angle), "pixels": this, "bright": bool(delta > 0)})
    # where a scratch runs within PARALLEL_DEG of the local laths, it cannot be told from them by geometry
    crossing = np.zeros((h, w), bool)
    for s_line in scratch_lines:
        local = angles[colony]
        d = np.abs((s_line["direction_deg"] - local) % 180)
        crossing |= s_line["pixels"] & (np.minimum(d, 180 - d) >= PARALLEL_DEG)

    gradient = (cols / w - 0.5) * np.cos(rng.uniform(0, 6.3)) + (rows / h - 0.5) * np.sin(rng.uniform(0, 6.3))
    img = img * (1 + spec.illumination * gradient)
    img = img + rng.normal(0, spec.noise, img.shape)
    if spec.inverted:
        img = 1.0 - img
    image = (np.clip(img, 0, 1) * 255).astype(np.uint8)

    flakes = []
    objects = ndi.find_objects(flake_ids)
    for fid, sl in enumerate(objects, start=1):
        if sl is None:
            continue
        rr, cc = np.nonzero(flake_ids[sl] == fid)
        rr, cc = rr + sl[0].start, cc + sl[1].start
        _, major, minor = moments_orientation(rr, cc)
        flakes.append({
            "flake": fid,
            "orientation_deg": design[fid],
            "area_px": int(rr.size),
            "major_px": major,
            "minor_px": minor,
            "aspect_ratio": major / minor if minor > 0 else float("nan"),
            "touches_edge": bool(rr.min() == 0 or cc.min() == 0 or rr.max() == h - 1 or cc.max() == w - 1),
            "crossed_by_scratch": bool(scratch[rr, cc].any()),
        })
    return {"image": image, "flake_ids": flake_ids, "scratch": scratch, "gb_alpha": gb_alpha,
            "flakes": flakes, "colony_angles": angles, "scratch_lines": scratch_lines, "colony": colony,
            "scratch_crossing": crossing}


def default_specs(seed: int = 0) -> list[Spec]:
    """36 images in 12 families: alignment (kappa) x corruption (clean / scratched / harsh)."""
    rng = np.random.default_rng(seed)
    specs = []
    alignment = {"random": 0.0, "weak": 1.0, "moderate": 4.0, "strong": 25.0}
    corruption = {
        "clean": dict(n_scratches=0, noise=0.02, gap_fraction=0.04, illumination=0.1),
        "scratched": dict(n_scratches=3, noise=0.03, gap_fraction=0.08, illumination=0.15),
        "harsh": dict(n_scratches=5, noise=0.06, gap_fraction=0.15, illumination=0.3, blur_px=1.2),
    }
    for a_name, kappa in alignment.items():
        for c_name, extra in corruption.items():
            for k in range(3):
                s = int(rng.integers(0, 2**31))
                specs.append(Spec(
                    name=f"{a_name}_{c_name}_{k}",
                    family=f"{a_name}_{c_name}",
                    seed=s,
                    kappa=kappa,
                    mean_direction_deg=float(rng.uniform(0, 180)),
                    n_colonies=int(rng.integers(6, 16)),
                    lath_width_px=(int(rng.integers(4, 8)), int(rng.integers(10, 18))),
                    lath_length_px=(int(rng.integers(25, 50)), int(rng.integers(100, 240))),
                    boundary_px=float(rng.choice([1.5, 2.0, 3.0])),
                    inverted=bool(k == 2),  # one image per family with bright boundaries, as in BSE images
                    **extra,
                ))
    return specs


def write_dataset(out: Path, specs: list[Spec]) -> Path:
    out = Path(out)
    for sub in ["images", "truth/flake_ids", "truth/scratch", "truth/gb_alpha"]:
        (out / sub).mkdir(parents=True, exist_ok=True)
    flake_rows, image_rows = [], []
    for spec in specs:
        g = generate(spec)
        Image.fromarray(g["image"]).save(out / "images" / f"{spec.name}.png")
        Image.fromarray(g["flake_ids"].astype(np.uint16)).save(out / "truth" / "flake_ids" / f"{spec.name}.png")
        code = np.where(g["scratch_crossing"], 255, np.where(g["scratch"], 128, 0)).astype(np.uint8)
        Image.fromarray(code).save(out / "truth" / "scratch" / f"{spec.name}.png")
        Image.fromarray((g["gb_alpha"] * 255).astype(np.uint8)).save(out / "truth" / "gb_alpha" / f"{spec.name}.png")
        flake_rows += [{"image": spec.name, **f} for f in g["flakes"]]
        row = {k: v for k, v in asdict(spec).items() if k not in ("tags",)}
        row.update(shape=f"{spec.shape[0]}x{spec.shape[1]}", lath_width_px=str(spec.lath_width_px),
                   lath_length_px=str(spec.lath_length_px), image=spec.name)
        image_rows.append(row)
    pd.DataFrame(flake_rows).to_csv(out / "truth" / "flakes.csv", index=False, float_format="%.5g")
    pd.DataFrame(image_rows).drop(columns=["name"]).to_csv(out / "truth" / "images.csv", index=False)
    return out


def upsample_dataset(out: Path, factor: int) -> None:
    """Enlarge a written dataset in place, the way the adapt tool enlarges fine real images: images bicubic,
    label maps nearest, truth lengths and areas scaled (orientations and aspect ratios do not change)."""
    for f in sorted((out / "images").glob("*.png")):
        im = Image.open(f)
        im.resize((im.width * factor, im.height * factor), Image.BICUBIC).save(f)
    for sub in ("flake_ids", "scratch", "gb_alpha"):
        for f in sorted((out / "truth" / sub).glob("*.png")):
            a = np.array(Image.open(f))
            Image.fromarray(np.kron(a, np.ones((factor, factor), a.dtype))).save(f)
    fl = pd.read_csv(out / "truth" / "flakes.csv")
    fl["area_px"] = fl["area_px"] * factor**2
    for c in ("major_px", "minor_px"):
        fl[c] = fl[c] * factor
    fl.to_csv(out / "truth" / "flakes.csv", index=False, float_format="%.5g")


def ensure(out: Path, seed: int = 0) -> Path:
    """Generate the default set into `out` unless it is already there."""
    out = Path(out)
    if not (out / "truth" / "images.csv").exists():
        write_dataset(out, default_specs(seed))
    return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="data/lamellar_synth")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    out = write_dataset(Path(args.out), default_specs(args.seed))
    print(f"wrote {len(default_specs(args.seed))} images to {out}")


if __name__ == "__main__":
    main()

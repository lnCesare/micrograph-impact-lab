"""Paste fake polishing scratches onto real micrographs, keeping the truth apart: the real-texture scratch test.

The real Ti64 images have no known scratches, so scratch handling on real texture cannot be scored on them.
This writes scratched copies with an exact record of where every scratch is, so the evaluator can score
(1) how many pasted scratches the method finds, and (2) how much the scratches change the measurements,
against the method's own result on the clean original.

Blindness: the positions come from a seed drawn from the operating system's entropy, written only into the
truth folder. This script prints counts only. Methods never see the truth folder (tests/test_leakage.py),
and the evaluator alone reads it.

Scratch model (bright-field optical polishing damage):
- 2-6 scratches per image; ~60 % along one common polishing direction (+-10 deg), the rest at random angles;
  directions are not chosen relative to the laths, so some run along them (the hard case);
- straight, 0.3-1.0 x the image diagonal long, 1-3 px wide, Gaussian cross-profile;
- ~70 % dark, ~30 % light, varying slowly along the length, with short dropouts (intermittent scratches);
- severity "faint": contrast 0.15-0.4 of the image's 5-95 % intensity range, 1-3 px wide;
  "strong": contrast 0.5-1.0, 2-4 px wide.

Writes, under the output folder:
    images/<name>.png             the scratched copy (what a method sees)
    truth/scratch/<name>.png      255 where a scratch crosses the local lath direction by >= 15 deg,
                                  128 where it runs along it (local direction from the clean image's structure tensor)
    truth/images.csv              per image: source file, number of scratches, seed (no positions)

    python -m src.scratch_paste --src $MICROGRAPHS_DIR/01_BEST_Ti64_lamellar_bimodal/data --out data/Ti64_scratched
"""

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
from scipy import ndimage as ndi
from skimage.feature import structure_tensor

PARALLEL_DEG = 15.0


def lath_direction(gray: np.ndarray, sigma: float = 7.5) -> np.ndarray:
    """Local lath direction (deg, x right, y up, counterclockwise) from the structure tensor of the clean image.
    Written here on purpose, apart from the method's own code: the truth must not depend on the method."""
    arr, acc, aqq = structure_tensor(ndi.gaussian_filter(gray, 1.0), sigma=sigma, order="rc")
    grad = 0.5 * np.degrees(np.arctan2(-2 * acc, aqq - arr))
    return (grad + 90) % 180


SEVERITY = {"faint": {"contrast": (0.15, 0.4), "width": (1.0, 3.0)},
            "strong": {"contrast": (0.5, 1.0), "width": (2.0, 4.0)}}


def paste(gray: np.ndarray, rng: np.random.Generator, severity: str = "faint") -> tuple[np.ndarray, np.ndarray, int]:
    """gray in [0, 1]. Returns the scratched image, the truth code map (255 crossing, 128 parallel), and the count."""
    h, w = gray.shape
    rows, cols = np.mgrid[0:h, 0:w]
    spread = float(np.subtract(*np.percentile(gray, [95, 5])))
    out = gray.copy()
    mask = np.zeros((h, w), bool)
    angle_of = np.full((h, w), np.nan)
    n = int(rng.integers(2, 7))
    polish = rng.uniform(0, 180)
    diag = np.hypot(h, w)
    for _ in range(n):
        angle = (polish + rng.uniform(-10, 10)) % 180 if rng.uniform() < 0.6 else rng.uniform(0, 180)
        t = np.deg2rad(angle)
        r0, c0 = rng.uniform(0.15 * h, 0.85 * h), rng.uniform(0.15 * w, 0.85 * w)
        dist = np.abs((cols - c0) * np.sin(t) + (rows - r0) * np.cos(t))
        along = (cols - c0) * np.cos(t) - (rows - r0) * np.sin(t)
        half_len = rng.uniform(0.15, 0.5) * diag
        width = rng.uniform(*SEVERITY[severity]["width"])
        # contrast varying slowly along the length, with short dropouts
        positions = np.arange(-int(diag), int(diag) + 1)
        slow = ndi.gaussian_filter1d(rng.normal(0, 1, positions.size), 40)
        slow = 0.6 + 0.4 * (slow - slow.min()) / max(np.ptp(slow), 1e-9)
        drop = ndi.binary_dilation(rng.uniform(size=positions.size) < 0.004, iterations=int(rng.integers(3, 12)))
        strength = np.where(drop, 0.0, slow)
        idx = np.clip(np.round(along).astype(int) + int(diag), 0, positions.size - 1)
        amp = rng.uniform(*SEVERITY[severity]["contrast"]) * spread * (-1 if rng.uniform() < 0.7 else 1)
        on = (np.abs(along) < half_len) & (strength[idx] > 0)
        profile = np.exp(-0.5 * (dist / (width / 2)) ** 2) * on * strength[idx]
        out = out + amp * profile
        this = (dist <= width / 2 + 0.5) & on
        mask |= this
        angle_of[this] = angle
    lath = lath_direction(gray)
    d = np.abs((angle_of - lath) % 180)
    crossing = mask & (np.minimum(d, 180 - d) >= PARALLEL_DEG)
    code = np.where(crossing, 255, np.where(mask, 128, 0)).astype(np.uint8)
    return np.clip(out, 0, 1), code, n


def write_dataset(src: Path, names: list[str], out: Path, seed: int | None = None, severity: str = "faint") -> Path:
    out = Path(out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "truth" / "scratch").mkdir(parents=True, exist_ok=True)
    seed = int.from_bytes(os.urandom(8), "little") if seed is None else seed
    rng = np.random.default_rng(seed)
    rows = []
    for name in names:
        im = np.array(Image.open(Path(src) / name))
        gray = (im[..., :3].mean(axis=-1) if im.ndim == 3 else im).astype(float) / 255.0
        scratched, code, n = paste(gray, rng, severity)
        stem = Path(name).stem
        Image.fromarray((scratched * 255).round().astype(np.uint8)).save(out / "images" / f"{stem}.png")
        Image.fromarray(code).save(out / "truth" / "scratch" / f"{stem}.png")
        rows.append({"image": stem, "source": name, "n_scratches": n})
    table = pd.DataFrame(rows)
    table["seed"] = seed
    table["severity"] = severity
    table.to_csv(out / "truth" / "images.csv", index=False)
    return out


def main(argv=None) -> None:
    from src.splits import TI64_LAMELLAR

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, required=True, help="folder with the Ti64 images")
    parser.add_argument("--out", type=Path, default=Path("data/Ti64_scratched"))
    parser.add_argument("--severity", choices=sorted(SEVERITY), default="faint")
    args = parser.parse_args(argv)
    write_dataset(args.src, TI64_LAMELLAR, args.out, severity=args.severity)
    print(f"wrote {len(TI64_LAMELLAR)} scratched copies to {args.out}/images; truth in {args.out}/truth (not shown)")


if __name__ == "__main__":
    main()

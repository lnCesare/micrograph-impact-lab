"""Synthetic twin of a set of real micrographs: code-generated lamellar images with exact per-flake truth,
fitted to look like the real ones (flake width, boundary or matrix width, noise relative to contrast, phase fraction,
contrast polarity, style), so the flakes method can be tuned and scored for that kind of image.

A twin is only as good as the match: `realism` in twin_info.json compares the profile of the twin images with the
profile of the real ones, and the adapt tool reports it. Structures the generator cannot draw (curved plates,
dendrites, pores, multi-phase textures) are not twinned, whatever the match of the numbers says.

    python -m src.twin path/to/real/*.png --out data/twins/myset
"""

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from src.methods import profile as prof_mod
from src.synth_lamellar import Spec, generate, upsample_dataset, write_dataset

ALIGNMENT = {"random": 0.0, "moderate": 4.0, "strong": 25.0}
NUMERIC = ("noise_to_contrast", "minority_fraction", "coherence", "contrast")


def set_profile(paths: list[Path]) -> dict:
    """Median profile of a set of images; mode by vote ("ambiguous" only when no image is clear)."""
    profiles = []
    for p in paths:
        g = prof_mod.load(p)
        profiles.append(prof_mod.profile(g, prof_mod.region_of_interest(g)))
    votes = [p["mode"] for p in profiles if p["mode"] != "ambiguous"]
    mode = max(set(votes), key=votes.count) if votes else "ambiguous"
    out = {"mode": mode, "n_images": len(profiles), "per_image": profiles,
           "shape": [int(np.median([p["shape"][0] for p in profiles])), int(np.median([p["shape"][1] for p in profiles]))]}
    for k in NUMERIC:
        out[k] = float(np.median([p[k] for p in profiles]))
    out["by_mode"] = {}
    for m in profiles[0]["by_mode"]:
        vals = {k: [p["by_mode"][m][k] for p in profiles if p["by_mode"][m][k] is not None]
                for k in ("flake_width_px", "boundary_width_px")}
        phases = [p["by_mode"][m]["flake_phase"] for p in profiles]
        out["by_mode"][m] = {k: (float(np.median(v)) if v else None) for k, v in vals.items()}
        out["by_mode"][m]["flake_phase"] = max(set(phases), key=phases.count)
    return out


def _measure(spec: Spec, mode: str) -> dict:
    g = generate(spec)["image"] / 255.0
    p = prof_mod.profile(g)
    return {"flake": p["by_mode"][mode]["flake_width_px"], "boundary": p["by_mode"][mode]["boundary_width_px"],
            "nc": p["noise_to_contrast"], "fraction": p["minority_fraction"], "profile": p}


def _mismatch(m: dict, target: dict, mode: str) -> float:
    """Worst |log ratio| of the fitted quantities (flake width, boundary width, noise / contrast)."""
    t = target["by_mode"][mode]
    pairs = [(m["flake"], t["flake_width_px"]), (m["nc"], target["noise_to_contrast"])]
    if mode == "boundaries":
        pairs.append((m["boundary"], t["boundary_width_px"]))
    elif mode == "phase":
        pairs.append((m["fraction"], target["minority_fraction"]))
    logs = [abs(np.log(a / b)) for a, b in pairs if a and b]
    return float(max(logs)) if logs else float("inf")


def fit_spec(target: dict, mode: str, length_px: float | None, seed: int, iters: int = 8,
             preview_shape=(512, 640)) -> tuple[Spec, list]:
    """A base Spec whose images profile like `target` (a set profile), found by measuring previews and adjusting.

    The updates are damped and kept within a factor ~2 of the calibrated start, the laths stay at least 4 times
    longer than wide, and the iteration that matched best is returned (not the last one): the profiler's widths
    are not monotonic in the generator's widths outside the usual range (wide laths with thick boundaries turn
    into blobs that measure thin), and an undamped fit ran away there (JOURNAL 2026-11-17)."""
    t = target["by_mode"][mode]
    width = max(t["flake_width_px"] or 8.0, 3.0)
    c_w0 = max((width - 1.1) / 0.76, 2.5)  # calibrated start (JOURNAL 2026-11-17), then refined below
    c_w = c_w0
    b = float(np.clip(((t["boundary_width_px"] or 4.0) - 2.7) / 0.55, 0.5, 10.0)) if mode == "boundaries" else (
        1.0 if mode == "regions" else 2.0)
    noise = 0.03
    blur = 1.0
    fill = 0.6
    length = length_px or 6 * width

    def make(k):
        L = max(length, 4.0 * c_w)
        return Spec(name="preview", family="preview", seed=seed + k, shape=preview_shape, kappa=4.0, n_colonies=6,
                    lath_width_px=(0.75 * c_w, 1.25 * c_w), lath_length_px=(0.5 * L, 1.8 * L), boundary_px=b,
                    noise=noise, gap_fraction=0.06, n_scratches=0, n_pits=10, blur_px=blur,
                    style={"boundaries": "network", "phase": "islands", "regions": "regions"}[mode],
                    inverted=(t["flake_phase"] == "dark") if mode == "boundaries" else False,
                    flake_dark=(t["flake_phase"] == "dark"), island_fill=fill, matrix_gap_px=max(0.5 * c_w, 2.0))

    history, best = [], None
    for k in range(iters):
        spec = make(k)
        m = _measure(spec, mode)
        miss = _mismatch(m, target, mode)
        history.append({"iter": k, "lath_width_center": round(c_w, 2), "boundary_px": round(b, 2), "noise": round(noise, 4),
                        "blur_px": round(blur, 2),
                        "island_fill": round(fill, 3), "mismatch": round(miss, 3),
                        "measured": {key: m[key] for key in ("flake", "boundary", "nc", "fraction")}})
        if best is None or miss < best[0]:
            best = (miss, spec)
        if m["flake"]:
            c_w = float(np.clip(c_w * (width / m["flake"]) ** 0.7, 0.6 * c_w0, 1.8 * c_w0))
        if mode == "boundaries" and m["boundary"] and t["boundary_width_px"]:
            b = float(np.clip(b + 0.7 * (t["boundary_width_px"] - m["boundary"]) / 0.55, 0.5, min(30.0, 0.8 * c_w)))
        if mode == "phase" and m["fraction"]:
            fill = float(np.clip(fill * (target["minority_fraction"] / max(m["fraction"], 1e-3)) ** 0.7, 0.15, 0.95))
        if m["nc"]:
            wanted = noise * (target["noise_to_contrast"] / m["nc"]) ** 0.8
            if wanted < 0.003 and m["nc"] > 1.1 * target["noise_to_contrast"]:
                # less noise than the floor would be needed: the real image is blurrier (the residual of the noise
                # measure also picks up sharp edges)
                blur = float(np.clip(blur * 1.4, 0.6, 3.5))
            noise = float(np.clip(wanted, 0.003, 0.25))
    return best[1], history


def fit_structure(spec: Spec, real: dict, params: dict, iters: int = 3, upsample: int = 1) -> tuple[Spec, list]:
    """Second fit, on what the method measures: the lath length is scaled until the method's median aspect ratio on
    preview twin images matches the one on the real images. The real laths are measured through the method's own
    errors (laths cut by noise, merged across weak boundaries); a twin that reproduces the measured aspect ratio
    reproduces those conditions, and its exact truth then shows how far the measurement is off."""
    import tempfile

    from PIL import Image

    target = real.get("median_aspect_ratio")
    history = []
    if not target:
        return spec, history
    best = None
    for k in range(iters):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for j, kappa in enumerate((0.0, 25.0)):  # a random and a strongly aligned preview
                g = Image.fromarray(generate(replace(spec, seed=spec.seed + 100 * k + j, kappa=kappa, n_scratches=0))["image"])
                if upsample > 1:  # enlarged like the real images it is compared with
                    g = g.resize((g.width * upsample, g.height * upsample), Image.BICUBIC)
                paths.append(Path(tmp) / f"p{j}.png")
                g.save(paths[-1])
            got = method_structure(paths, params)
        ar = got.get("median_aspect_ratio")
        miss = abs(np.log(ar / target)) if ar else float("inf")
        history.append({"iter": k, "lath_length_px": [round(v, 1) for v in spec.lath_length_px], "method": got,
                        "mismatch": round(miss, 3)})
        if best is None or miss < best[0]:
            best = (miss, spec)
        if not ar or miss < np.log(1.1):
            break
        f = float(np.clip((target / ar) ** 1.2, 0.5, 2.0))  # AR grows less than proportionally with length
        lo, hi = spec.lath_length_px
        w_mid = float(np.mean(spec.lath_width_px))
        lo, hi = max(lo * f, 1.5 * w_mid), max(hi * f, 2.5 * w_mid)
        spec = replace(spec, lath_length_px=(lo, hi))
    return best[1], history


def generator_version() -> str:
    """Hash of the twin fit and the generator: a twin built by other code is rebuilt, never reused."""
    import hashlib

    here = Path(__file__).resolve().parent
    blob = b"".join((here / f).read_bytes() for f in ("twin.py", "synth_lamellar.py"))
    return hashlib.sha256(blob).hexdigest()[:10]


STRUCTURE_KEYS = ("median_aspect_ratio", "median_width_px")
REALISM_MAX_LOG = float(np.log(1.35))  # profile numbers (widths, noise / contrast)
STRUCTURE_MAX_LOG = float(np.log(1.5))  # what the method measures on twin vs real images


def method_structure(paths: list[Path], params: dict) -> dict:
    """Median over images of the method's median flake aspect ratio and width, for the realism check."""
    import tempfile

    from src.methods import METHODS

    with tempfile.TemporaryDirectory() as tmp:
        res = METHODS["flakes"]([Path(p) for p in paths], params, Path(tmp))
    out = {}
    for k in STRUCTURE_KEYS:
        v = [s[k] for s in res["image_summaries"].values() if s.get(k)]
        out[k] = float(np.median(v)) if v else None
    return out


def build_twin(paths: list[Path], out: Path, mode: str | None = None, length_px: float | None = None,
               seed: int = 0, per_family: int = 3, max_side: int = 1024, method_check: dict | None = None,
               upsample: int = 1, fit_paths: list[Path] | None = None, overrides: dict | None = None) -> dict:
    """Write a twin dataset to `out` (images/, truth/, split.json, twin_info.json) and return twin_info.

    method_check = {"params": ..., "real": method_structure(real images, params)}: the same method run on a few
    twin images must measure flakes of about the same shape (median aspect ratio, width) as on the real images;
    profile numbers alone can match on images that look nothing alike (blobs instead of laths).

    upsample > 1 (the real images were enlarged because their flakes are fine): the twin is fitted to the original
    images (fit_paths), drawn at their scale and enlarged by the same factor, so that it carries the same
    interpolation as the enlarged real images (`paths`), with which it is compared."""
    out = Path(out)
    target = set_profile(paths)
    fit_target = set_profile(fit_paths) if upsample > 1 and fit_paths else target
    mode = mode or (target["mode"] if target["mode"] != "ambiguous" else "boundaries")
    # the user's choice of scale / phase; a width is given in original pixels (target is the enlarged set when
    # upsample > 1, fit_target the original one)
    for prof_, scale in ((fit_target, 1), (target, upsample if fit_target is not target else 1)):
        for k, v in (overrides or {}).items():
            prof_["by_mode"][mode][k] = v * scale if k == "flake_width_px" else v
    h, w = target["shape"]
    scale_down = max(h, w) / max_side
    shape = (int(h / scale_down), int(w / scale_down)) if scale_down > 1 else (h, w)  # crop size, not a resize
    shape = (max(shape[0], 256), max(shape[1], 256))
    if upsample > 1:
        shape = (max(shape[0] // upsample, 96), max(shape[1] // upsample, 96))
    preview = (min(shape[0], 640), min(shape[1], 800))
    base, history = fit_spec(fit_target, mode, length_px / upsample if length_px else None, seed, preview_shape=preview)
    structure_history = []
    if method_check:
        base, structure_history = fit_structure(base, method_check["real"], method_check["params"], upsample=upsample)
    rng = np.random.default_rng(seed + 1000)
    area_ratio = shape[0] * shape[1] / (512 * 640)
    specs = []
    for a_name, kappa in ALIGNMENT.items():
        for c_name in ("clean", "scratched"):
            for k in range(per_family):
                specs.append(replace(
                    base, name=f"{a_name}_{c_name}_{k}", family=f"{a_name}_{c_name}", seed=int(rng.integers(0, 2**31)),
                    shape=shape, kappa=kappa, mean_direction_deg=float(rng.uniform(0, 180)),
                    n_colonies=int(np.clip(rng.integers(4, 14) * area_ratio, 2, 40)),
                    n_scratches=(int(rng.integers(2, 5)) if c_name == "scratched" else 0),
                    gap_fraction=base.gap_fraction * (2.0 if c_name == "scratched" else 1.0),
                    noise=base.noise * (1.3 if c_name == "scratched" else 1.0)))
    write_dataset(out, specs)
    if upsample > 1:
        upsample_dataset(out, upsample)  # (src/synth_lamellar.py: the generator owns its file layout)
    dev = sorted(s.name for s in specs if not s.name.endswith(f"_{per_family - 1}"))
    holdout = sorted(s.name for s in specs if s.name.endswith(f"_{per_family - 1}"))
    (out / "split.json").write_text(json.dumps({"dev": dev, "holdout": holdout}, indent=2) + "\n")

    # realism: profile of twin images vs the real set
    twin_profiles = [prof_mod.profile(prof_mod.load(out / "images" / f"{n}.png")) for n in dev[:: max(len(dev) // 4, 1)]]
    tp = {"by_mode": {mode: {k: float(np.median([p["by_mode"][mode][k] for p in twin_profiles
                                                   if p["by_mode"][mode][k] is not None]))
                             for k in ("flake_width_px", "boundary_width_px")}}}
    for k in NUMERIC:
        tp[k] = float(np.median([p[k] for p in twin_profiles]))
    realism = {}
    for k in ("flake_width_px", "boundary_width_px"):
        r, s_ = target["by_mode"][mode][k], tp["by_mode"][mode][k]
        realism[k] = {"real": r, "twin": round(s_, 2), "ratio": round(s_ / r, 3) if r else None}
    for k in NUMERIC:
        realism[k] = {"real": round(target[k], 4), "twin": round(tp[k], 4),
                      "ratio": round(tp[k] / target[k], 3) if target[k] else None}
    worst = max(abs(np.log(v["ratio"])) for k, v in realism.items()
                if v["ratio"] and k in ("flake_width_px", "boundary_width_px", "noise_to_contrast"))
    structure_worst = None
    if method_check:
        clean = [n for n in dev if "_clean_" in n][:: 2][:3]
        tw = method_structure([out / "images" / f"{n}.png" for n in clean], method_check["params"])
        logs = []
        for k in STRUCTURE_KEYS:
            r, s_ = method_check["real"].get(k), tw.get(k)
            ratio = round(s_ / r, 3) if r and s_ else None
            realism[f"method_{k}"] = {"real": r, "twin": s_, "ratio": ratio}
            logs.append(abs(np.log(ratio)) if ratio else float("inf"))
        structure_worst = float(max(logs))
    info = {
        "mode": mode, "mode_from_profile": target["mode"], "flake_phase": target["by_mode"][mode]["flake_phase"],
        "shape": list(shape), "n_images": len(specs), "dev": dev, "holdout": holdout,
        "base_spec": {k: v for k, v in asdict(base).items() if k not in ("tags",)},
        "fit_history": history, "structure_fit_history": structure_history, "realism": realism,
        "realism_worst_log_ratio": round(float(worst), 3),
        "structure_worst_log_ratio": round(structure_worst, 3) if structure_worst is not None else None,
        "realism_ok": bool(worst <= REALISM_MAX_LOG and (structure_worst is None or structure_worst <= STRUCTURE_MAX_LOG)),
        "real_profile": {k: v for k, v in target.items() if k != "per_image"}, "upsample": upsample,
        "overrides": overrides or {}, "generator_version": generator_version(),
        "sources": [str(p) for p in paths],
    }
    (out / "twin_info.json").write_text(json.dumps(info, indent=2, default=str) + "\n")
    return info


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=["boundaries", "phase"])
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    info = build_twin(args.images, args.out, mode=args.mode, seed=args.seed)
    print(json.dumps({k: info[k] for k in ("mode", "mode_from_profile", "shape", "realism", "realism_ok")}, indent=2))


if __name__ == "__main__":
    main()

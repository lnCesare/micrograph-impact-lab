"""Adapt the flakes method to a new set of images and measure them: the tool for a folder of your own micrographs.

    python -m src.adapt path/to/images/ --name my_ti64 [--mode boundaries|phase] [--um-per-px 0.2]
                        [--budget 30] [--live 2] [--dry-run]

Steps (all state in results/flakes/adapt/<name>/):
1. profile: what kind of flake image this is (boundary network or phase islands), flake and boundary width, noise;
2. first pass on the real images with scaled defaults (typical flake length, for the twin);
3. twin: code-generated images with exact per-flake truth, fitted to the real profile (src/twin.py), with a realism
   check; 2/3 of the twin images are for tuning (dev), 1/3 are held out;
4. warm start from the knowledge memory: the params that worked on the most similar image set measured before;
5. search: many configurations evaluated on the twin dev images, in parallel, cached (src/search.py);
6. optional agent stage (--live N, bills the Anthropic API): Claude plans the next search from all trials so far,
   and reviews the drawings of the real images and the twin; its verdicts are kept;
7. twin holdout: the chosen configuration (and the start) scored once on the held-out twin images;
8. real images: measured with the chosen params; per-flake and per-image tables, drawings, bootstrap intervals;
   the 30-degree rotation check (src/consistency.py) on the real images;
9. report (report.md, report.html) with every number's source and a confidence section; knowledge entry.

What it cannot do: the real images have no ground truth, so their accuracy is inferred from the twin, as far as the
twin resembles them (stated in the report), and judged in the drawings.
"""

import argparse
import json
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi
from skimage.filters import gaussian
import pandas as pd

from src import axial, consistency, knowledge, twin
from src.config import load_config
from src.execute import code_version
from src.methods import METHODS
from src.methods.flakes import CHOICES, DEFAULTS
from src.methods.profile import scaled_params
from src.search import Searcher, default_space, objective

ROOT = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def collect(inputs: list[Path]) -> list[Path]:
    out = []
    for p in map(Path, inputs):
        if p.is_dir():
            out += sorted(q for q in p.iterdir() if q.suffix.lower() in IMAGE_SUFFIXES)
        elif p.suffix.lower() in IMAGE_SUFFIXES:
            out.append(p)
    if not out:
        raise SystemExit("no images found")
    return out


def upsample_if_fine(images: list[Path], prof: dict, mode: str, out: Path, min_width: float = 6.0,
                     max_side: int = 3000) -> tuple[list[Path], int]:
    """Images whose flakes are under ~6 px wide are analysed at 2-3x size (bicubic): it adds no information, but the
    method's integer-radius morphology works far better at 8-12 px than at 4 px (measured on the scientist's SEM image:
    rotation check 0.71 -> 0.54, 3x more flakes resolved). Lengths are converted back to original pixels."""
    from PIL import Image

    width = prof["by_mode"][mode]["flake_width_px"] or 10.0
    if width >= min_width:
        return images, 1
    factor = int(min(3, np.ceil(9.0 / width)))
    # keep the enlarged images to a size the method handles in reasonable time and memory (~3000 px a side)
    factor = int(min(factor, max_side // max(prof["shape"])))
    if factor < 2:
        return images, 1
    folder = out / "upsampled"
    folder.mkdir(parents=True, exist_ok=True)
    new = []
    for img in images:
        im = Image.open(img)
        target = folder / f"{Path(img).stem}.png"
        im.resize((im.width * factor, im.height * factor), Image.BICUBIC).save(target)
        new.append(target)
    return new, factor


def two_scales(images: list[Path], prof: dict, mode: str, coarse_factor: float = 3.0, min_share: float = 0.3) -> dict | None:
    """A second, coarser structure in the flake phase (Widmanstaetten plates next to fine pearlite, primary alpha
    grains next to fine lamellae): the profile takes the median width, i.e. the fine scale. The share of the flake
    phase's area in structures at least coarse_factor x the flake width used; returns the coarse scale when it is
    at least min_share (the user may then choose it with --flake-width)."""
    from skimage.filters import threshold_otsu
    from skimage.morphology import skeletonize

    from src.methods import profile as prof_mod

    width = prof["by_mode"][mode]["flake_width_px"]
    flake_phase = prof["by_mode"][mode].get("flake_phase")
    if not width:
        return None
    found = []
    for img in images[:4]:
        g = prof_mod.load(img)
        roi = prof_mod.region_of_interest(g)
        sm = gaussian(np.where(roi, g, float(np.median(g[roi]))), 1.0)
        t = threshold_otsu(sm[roi])
        for name, phase in (("dark", (sm < t) & roi), ("bright", (sm >= t) & roi)):
            if flake_phase in ("dark", "bright") and name != flake_phase:
                continue  # the other phase is the boundaries or the matrix: thick is normal there
            dist = ndi.distance_transform_edt(phase)
            skel = skeletonize(phase)
            th = 2 * dist[skel]
            if th.size < 50:
                continue
            coarse = th >= coarse_factor * width
            share = float(th[coarse].sum() / th.sum())  # area ~ thickness x skeleton length
            if share >= min_share:
                found.append((name, share, float(np.median(th[coarse]))))
    if not found:
        return None
    phase = max({f[0] for f in found}, key=lambda n: sum(1 for f in found if f[0] == n))
    rows = [f for f in found if f[0] == phase]
    return {"phase": phase, "area_share": round(float(np.median([r[1] for r in rows])), 2),
            "coarse_width_px": round(float(np.median([r[2] for r in rows])), 1), "fine_width_px": width}


PX_LENGTH_COLUMNS = ("length_px", "width_px", "median_length_px", "median_width_px", "centroid_row", "centroid_col")


def to_original_pixels(df: pd.DataFrame, factor: int) -> pd.DataFrame:
    if factor == 1:
        return df
    df = df.copy()
    for c in PX_LENGTH_COLUMNS:
        if c in df:
            df[c] = df[c] / factor
    if "area_px" in df:
        df["area_px"] = df["area_px"] / factor**2
    return df


def resolved_start(profile: dict, mode: str, extra: dict | None = None) -> dict:
    """Explicit params for this image set: defaults with pixel params scaled to the measured widths."""
    m = profile["by_mode"][mode]
    p = {k: v for k, v in DEFAULTS.items() if k != "um_per_px"}
    boundary = m.get("boundary_width_px") if mode == "boundaries" else None
    p.update(scaled_params(m.get("flake_width_px") or 10.0, boundary, (), DEFAULTS, shape=profile.get("shape")))
    p.update(mode=mode, flake_width_px=None, flake_phase=m.get("flake_phase", "auto") if mode == "phase" else "auto")
    p.update(extra or {})
    return p


def _measure_one(args):
    image, params, out_dir, draw = args
    name = Path(image).stem
    res = METHODS["flakes"]([Path(image)], params, Path(out_dir), overlay=[name] if draw else [])
    return name, res["predictions"][name], res["image_summaries"][name], res.get("defects", {}).get(name, []), \
        res.get("overlays", [])


def measure_real(images: list[Path], params: dict, out_dir: Path, workers: int = 6) -> dict:
    with ProcessPoolExecutor(workers) as ex:
        parts = list(ex.map(_measure_one, [(str(i), params, str(out_dir), True) for i in images]))
    return {"predictions": {n: p for n, p, _, _, _ in parts}, "image_summaries": {n: s for n, _, s, _, _ in parts},
            "defects": {n: d for n, _, _, d, _ in parts}, "overlays": [o for *_, ov in parts for o in ov]}


def side_by_side(images: list[Path], overlays: list[str], out_dir: Path, max_width: int = 2048) -> list[str]:
    """Original next to its drawing, per image, for review by eye (review/<name>.jpg)."""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    by_stem = {Path(o).stem: Path(o) for o in overlays}
    written = []
    for img in images:
        # (the method saves a drawing as <stem>.jpg with the stem's own last ".xxx" replaced, e.g. "L08__10.3390_x"
        # -> "L08__10.jpg": match either name)
        o = by_stem.get(Path(img).stem) or by_stem.get(Path(Path(img).stem).stem)
        if o is None or not o.exists():
            continue
        ov = Image.open(o).convert("RGB")
        src = Image.open(img).convert("RGB").resize(ov.size)
        c = Image.new("RGB", (ov.width * 2 + 8, ov.height), "white")
        c.paste(src, (0, 0))
        c.paste(ov, (ov.width + 8, 0))
        if c.width > max_width:
            c = c.resize((max_width, round(c.height * max_width / c.width)))
        path = out_dir / f"{Path(img).stem}.jpg"
        c.save(path, quality=88)
        written.append(str(path))
    return written


def write_input_size_labels(originals: list[Path], analysed: list[Path], labels_dir: Path) -> None:
    """One label PNG per input image, at the input's size (0 background, k = flake k of flakes.csv; pockets are 0),
    for src.score_masks: labels of images analysed enlarged are shrunk back (nearest neighbour)."""
    from PIL import Image

    for orig, ana in zip(originals, analysed):
        path = labels_dir / f"{Path(ana).stem}.png"
        if not path.exists():
            continue
        lab = np.array(Image.open(path)).astype(np.int64)
        w, h = Image.open(orig).size
        if lab.shape != (h, w):
            rr = (np.arange(h) * lab.shape[0] / h + lab.shape[0] / (2 * h)).astype(int).clip(0, lab.shape[0] - 1)
            cc = (np.arange(w) * lab.shape[1] / w + lab.shape[1] / (2 * w)).astype(int).clip(0, lab.shape[1] - 1)
            lab = lab[rr][:, cc]
        if lab.max() > 65535:
            raise ValueError(f"{orig}: more than 65535 flakes do not fit a 16-bit label PNG")
        out = labels_dir / f"{Path(orig).stem}.png"
        Image.fromarray(lab.astype(np.uint16)).save(out)
        if out != path:
            path.unlink()


def bootstrap(flakes: list[dict], n: int = 300, seed: int = 0) -> dict:
    """95 % intervals of the image statistics, resampling the flakes used (area-weighted, as the method)."""
    used = [f for f in flakes if f.get("used_for_orientation")]
    shape = [f["aspect_ratio"] for f in flakes if f.get("used_for_shape")]
    if len(used) < 10:
        return {}
    rng = np.random.default_rng(seed)
    th = np.array([f["orientation_deg"] for f in used])
    wt = np.array([f["area_px"] for f in used], float)
    ar = np.array(shape, float)
    S, D, A = [], [], []
    ref_dir = axial.resultant(th, wt)[0]
    for _ in range(n):
        i = rng.integers(0, len(th), len(th))
        d, s = axial.resultant(th[i], wt[i])
        S.append(s)
        D.append(float(axial.axial_diff(d, ref_dir)))
        if len(ar):
            A.append(float(np.median(ar[rng.integers(0, len(ar), len(ar))])))
    q = lambda v: [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]
    return {"order_parameter_ci95": q(S), "mean_direction_ci95_halfwidth_deg": round(float(np.percentile(D, 95)), 2),
            "median_aspect_ratio_ci95": q(A) if A else None}


def _fmt(v, nd=2):
    return "n/a" if v is None else f"{v:.{nd}f}"


def real_image_guard(sample: list[Path], start: dict, chosen: dict, out: Path, tolerances: dict) -> dict:
    """The twin cannot see everything: settings tuned on it are kept only if, on the real images, they are not
    clearly less stable under rotation than the start settings, and do not collapse the number of flakes
    (label-free checks). Found on the basketweave set: a 6 % better twin objective came with a rotation check of 3.9
    instead of 1.2 (and 4x fewer flakes). A moderate drop in the number of flakes is no fault by itself: on Ti64 the
    tuned settings keep laths whole instead of cutting them (654 flakes per image instead of 1604, twin holdout
    0.51 instead of 1.16), so only a collapse below a quarter counts."""
    res = {}
    for which, params in (("start", start), ("chosen", chosen)):
        real = measure_real(sample, params, out / which, workers=6)
        cons = consistency.check(METHODS["flakes"], sample, params, out / which / "consistency",
                                 {"image_summaries": real["image_summaries"]}, tolerances)
        res[which] = {"rotation": cons["metrics"].get("worst_consistency_ratio"),
                      "flakes_used": float(np.median([s["n_used_orientation"] for s in real["image_summaries"].values()]))}
    a, b = res["start"], res["chosen"]
    worse_rotation = b["rotation"] is not None and a["rotation"] is not None and \
        b["rotation"] > max(1.0, 1.25 * a["rotation"])
    lost_flakes = b["flakes_used"] < 0.25 * a["flakes_used"]
    res["keep_chosen"] = not (worse_rotation or lost_flakes)
    res["reason"] = ("" if res["keep_chosen"] else
                     ("fewer than a quarter of the flakes" if lost_flakes else "") + (" and " if lost_flakes and worse_rotation else "")
                     + ("a worse rotation check" if worse_rotation else ""))
    return res


# --- the agent stage --------------------------------------------------------------------------------------------

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string"},
        "rationale": {"type": "string"},
        "reading": {"type": "string", "enum": ["keep", "boundaries", "phase", "regions"]},
        "flake_phase": {"type": "string", "enum": ["auto", "bright", "dark"]},
        "start_params_json": {"type": "string"},
        "search_space_json": {"type": "string"},
    },
    "required": ["hypothesis", "rationale", "reading", "flake_phase", "start_params_json", "search_space_json"],
    "additionalProperties": False,
}
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "reject", "inconclusive"]},
        "reading_correct": {"type": "string", "enum": ["yes", "no", "unsure"]},
        "reason": {"type": "string"},
        "next_step": {"type": "string"},
    },
    "required": ["verdict", "reading_correct", "reason", "next_step"],
    "additionalProperties": False,
}


def _trial_table(trials: list[dict], k: int = 12) -> str:
    rows = sorted({t["key"]: t for t in trials}.values(), key=lambda t: t["objective"])[:k]
    lines = []
    for t in rows:
        m = t["metrics"]
        lines.append(json.dumps({"objective": round(t["objective"], 3), "worst_family": m["worst_family_tolerance_ratio"],
                                 "pooled": m["worst_tolerance_ratio"], "AR_bias": m["median_aspect_ratio_bias"],
                                 "recall": m["flake_recall"], "by_family": t.get("by_family"),
                                 "params": {k_: v for k_, v in t["params"].items()
                                            if k_ in DEFAULTS and v != DEFAULTS.get(k_)}}, default=str))
    return "\n".join(lines)


def agent_plan(claude, ctx: dict, trials: list[dict], reviews: list[dict], best: dict) -> dict:
    from src.methods.flakes import flakes as flakes_fn

    prompt = f"""You tune the flakes method for a new set of micrographs by choosing the next local search.
Image set profile (median): {json.dumps({k: v for k, v in ctx['profile'].items() if k != 'per_image'}, default=str)}
Reading of the image: {ctx['mode']} ({ctx['mode_reason']}).
Twin (code-generated images with exact truth, fitted to this profile) realism: {json.dumps(ctx['realism'])}
Twin trusted: {ctx.get('twin_trusted')}. {'Your search space is run on the twin dev images.' if ctx.get('twin_trusted') else
'The twin does NOT resemble these images closely enough: no search is run on it. Your start params are applied as they are, and only the next review of the real drawings judges them, so change few params and only on the reviewer\'s evidence (or leave start_params_json empty).'}

Method and params:
{flakes_fn.__doc__}

The search scores each configuration on the twin dev images: objective = worst family tolerance ratio + 0.25 x pooled
(families: alignment random / moderate / strong x clean / scratched; target <= 1; lower is better).
Best trials so far:
{_trial_table(trials)}
Current best params: {json.dumps(best['params'], default=str)}
Reviews of the real-image drawings so far: {json.dumps(reviews[-3:], default=str) if reviews else 'none'}
Similar image sets measured before (knowledge memory, nearest first; their reviews are lessons, not facts about this set):
{json.dumps(ctx.get('memory') or 'none', default=str)[:3000]}

Propose the next search: a hypothesis (one falsifiable sentence about which params limit the worst family or what the
reviewer saw); reading: "keep", or switch to "boundaries" / "phase" / "regions" (flakes told apart only by steps between
grey levels, no boundary lines, as in BSE images of TiAl) when the reviewer found that the outlined regions
are not the flakes (a switch rebuilds the twin for that reading and restarts the search; use it only on the reviewer's
evidence); flake_phase for the phase reading ("auto" = minority phase); start params (JSON object; only keys of the
params list; omitted keys keep the current best); and a search space (JSON object: param -> list of explicit values
to try, at most 6 params). Prefer changes that the reviewer's reading of the real drawings supports; the twin can be
wrong where it does not resemble the real images."""
    dry = {"hypothesis": "Dry run.", "rationale": "No API call.", "reading": "keep", "flake_phase": "auto",
           "start_params_json": "{}", "search_space_json": "{}"}
    return claude.ask_json("planner", prompt, PLAN_SCHEMA, dry_run_response=dry)


def agent_review(claude, ctx: dict, real_overlays: list[Path], twin_overlay: Path | None, summary: dict) -> dict:
    images = [p for p in real_overlays[:3]] + ([twin_overlay] if twin_overlay else [])
    prompt = f"""Review the flake measurements of a new image set.
Each attached image shows a micrograph on the left and the method's drawing on it on the right.
The first {min(3, len(real_overlays))} are REAL micrographs of the set (no ground truth);
{'the last one is a code-generated twin image with exact truth;' if twin_overlay else ''}
in the drawing each flake is filled with a colour for its orientation (key: half wheel, bottom right), grey = measured but not used
(branched, round, or taken for grain-boundary alpha), white lines with black edges = detected scratches, arrow = mean
direction (length grows with the order parameter); header: mean direction, S, local S, median aspect ratio, flakes used.
Reading used: {ctx['mode']} ({ctx['mode_reason']}); flakes are the {'regions between boundary lines' if ctx['mode'] == 'boundaries' else ('regions of uniform grey level between intensity steps' if ctx['mode'] == 'regions' else ctx.get('flake_phase', '') + ' phase')}.
Twin scores of the current configuration: {json.dumps(summary.get('twin'), default=str)} (twin trusted:
{summary.get('twin_trusted')}; an untrusted twin does not resemble these images and its scores say little about them)
Two scales found in the flake phase: {json.dumps(ctx.get('two_scales'), default=str)}
Similar sets measured before and what their reviewers said: {json.dumps(ctx.get('memory') or 'none', default=str)[:2000]}
Real-image summary: {json.dumps(summary.get('real'), default=str)}

1. reading_correct: do the outlined regions correspond to the flakes a metallographer would measure in these images
   (yes / no / unsure)? If no, say which reading would: the other phase; the boundary reading (flakes between thin
   lines); the phase reading (one phase in a matrix); or the regions reading (flakes told apart only by steps between
   several grey levels, no lines between them, as in back-scattered electron images of TiAl lamellae).
2. Where does the segmentation fail on the real images: flakes merged across boundaries, flakes cut along their length,
   boundaries or scratches missed, matrix or grain-boundary alpha taken as flakes?
3. verdict: accept only if the drawings support the measured numbers. next_step: concrete param changes (param: value
   -> value) or a different reading, or say that no param of the method can fix what you see."""
    dry = {"verdict": "inconclusive", "reading_correct": "unsure", "reason": "Dry run: no review.", "next_step": "none"}
    return claude.ask_json("reviewer", prompt, REVIEW_SCHEMA, images=images, dry_run_response=dry)


def _validated(params_json: str, base: dict) -> dict:
    try:
        p = json.loads(params_json or "{}")
    except json.JSONDecodeError:
        return {}
    out = {}
    for k, v in p.items():
        if k not in DEFAULTS or k in ("um_per_px",):
            continue
        if k in CHOICES and v not in CHOICES[k]:
            continue
        out[k] = v
    return out


def _space_from(space_json: str) -> dict:
    try:
        s = json.loads(space_json or "{}")
    except json.JSONDecodeError:
        return {}
    return {k: ("abs", list(v)[:8]) for k, v in s.items() if k in DEFAULTS and isinstance(v, list) and v
            and k not in CHOICES}


# --- the report --------------------------------------------------------------------------------------------------

def _figures(out: Path, real: dict) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    files = []
    th = [f["orientation_deg"] for fl in real["predictions"].values() for f in fl if f.get("used_for_orientation")]
    wt = [f["area_px"] for fl in real["predictions"].values() for f in fl if f.get("used_for_orientation")]
    ar = [f["aspect_ratio"] for fl in real["predictions"].values() for f in fl if f.get("used_for_shape")]
    if th:
        fig = plt.figure(figsize=(4.2, 3.2))
        ax = fig.add_subplot(projection="polar")
        bins = np.deg2rad(np.arange(0, 361, 10))
        doubled = np.concatenate([th, np.array(th) + 180])
        ax.hist(np.deg2rad(doubled), bins=bins, weights=np.concatenate([wt, wt]), color="#3b6ea5")
        ax.set_yticklabels([])
        ax.set_title("Flake orientation (area-weighted), all images", fontsize=9)
        fig.tight_layout()
        fig.savefig(out / "rose.png", dpi=130)
        plt.close(fig)
        files.append("rose.png")
    if ar:
        fig, ax = plt.subplots(figsize=(4.2, 3.0))
        ax.hist(np.clip(ar, 1, 20), bins=40, color="#7a5195")
        ax.set_xlabel("aspect ratio (interior, unbranched flakes)")
        ax.set_ylabel("flakes")
        fig.tight_layout()
        fig.savefig(out / "aspect_ratio.png", dpi=130)
        plt.close(fig)
        files.append("aspect_ratio.png")
    return files


def write_report(out: Path, ctx: dict) -> None:
    t = ctx["table"]
    tw = ctx["twin_scores"]
    cons = ctx["consistency"]["metrics"] if ctx.get("consistency") else {}
    conf = ctx["confidence"]
    lines = [
        f"# Flake measurements: {ctx['name']}",
        "",
        f"{len(t)} images; reading: **{ctx['mode']}** ({ctx['mode_reason']}); "
        f"flake width {ctx['flake_width_px']} px; code version {ctx['code_version']}.",
        "",
        "## Confidence",
        "",
        *[f"- {c}" for c in conf],
        "",
        "## Set summary",
        "",
        "| | median | min | max |",
        "|---|---|---|---|",
    ]
    for col, label in (("order_parameter", "order parameter S"), ("local_order_parameter", "local S (within colonies)"),
                       ("mean_misalignment_deg", "mean misalignment (deg)"), ("median_aspect_ratio", "median aspect ratio"),
                       ("median_length_px", "median length (px)"), ("median_width_px", "median width (px)"),
                       ("n_used_orientation", "flakes used per image")):
        if col in t and t[col].notna().any():
            lines.append(f"| {label} | {t[col].median():.3g} | {t[col].min():.3g} | {t[col].max():.3g} |")
    lines += ["", "Mean directions differ per image (axial; see the table): a set-level mean direction is only meaningful "
              "when the images were taken in the same specimen frame.", "",
              "## Per image", "",
              "| image | mean dir (deg) | S [95 % CI] | local S | misalignment (deg) | AR [95 % CI] | flakes | scratches |",
              "|---|---|---|---|---|---|---|---|"]
    for _, r in t.iterrows():
        ci = r.get("order_parameter_ci95") or [None, None]
        aci = r.get("median_aspect_ratio_ci95") or [None, None]
        fmt = lambda v: "–" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.3g}"
        lines.append(f"| {r['image']} | {fmt(r['mean_direction_deg'])} | {fmt(r['order_parameter'])} [{fmt(ci[0])}, {fmt(ci[1])}] "
                     f"| {fmt(r['local_order_parameter'])} | {fmt(r['mean_misalignment_deg'])} | {fmt(r['median_aspect_ratio'])} "
                     f"[{fmt(aci[0])}, {fmt(aci[1])}] | {int(r['n_used_orientation'])} | {int(r['n_scratches'])} |")
    lines += ["", "## How accurate is it (twin images with exact truth)", "",
              f"Twin: {ctx['twin_info']['n_images']} code-generated images fitted to this set "
              f"(realism ok: {ctx['twin_info']['realism_ok']}; worst profile mismatch x{np.exp(ctx['twin_info']['realism_worst_log_ratio']):.2f}).",
              "", "| twin images | params | mean direction err (deg) | S err | misalignment err (deg) | AR err | flake orientation err (deg) | flakes found | worst family ratio |",
              "|---|---|---|---|---|---|---|---|---|"]
    for row in tw:
        m = row["metrics"]
        f = lambda v, d=3: "–" if v is None else (f"{v:.{d}g}")
        lines.append(f"| {row['split']} | {row['which']} | {f(m['median_direction_error_deg'])} | {f(m['median_order_abs_error'])} "
                     f"| {f(m['median_misalignment_abs_error_deg'])} | {f(m['median_aspect_ratio_rel_error'])} "
                     f"| {f(m['median_flake_orientation_error_deg'])} | {f(m['flake_recall'])} | {f(m['worst_family_tolerance_ratio'])} |")
    lines += ["", f"Search: {ctx['search']['n_evaluated']} new configurations evaluated ({ctx['search']['n_trials']} incl. cached); "
              f"objective {ctx['search']['start_objective']:.3f} at the start, {ctx['search']['best_objective']:.3f} chosen.",
              f"Warm start: {ctx['warm_start'] or 'none (no similar image set in the memory)'}.", ""]
    if cons:
        lines += ["## Real images: rotation check", "",
                  f"Central disk measured as is and turned by 30 deg: direction off by "
                  f"{_fmt(cons.get('median_rotation_direction_diff_deg'))} deg, S by {_fmt(cons.get('median_rotation_order_abs_diff'), 3)}, "
                  f"aspect ratio by {_fmt(cons.get('median_rotation_aspect_ratio_rel_diff'), 3)} (relative) "
                  f"(worst ratio to tolerance {_fmt(cons.get('worst_consistency_ratio'))}; <= 1 passes). Necessary, not sufficient.", ""]
    if ctx.get("reviews"):
        lines += ["## Agent reviews of the real drawings", ""]
        for i, r in enumerate(ctx["reviews"], 1):
            lines.append(f"- Round {i}: **{r.get('verdict')}**, reading correct: {r.get('reading_correct')}. {r.get('reason')}")
        lines.append("")
    lines += ["## Drawings (left: original, right: flakes coloured by orientation; grey = not used)", "",
              *[f"![{Path(o).stem}]({Path(o).parent.name}/{Path(o).name})" for o in ctx["overlays"][:20]], "",
              *[f"![{f}]({f})" for f in ctx["figures"]], "",
              "## Files", "", "- `flakes.csv`: every flake (orientation, length, width, aspect ratio, misalignment, flags)",
              "- `image_summaries.csv`: one row per image, with bootstrap intervals", "- `params.json`: the params used",
              "- `adapt_log.json`: profile, twin, search trials, reviews", ""]
    (out / "report.md").write_text("\n".join(lines))
    try:
        import html as _html

        body = []
        for line in lines:
            if line.startswith("# "):
                body.append(f"<h1>{_html.escape(line[2:])}</h1>")
            elif line.startswith("## "):
                body.append(f"<h2>{_html.escape(line[3:])}</h2>")
            elif line.startswith("![") and "](" in line:
                src = line.split("](")[1].rstrip(")")
                body.append(f'<figure><img src="{src}" style="max-width:100%"><figcaption>{_html.escape(src)}</figcaption></figure>')
            elif line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if all(set(c) <= set("-") for c in cells):
                    continue
                body.append("<tr>" + "".join(f"<td>{_html.escape(c).replace('**', '')}</td>" for c in cells) + "</tr>")
            elif line.startswith("- "):
                body.append(f"<li>{_html.escape(line[2:]).replace('**', '')}</li>")
            elif line:
                body.append(f"<p>{_html.escape(line).replace('**', '')}</p>")
        html_text = "\n".join(body)
        html_text = html_text.replace("</tr>\n<p>", "</tr></table>\n<p>").replace("<p>| ", "<p>")
        # wrap consecutive rows in tables
        out_lines, in_table = [], False
        for l_ in html_text.split("\n"):
            if l_.startswith("<tr>") and not in_table:
                out_lines.append("<table>")
                in_table = True
            if not l_.startswith("<tr>") and in_table:
                out_lines.append("</table>")
                in_table = False
            out_lines.append(l_)
        if in_table:
            out_lines.append("</table>")
        style = ("body{font-family:-apple-system,Helvetica,Arial,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;"
                 "color:#1d2433;background:#fff}table{border-collapse:collapse;margin:8px 0;font-size:13px}"
                 "td{border:1px solid #d5d9e0;padding:4px 8px}tr:first-child td{background:#f1f3f7;font-weight:600}"
                 "figure{margin:12px 0}figcaption{font-size:12px;color:#5a6270}")
        (out / "report.html").write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>Flakes: {ctx['name']}</title>"
                                         f"<style>{style}</style></head><body>{''.join(out_lines)}</body></html>")
    except Exception:  # the markdown report is the record; the HTML is a convenience
        pass


# --- main ---------------------------------------------------------------------------------------------------------

def adapt(images: list[Path], name: str, mode: str | None = None, budget: int = 30, live: int = 0, dry_run: bool = False,
          um_per_px: float | None = None, workers: int = 6, results_root: Path | None = None,
          twin_root: Path | None = None, use_knowledge: bool = True, record_knowledge: bool = True,
          flake_width: float | None = None, flake_phase: str | None = None, log=print) -> dict:
    t0 = time.monotonic()
    results_root = Path(results_root or ROOT / "results" / "flakes")
    out = results_root / "adapt" / name
    out.mkdir(parents=True, exist_ok=True)
    twin_dir = Path(twin_root or ROOT / "data" / "twins") / name
    cv = code_version()
    log(f"adapt {name}: {len(images)} images, code {cv}")

    # 1. profile and reading
    prof = twin.set_profile(images)
    if mode:
        mode_reason = "set by the user"
    elif prof["mode"] != "ambiguous":
        mode = prof["mode"]
        mode_reason = ("minority phase is a connected thin network" if mode == "boundaries"
                       else "minority phase is separate blobs about as thick as the matrix")
    else:
        mode, mode_reason = "boundaries", "ambiguous profile; boundary reading by default (the agent review checks it)"
    overrides = {}
    if flake_width:
        overrides["flake_width_px"] = float(flake_width)
    if flake_phase and mode == "phase":
        overrides["flake_phase"] = flake_phase
    prof["by_mode"][mode].update(overrides)
    log(f"  profile: {mode} ({mode_reason}); flake width {prof['by_mode'][mode]['flake_width_px']} px"
        + (f" (set by the user: {overrides})" if overrides else ""))
    scales = None if flake_width else two_scales(images, prof, mode)
    if scales:
        log(f"  two scales: {scales['area_share']:.0%} of the {scales['phase']} phase is in structures "
            f"~{scales['coarse_width_px']} px wide; measuring the fine scale ({scales['fine_width_px']} px)")
    original_images = list(images)
    original_width = prof["by_mode"][mode]["flake_width_px"]
    images, factor = upsample_if_fine(images, prof, mode, out)
    if factor > 1:
        prof = twin.set_profile(images)
        prof["by_mode"][mode].update({k: (v * factor if k == "flake_width_px" else v) for k, v in overrides.items()})
        log(f"  fine flakes: analysed at {factor}x size; flake width now {prof['by_mode'][mode]['flake_width_px']} px")

    # 2. first pass (typical flake length for the twin)
    start = resolved_start(prof, mode)
    sample = images[:: max(len(images) // 4, 1)][:4]
    first = measure_real(sample, start, out / "first_pass", workers)
    lengths = [s["median_length_px"] for s in first["image_summaries"].values() if s.get("median_length_px")]
    length = float(np.median(lengths)) if lengths else None
    real_structure = {k: (float(np.median(v)) if (v := [s[k] for s in first["image_summaries"].values() if s.get(k)])
                          else None) for k in twin.STRUCTURE_KEYS}
    method_check = {"params": start, "real": real_structure}

    # 3. twin
    old_info = json.loads((twin_dir / "twin_info.json").read_text()) if (twin_dir / "twin_info.json").exists() else {}
    if (old_info.get("mode") == mode and old_info.get("structure_worst_log_ratio") is not None
            and old_info.get("upsample", 1) == factor and old_info.get("overrides", {}) == overrides
            and old_info.get("generator_version") == twin.generator_version()
            and abs(np.log((old_info.get("real_profile", {}).get("by_mode", {}).get(mode, {}).get("flake_width_px") or 1e-9)
                           / (prof["by_mode"][mode]["flake_width_px"] or 1e-9))) < 0.05):  # (same images, same profile)
        tinfo = json.loads((twin_dir / "twin_info.json").read_text())
        log("  twin: reused")
    else:
        if twin_dir.exists():
            shutil.rmtree(twin_dir)
        tinfo = twin.build_twin(images, twin_dir, mode=mode, length_px=length, seed=0, method_check=method_check,
                                upsample=factor, fit_paths=original_images, overrides=overrides)
        log(f"  twin: built, realism ok {tinfo['realism_ok']} (worst profile mismatch x{np.exp(tinfo['realism_worst_log_ratio']):.2f}, "
            f"flake shape as measured by the method x{np.exp(tinfo['structure_worst_log_ratio']):.2f})")

    # 4. warm starts
    kfile = results_root / "knowledge.jsonl"
    starts, warm = [start], None
    reading_start = start  # the scaled defaults of the reading in use (the fallback when the twin is not trusted)
    memory_notes = []  # what was learned on the most similar earlier sets, failures included (for the agent)
    if use_knowledge:
        entries = knowledge.load(kfile)
        for d, e in knowledge.nearest(entries, prof, mode, k=3, only_succeeded=False):
            if e.get("name") != name:
                memory_notes.append({"set": e.get("name"), "distance": round(d, 2), "reading": e.get("mode"),
                                     "twin_trusted": e.get("twin_trusted"), "succeeded": knowledge.succeeded(e),
                                     "twin_holdout_worst_family": (e.get("twin_holdout") or {}).get("worst_family_tolerance_ratio"),
                                     "rotation_check": e.get("consistency"), "last_review": e.get("review")})
        near = [(d, e) for d, e in knowledge.nearest(entries, prof, mode) if e.get("name") != name]
        for d, e in near[:2]:
            starts.append(knowledge.warm_start(e, prof, mode) | {"flake_width_px": None})
        if near:
            warm = ", ".join(f"{e['name']} (distance {d:.2f})" for d, e in near[:2])
            log(f"  warm start from {warm}")

    # 5. search, only on a twin that can be trusted: one that resembles the images (realism) and on which flakes
    # can be found at all; on any other twin the search fits something that is not these images
    def trusted(info, result):
        recall = result["start_best"]["metrics"].get("flake_recall") or 0.0
        return bool(info["realism_ok"] and recall >= 0.3), recall

    searcher = Searcher(twin_dir, tinfo["dev"], results_root / "search_cache", cv, workers=workers, log=log)
    res = searcher.run(starts, default_space(mode), budget=0)  # the starts only
    twin_trusted, start_recall = trusted(tinfo, res)
    if twin_trusted:
        res = searcher.run(starts, default_space(mode), budget=budget)  # the starts come from the cache
        best = res["best"]
    else:
        log(f"  twin not trusted (realism ok {tinfo['realism_ok']}, start flake recall {start_recall:.2f}): "
            "no search; the scaled defaults are used")
        best = searcher.evaluate(reading_start, tag="defaults")
    set_by_eye = False  # params changed by the agent from the drawings alone (no trusted twin to score them)

    # 6. agent stage
    reviews, plans = [], []
    if live > 0 or dry_run:
        from src.llm import Claude

        cfg = load_config(ROOT / "config.yaml")
        cfg["eval"]["dataset"] = "lamellar_synth"  # the flakes question in the system prompt
        claude = Claude(cfg, dry_run=dry_run)
        ctx = {"profile": prof, "mode": mode, "mode_reason": mode_reason, "realism": tinfo["realism"],
               "flake_phase": prof["by_mode"][mode]["flake_phase"], "twin_trusted": twin_trusted,
               "memory": memory_notes, "two_scales": scales}
        for i in range(max(live, 1 if dry_run else 0)):
            draw_dir = out / f"agent_round_{i + 1}"
            real_i = measure_real(sample[:3], best["params"], draw_dir, workers)
            tw_draw = METHODS["flakes"]([twin_dir / "images" / f"{tinfo['dev'][0]}.png"], best["params"], draw_dir / "twin",
                                        overlay=[tinfo["dev"][0]])
            summ = {"twin": best["metrics"], "twin_trusted": twin_trusted,
                    "real": {n: {k: s.get(k) for k in ("order_parameter", "median_aspect_ratio", "n_used_orientation",
                                                       "coverage")} for n, s in real_i["image_summaries"].items()}}
            # the reviewer sees each original next to its drawing (the colour fill hides the structure)
            real_pairs = side_by_side(sample[:3], real_i["overlays"], draw_dir / "pairs", max_width=1568)
            twin_pairs = side_by_side([twin_dir / "images" / f"{tinfo['dev'][0]}.png"], tw_draw["overlays"],
                                      draw_dir / "pairs_twin", max_width=1568)
            review = agent_review(claude, ctx, [Path(o) for o in real_pairs],
                                  Path(twin_pairs[0]) if twin_pairs else None, summ)
            reviews.append(review)
            log(f"  agent review {i + 1}: {review['verdict']}, reading correct: {review['reading_correct']}")
            plan = agent_plan(claude, ctx, searcher.trials, reviews, best)
            plans.append(plan)
            log(f"  agent plan {i + 1}: reading {plan['reading']}; {plan['hypothesis'][:160]}")
            if plan["reading"] in ("boundaries", "phase", "regions") and plan["reading"] != mode:
                # the reviewer found that the outlined regions are not the flakes: switch the reading, rebuild the twin
                mode = plan["reading"]
                mode_reason = f"switched by the agent after review {i + 1}: {review['reason'][:300]}"
                twin_dir = twin_dir.parent / f"{name}__{mode}"
                if not (twin_dir / "twin_info.json").exists():
                    start_m = resolved_start(prof, mode)
                    first_m = measure_real(sample, start_m, draw_dir / "first_pass", workers)
                    real_m = {k: (float(np.median(v)) if (v := [s[k] for s in first_m["image_summaries"].values()
                                                                if s.get(k)]) else None) for k in twin.STRUCTURE_KEYS}
                    tinfo = twin.build_twin(images, twin_dir, mode=mode, length_px=length, seed=0,
                                            method_check={"params": start_m, "real": real_m},
                                            upsample=factor, fit_paths=original_images)
                else:
                    tinfo = json.loads((twin_dir / "twin_info.json").read_text())
                extra = {"flake_phase": plan["flake_phase"]} if mode == "phase" and plan["flake_phase"] != "auto" else {}
                searcher = Searcher(twin_dir, tinfo["dev"], results_root / "search_cache", cv, workers=workers, log=log)
                reading_start = resolved_start(prof, mode, extra)
                res = searcher.run([reading_start], default_space(mode), budget=0)
                twin_trusted, start_recall = trusted(tinfo, res)
                if twin_trusted:
                    res = searcher.run([reading_start], default_space(mode), budget=budget, seed=i + 1)
                best = res["best"]  # objectives on different twins are not comparable: the new reading's best
                set_by_eye = False
                ctx.update(mode=mode, mode_reason=mode_reason, realism=tinfo["realism"], twin_trusted=twin_trusted,
                           flake_phase=extra.get("flake_phase", prof["by_mode"][mode]["flake_phase"]))
                continue
            proposal = {k: v for k, v in _validated(plan["start_params_json"], best["params"]).items() if k != "mode"}
            if twin_trusted:
                p_start = {**best["params"], **proposal}
                space = _space_from(plan["search_space_json"]) or default_space(mode)
                r2 = searcher.run([p_start], space, budget=max(budget // 2, 6), seed=i + 1)
                if r2["best"]["objective"] < best["objective"]:
                    best = r2["best"]
            elif proposal:
                # no trusted twin: the agent's change is taken on the evidence of the drawings, and judged by the next
                # review round (the report says so)
                best = searcher.evaluate({**best["params"], **proposal}, tag=f"agent round {i + 1} (by eye)")
                set_by_eye = True
                if proposal.get("flake_phase") in ("bright", "dark"):
                    ctx["flake_phase"] = proposal["flake_phase"]  # (the next review is told which phase is measured)
                log(f"  agent round {i + 1}: params set from the drawings (twin not trusted): {proposal}")
        usage = claude.usage
    else:
        usage = []

    # 6b. real-image guard: twin-tuned settings must not be clearly worse on the real images than the start
    guard = None
    guard_start = res["start_best"]["params"] if twin_trusted else reading_start
    # twin-tuned settings, and settings the agent set from the drawings alone (SEM screenshot: the agent's changes
    # made the rotation check 1.02 instead of 0.45, and both its reviews still rejected the drawings)
    if (twin_trusted or set_by_eye) and best["params"] != guard_start:
        # on the same images as the final rotation check: on 3 images with ~50 flakes each the check is too noisy
        # to decide (banded set: 1.50 on 3 images, 0.43 on 6, for the same settings)
        guard = real_image_guard(images[:6], guard_start, best["params"], out / "guard",
                                 load_config(ROOT / "config.yaml")["flakes"]["consistency_tolerances"])
        guard["by_eye"] = set_by_eye
        log(f"  real-image guard: start {guard['start']}, chosen {guard['chosen']}: "
            + ("chosen kept" if guard["keep_chosen"] else f"start kept ({guard['reason']})"))
        if not guard["keep_chosen"]:
            best = searcher.evaluate(guard_start, tag="guard: start kept")
            set_by_eye = False

    # 7. twin holdout, once
    twin_scores = []
    for which, params in (("start", res["start_best"]["params"]), ("chosen", best["params"])):
        for split, names in (("dev", tinfo["dev"]), ("holdout", tinfo["holdout"])):
            t = searcher.evaluate(params, names=names, tag=f"{which} {split}")
            twin_scores.append({"which": which, "split": split, "metrics": t["metrics"], "objective": t["objective"],
                                "by_family": t.get("by_family")})

    # 8. real images
    final_params = dict(best["params"]) | ({"um_per_px": um_per_px / factor} if um_per_px else {})
    real = measure_real(images, final_params, out, workers)
    write_input_size_labels(original_images, images, out / "labels")
    review_files = side_by_side(images, real["overlays"], out / "review")  # (upsampled images, when fine)
    rows = []
    for n, s in real["image_summaries"].items():
        rows.append({"image": n, **{k: v for k, v in s.items() if not isinstance(v, dict)}, **bootstrap(real["predictions"][n])})
    table = to_original_pixels(pd.DataFrame(rows), factor)
    table.to_csv(out / "image_summaries.csv", index=False, float_format="%.4g")
    to_original_pixels(pd.DataFrame([{"image": n, "flake": i + 1, **f} for n, fl in real["predictions"].items()
                                     for i, f in enumerate(fl)]), factor).to_csv(out / "flakes.csv", index=False,
                                                                                 float_format="%.4g")
    (out / "params.json").write_text(json.dumps(final_params, indent=2, default=str) + "\n")
    cfg = load_config(ROOT / "config.yaml")
    cons = consistency.check(METHODS["flakes"], images[:6], final_params, out / "consistency",
                             {"image_summaries": real["image_summaries"]}, cfg["flakes"]["consistency_tolerances"])

    # confidence statements
    width = original_width or 0
    hold = next(r for r in twin_scores if r["which"] == "chosen" and r["split"] == "holdout")
    dev = next(r for r in twin_scores if r["which"] == "chosen" and r["split"] == "dev")
    conf = []
    hw = hold["metrics"]["worst_family_tolerance_ratio"]
    cr0 = cons["metrics"].get("worst_consistency_ratio")
    if not twin_trusted:
        conf.append("**Twin not usable** for this image set (it does not resemble the images closely enough, or flakes "
                    "cannot be found on it at this size): the accuracy below is not evidence about these images, and no "
                    "search was run on it; " + ("the settings were changed by the agent from its reading of the drawings "
                    "(not scored against any truth). " if set_by_eye else "the scaled default settings were used. ")
                    + "Treat the numbers as unvalidated; check them in the drawings.")
    elif hw is not None and hw > 2:
        conf.append("**Low confidence**: on the held-out twin images the errors exceed the tolerances more than twice "
                    "(worst family ratio below).")
    elif hw is not None and hw <= 1 and cr0 is not None and cr0 <= 1:
        conf.append("**Within tolerances** on held-out twin images, and consistent under rotation on the real images.")
    f2 = lambda v: "n/a" if v is None else f"{v:.2f}"
    conf.append(f"Twin accuracy (held-out twin images, exact truth): worst family ratio {f2(hold['metrics']['worst_family_tolerance_ratio'])}, "
                f"pooled {f2(hold['metrics']['worst_tolerance_ratio'])} (<= 1 means within the tolerances of config.yaml); "
                f"dev {f2(dev['metrics']['worst_family_tolerance_ratio'])}: a gap shows how much the search fitted the dev twin images.")
    sw = tinfo.get("structure_worst_log_ratio")
    conf.append("The twin resembles the real images" + ("" if tinfo["realism_ok"] else " only roughly") +
                f" (worst profile mismatch x{np.exp(tinfo['realism_worst_log_ratio']):.2f}"
                + (f"; flake aspect ratio and width as measured by the method differ by up to x{np.exp(sw):.2f}" if sw is not None else "")
                + "); the twin accuracy carries over only as far as that, and only for structures the generator draws "
                "(straight laths in colonies).")
    cr = cons["metrics"].get("worst_consistency_ratio")
    rot = cons["metrics"]
    halves = [(r["order_parameter_ci95"][1] - r["order_parameter_ci95"][0]) / 2 for r in rows if r.get("order_parameter_ci95")]
    ci_s = float(np.median(halves)) if halves else None
    s_med = rot.get("median_order_parameter")
    conf.append(f"Rotation check on the real images (central disk turned by 30 deg and measured again): worst ratio "
                f"{cr if cr is None else round(cr, 2)} ({'passes' if cr is not None and cr <= 1 else 'FAILS'}); "
                f"S changes by {_fmt(rot.get('median_rotation_order_abs_diff'), 3)}, the direction by "
                + (f"{_fmt(rot.get('median_rotation_direction_diff_deg'), 2)} deg"
                   if rot.get('median_rotation_direction_diff_deg') is not None else "n/a (S < 0.3: no defined direction)")
                + f", the median aspect ratio by {_fmt(100 * rot['median_rotation_aspect_ratio_rel_diff'], 1) if rot.get('median_rotation_aspect_ratio_rel_diff') is not None else 'n/a'} %"
                + (f"; for comparison, the 95 % interval of S from the finite number of flakes is +/-{ci_s:.3f}" if ci_s else "")
                + (". At S below ~0.3 the mean direction is weakly defined and moves easily" if s_med is not None and s_med < 0.3 else "")
                + ".")
    if factor > 1:
        conf.append(f"Flakes are only ~{width:.1f} px wide (the profiler over-reads small widths by ~1 px): the images were "
                    f"analysed at {factor}x size. Near and below ~4-5 px the boundaries are at the resolution limit: "
                    "orientation and S hold best, aspect ratios and lengths least. Use higher-resolution images if you can.")
    if guard and not guard["keep_chosen"]:
        conf.append(f"The settings {'set by the agent from the drawings' if guard.get('by_eye') else 'tuned on the twin'} were **not used**: on the real images they gave {guard['reason']} "
                    f"(flakes used {guard['chosen']['flakes_used']:.0f} vs {guard['start']['flakes_used']:.0f}, rotation check "
                    f"{guard['chosen']['rotation']} vs {guard['start']['rotation']} with the start settings), so the start "
                    "settings were kept: the twin did not capture what made the difference.")
    if scales:
        conf.append(f"**Two scales**: {scales['area_share']:.0%} of the {scales['phase']} phase is in structures about "
                    f"{scales['coarse_width_px']:.0f} px wide, {scales['coarse_width_px'] / scales['fine_width_px']:.0f}x the "
                    f"typical flake width used here ({scales['fine_width_px']:.1f} px). The settings fit the fine structures; "
                    f"the coarse ones may be split, merged or left out, and the statistics mix the two populations. "
                    f"If the coarse ones are the flakes you mean, rerun with --flake-width "
                    f"{scales['coarse_width_px']:.0f}" + (" --mode phase --flake-phase " + scales['phase'] if mode == "phase" else "")
                    + ".")
    if overrides:
        conf.append(f"Set by the user: {overrides}.")
    if "ambiguous" in mode_reason:
        conf.append("The image profile was ambiguous between a boundary network and phase islands: check in the drawings "
                    "that the outlined regions are the flakes you mean; rerun with --mode phase otherwise.")
    if reviews:
        last = reviews[-1]
        conf.append(f"Last agent review of the real drawings: {last['verdict']}, reading correct: {last['reading_correct']}.")
    if um_per_px is None:
        conf.append("Lengths are in pixels (no --um-per-px given).")
    if usage:
        tin, tout = sum(u["input_tokens"] for u in usage), sum(u["output_tokens"] for u in usage)
        conf.append(f"Agent stage: {len(usage)} Claude calls, {tin} input / {tout} output tokens "
                    f"(about ${(4 * tin + 20 * tout) / 1e6:.2f} at claude-opus-5-5 list prices).")

    figures = _figures(out, real)
    report_ctx = {"name": name, "table": table, "mode": mode, "mode_reason": mode_reason, "flake_width_px": width,
                  "code_version": cv, "twin_info": tinfo, "twin_scores": twin_scores, "consistency": cons,
                  "search": {"n_evaluated": res["n_evaluated"], "n_trials": len(searcher.trials),
                             "start_objective": res["start_best"]["objective"], "best_objective": best["objective"]},
                  "warm_start": warm, "reviews": reviews, "overlays": review_files or real["overlays"], "figures": figures,
                  "confidence": conf}
    write_report(out, report_ctx)
    log_obj = {"name": name, "code_version": cv, "images": [str(i) for i in images], "profile": prof, "mode": mode,
               "mode_reason": mode_reason, "twin": {k: v for k, v in tinfo.items() if k not in ("dev", "holdout")},
               "starts": starts, "warm_start": warm, "chosen_params": final_params, "twin_scores": twin_scores,
               "consistency": cons["metrics"], "plans": plans, "twin_trusted": twin_trusted, "set_by_eye": set_by_eye, "upsample_factor": factor, "two_scales": scales, "overrides": overrides,
               "real_image_guard": guard,
               "memory_notes": memory_notes, "reviews": reviews, "usage": usage,
               "trials": [{k: t[k] for k in ("objective", "metrics", "params", "tag") if k in t} for t in searcher.trials],
               "seconds": round(time.monotonic() - t0)}
    (out / "adapt_log.json").write_text(json.dumps(log_obj, indent=2, default=str) + "\n")
    if record_knowledge:
        knowledge.record(kfile, {"name": name, "mode": mode, "profile": {k: v for k, v in prof.items() if k != "per_image"},
                                 "best_params": best["params"], "twin_dev": dev["metrics"], "twin_holdout": hold["metrics"],
                                 "realism_ok": tinfo["realism_ok"], "twin_trusted": twin_trusted, "consistency": cons["metrics"].get("worst_consistency_ratio"),
                                 "n_images": len(images), "code_version": cv,
                                 "review": reviews[-1] if reviews else None})
    log(f"  done in {log_obj['seconds']} s: report {out / 'report.md'}")
    return log_obj


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", type=Path, help="image files or folders")
    parser.add_argument("--name", required=True, help="name of this image set (results/flakes/adapt/<name>/)")
    parser.add_argument("--mode", choices=["boundaries", "phase", "regions"], help="force the reading of the image "
                        "(regions: flakes told apart by grey-level steps, no boundary lines; never chosen automatically)")
    parser.add_argument("--budget", type=int, default=30, help="configurations to evaluate on the twin (default 30)")
    parser.add_argument("--live", type=int, default=0, help="agent rounds with Claude (bills the API; default 0)")
    parser.add_argument("--dry-run", action="store_true", help="run one agent round with canned answers, no API")
    parser.add_argument("--um-per-px", type=float, help="scale, to report lengths in um")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--no-knowledge", action="store_true", help="do not warm-start from, nor add to, the memory")
    parser.add_argument("--flake-width", type=float, help="flake width in px, instead of the measured one (e.g. the "
                        "coarse scale the report names when an image has two)")
    parser.add_argument("--flake-phase", choices=["bright", "dark"], help="phase reading: which phase the flakes are")
    args = parser.parse_args(argv)
    adapt(collect(args.images), args.name, mode=args.mode, budget=args.budget, live=args.live, dry_run=args.dry_run,
          um_per_px=args.um_per_px, workers=args.workers, use_knowledge=not args.no_knowledge,
          record_knowledge=not args.no_knowledge, flake_width=args.flake_width, flake_phase=args.flake_phase)


if __name__ == "__main__":
    main()

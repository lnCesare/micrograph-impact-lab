"""Flake (lath) segmentation and alignment: orientation, order parameter, misalignment and aspect ratio per flake."""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from skimage.draw import line as draw_line
from skimage.feature import hessian_matrix, structure_tensor
from skimage.filters import gaussian, sato, threshold_otsu
from skimage.measure import label, regionprops
from skimage.morphology import disk, h_maxima, remove_small_holes, remove_small_objects
from skimage.segmentation import find_boundaries, relabel_sequential
from skimage.segmentation import watershed as skwatershed
from skimage.transform import probabilistic_hough_line

from src import axial
from src.methods import profile as profile_mod
from src.methods import register
from src.methods.common import save_labels

DEFAULTS = {
    "mode": "auto",
    "flake_phase": "auto",
    "phase_offset": 0.0,
    "flake_width_px": "auto",
    "polarity": "auto",
    "roi": "auto",
    "crop_bottom_px": 0,
    "flatten_px": 40.0,
    "sigma": 1.0,
    "boundary_width_px": 2.5,
    "boundary_sensitivity": 1.0,
    "detect_scratches": True,
    "scratch_min_length_px": 200,
    "scratch_min_crossing_deg": 12.0,
    "scratch_max_side_diff_deg": 15.0,
    "scratch_min_continuity": 0.45,
    "scratch_min_contrast": 0.1,
    "scratch_min_contrast_fraction": 0.35,
    "scratch_line_gap_px": 12,
    "scratch_ridge_factor": 1.0,
    "bridge_gaps_px": 9,
    "h_px": 0.5,
    "merge_saddle_ratio": 0.5,
    "merge_max_offaxis_deg": 30.0,
    "merge_min_fill": 0.9,
    "min_area_px": 30,
    "min_solidity": 0.75,
    "branch_test": "skeleton",
    "min_aspect_for_orientation": 1.5,
    "gb_alpha_length_factor": 4.0,
    "gb_alpha_min_misfit_deg": 25.0,
    "weight": "area",
    "tile_px": 160,
    "um_per_px": None,
}
CHOICES = {
    "mode": ("auto", "boundaries", "phase", "regions"),
    "flake_phase": ("auto", "bright", "dark"),
    "polarity": ("auto", "dark_boundaries", "bright_boundaries"),
    "roi": ("auto", "full"),
    "weight": ("area", "count"),
    "branch_test": ("skeleton", "solidity"),
}
SCRATCH = 1  # value in the returned defect maps, as in the watershed method
HOUGH_THETA = np.linspace(-np.pi / 2, np.pi / 2, 720, endpoint=False)


@register(task="flakes")
def flakes(images, params, out_dir, overlay=()):
    """Segment the flakes (laths, lamellae) of a lamellar microstructure and measure their shape and alignment.

    Pipeline: grayscale -> region of interest (drops dark mounting resin and anything outside the largest
    sample region) -> boundary polarity (the minority phase of an Otsu split is the boundary phase)
    -> background flattening -> gaussian denoise -> scratch detection and orientation-guided inpainting
    -> ridge filter (Sato) for thin boundaries -> flakes = regions between boundaries, necked regions
    separated by an h-maxima watershed on the distance transform -> per-flake second moments.

    Per flake: orientation_deg (0-180, from the image x axis, counterclockwise as displayed), length_px and
    width_px (major / minor axis of the same-moment ellipse), aspect_ratio, area_px, solidity,
    touches_edge, crossed_by_scratch, used_for_orientation, used_for_shape.
    Per image (image_summaries): mean_direction_deg, order_parameter S (2D nematic, 0 isotropic - 1 parallel),
    mean / median misalignment to the mean direction, circular_spread_deg, fraction_within_15deg,
    local_order_parameter (mean S inside tiles, i.e. alignment within colonies), tensor_order_parameter
    (independent estimate from the image structure tensor), median / mean aspect ratio, length and width,
    counts, scratch count and scratch pixel fraction.
    Orientation statistics use unbranched flakes (see branch_test) with aspect ratio >= min_aspect_for_orientation,
    edge flakes included; shape statistics use the same flakes minus those touching the edge.

    Params (all optional, defaults in brackets):
    - mode: "auto" | "boundaries" | "phase" | "regions" ["auto"]
        "boundaries": flakes are the regions between thin boundary lines (etched alpha + beta titanium, Widmanstaetten);
        "phase": flakes are the regions of one phase in a matrix of the other (islands, bands, lamellar colonies);
        "regions": flakes are regions of uniform grey level, told apart by steps between levels, with no boundary
        lines (back-scattered electron images of TiAl lamellae, multi-phase colour etchings); never chosen by "auto";
        "auto": from the image profile (connected thin network -> boundaries, separate thick blobs -> phase,
        ambiguous -> boundaries)
    - flake_phase: "auto" | "bright" | "dark", phase mode: which phase the flakes are ["auto": the minority phase]
    - phase_offset: -0.3-0.3, phase mode: shifts the Otsu threshold by this fraction of the intensity range;
      > 0 makes flakes thinner [0.0]
    - flake_width_px: "auto" | null | a width in px ["auto"]; every pixel param that is not given explicitly is
        scaled to it (proportionally for coarser images; for finer ones only below half the reference width of
        10 px); null = no scaling
    - polarity: "auto" | "dark_boundaries" (optical, etched) | "bright_boundaries" (e.g. backscattered SEM) ["auto"]
    - roi: "auto" drops very dark regions touching the image border and keeps the largest sample region;
      "full" uses the whole image ["auto"]
    - crop_bottom_px: rows cut from the bottom first, e.g. an SEM data bar [0]
    - flatten_px: 0-200, sigma of the background subtracted to even out illumination; 0 = off [40]
    - sigma: 0-3, gaussian denoise in px [1.0]
    - boundary_width_px: 1-8, typical boundary width; sets the ridge filter scales [2.5]
    - boundary_sensitivity: 0.3-3, multiplies the Otsu threshold of the ridge response;
      lower finds more (fainter) boundary, higher only the strongest [1.0]
    - detect_scratches: find straight thin lines that cross the flakes and inpaint them before segmenting [true]
    - scratch_min_length_px: 50-1000, shortest straight line taken as a scratch [200]
    - scratch_min_crossing_deg: 5-60, a scratch must cross the local flake direction by at least this;
      a straight line closer to the flake direction cannot be told from the flakes and is left alone [12]
    - scratch_max_side_diff_deg: 5-45, the flake direction on the two sides of a scratch must agree within
      this; lines between two differently oriented regions are kept as grain (colony) boundaries [15]
    - scratch_min_contrast: 0.02-0.5, along at least half its length a scratch must be darker (or brighter)
      than both its sides 3 px away by this fraction of the image's 5-95 % intensity range [0.1]
    - scratch_min_contrast_fraction: 0.2-0.9, the share of its length on which a scratch must stand out from both
      sides (on real texture a side sample often falls on a dark boundary) [0.35]
    - scratch_line_gap_px: 3-30, gaps a candidate line may have in the line map (a scratch crossing dark
      boundaries loses pixels at every crossing) [12]
    - scratch_ridge_factor: 0.3-3, multiplies the Otsu threshold of the thin-line response for scratch candidates [1.0]
    - scratch_min_continuity: -1-1, correlation of the image on the two sides of the line, sampled along
      the flakes 3 px from the line; a scratch lies on flakes that continue across it, a boundary ends them [0.45]
    - bridge_gaps_px: 0-25, closes gaps in the boundaries up to about this long, along the local flake direction
      only (a closing with a line oriented along the laths), so dashed boundaries between neighbouring laths
      are joined without bridging across a lath; 0 = off [9]
    - h_px: 0-5, h-maxima height of the watershed seeds on the distance transform; 0 = no watershed,
      flakes are the connected regions between boundaries [0.5]
    - merge_saddle_ratio: 0-1, neighbouring pieces are merged back when the narrowest point between them
      is at least this fraction of the thinner piece's half width (a lath cut across its width merges back,
      two laths touching through a narrow gap in the boundary stay apart); 1 = never merge [0.5]
    - merge_max_offaxis_deg: 5-90, pieces are merged only when they lie end to end: the line between their
      centres within this angle of their flake direction; neighbouring laths side by side are never merged,
      however wide the gap between them; 90 = no direction test [30]
    - merge_min_fill: 0-1, a merge is refused when the merged region would fill its same-moment ellipse less
      well than this fraction of the worse piece (pieces of one lath joined end to end fill it as well as each
      piece; an L-shaped or branched result much worse); 0 = no shape test [0.9]
    - min_area_px: 5-500, smaller regions are dropped [30]
    - min_solidity: 0-1, regions less solid than this are checked for branches [0.75]
    - branch_test: "skeleton" | "solidity" ["skeleton"]
        "skeleton": a low-solidity region is left out of the statistics only if its skeleton has three or more
        long arms (a Y, an H, laths joined across a boundary); a bent lath is kept, its skeleton is one path;
        "solidity": every region below min_solidity is left out (bent laths too)
    - gb_alpha_length_factor: 0-10, a flake longer than this many times the median flake length, whose direction
      differs from that of the flakes around it by more than gb_alpha_min_misfit_deg, is taken for grain-boundary
      alpha: drawn grey and left out of the statistics; 0 = off [4]
    - gb_alpha_min_misfit_deg: 10-60, see gb_alpha_length_factor [25]
    - min_aspect_for_orientation: 1-3, rounder flakes have no meaningful direction and are left out [1.5]
    - weight: "area" | "count", weighting of flakes in the orientation statistics ["area"]
    - tile_px: 64-512, tile size for local_order_parameter [160]
    - um_per_px: scale, if known; lengths are then also reported in um [null]
    Only the params above are accepted; any other key fails the run.
    Writes a flake label image per input to out_dir/labels/ and colour drawings for the chosen images:
    flakes coloured by orientation (colour wheel in the corner), unused flakes grey, scratches white with
    black edges, mean direction as an arrow.
    """
    unknown = set(params) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown params {sorted(unknown)}; allowed: {sorted(DEFAULTS)}")
    p = {**DEFAULTS, **params}
    for key, allowed in CHOICES.items():
        if p[key] not in allowed:
            raise ValueError(f"{key}={p[key]!r}, expected one of {allowed}")
    fw = p["flake_width_px"]
    if not (fw is None or fw == "auto" or (isinstance(fw, (int, float)) and fw > 0)):
        raise ValueError(f"flake_width_px={fw!r}, expected null, \"auto\" or a width in px")
    p["_given"] = sorted(params)

    out_dir = Path(out_dir)
    predictions, summaries, defect_maps, defects, overlays = {}, {}, {}, {}, []
    for path in map(Path, images):
        name = path.stem
        raw = load_gray(path)
        res = analyse(raw, p)
        predictions[name] = res["flakes"]
        summaries[name] = res["summary"]
        defect_maps[name] = res["defect_map"]
        defects[name] = res["scratches"]
        save_labels(res["labels"], out_dir / "labels" / f"{name}.png")
        if name in overlay:
            overlays.append(str(save_flake_overlay(res, out_dir / "overlays" / name)))
    return {
        "predictions": predictions,
        "image_summaries": summaries,
        "defects": defects,
        "defect_maps": defect_maps,
        "exclude_edge": False,
        "params_used": p,
        "overlays": overlays,
        "checks": {
            "every_image_has_flakes": all(s["n_used_orientation"] > 0 for s in summaries.values()),
            "order_parameter_in_range": all(
                s["order_parameter"] is not None and 0 <= s["order_parameter"] <= 1 for s in summaries.values()
            ),
        },
    }


# --- steps ----------------------------------------------------------------------------------------------------

def load_gray(path: Path) -> np.ndarray:
    """Grayscale in [0, 1]; a colour image is projected on its axis of largest colour variance (src/methods/profile.py)."""
    return profile_mod.load(path)


def region_of_interest(gray: np.ndarray, mode: str) -> np.ndarray:
    return profile_mod.region_of_interest(gray, mode)


def boundary_polarity(smooth: np.ndarray, roi: np.ndarray) -> str:
    """The boundary phase is the minority side of an Otsu split (boundaries are thin, flakes fill the area)."""
    values = smooth[roi]
    return "dark_boundaries" if np.mean(values < threshold_otsu(values)) <= 0.5 else "bright_boundaries"


def lath_direction_field(img: np.ndarray, sigma_int: float) -> tuple[np.ndarray, np.ndarray]:
    """Local flake direction (deg, project convention) and coherence from the structure tensor."""
    arr, acc, aqq = structure_tensor(img, sigma=sigma_int, order="rc")  # rows, cols
    # gradient direction in (x right, y up): gx = d/dcol, gy = -d/drow
    jxx, jyy, jxy = aqq, arr, -acc
    grad = 0.5 * np.degrees(np.arctan2(2 * jxy, jxx - jyy))
    coherence = np.hypot(jxx - jyy, 2 * jxy) / (jxx + jyy + 1e-12)
    return (grad + 90) % 180, coherence


def find_scratches(smooth: np.ndarray, roi: np.ndarray, direction: np.ndarray, coherence: np.ndarray, p: dict,
                   log: list | None = None):
    """Straight thin lines (dark or bright) that run across the flakes, with the same flake direction on both sides.

    With a list as `log`, every candidate line and the value of each test are appended to it (diagnostics)."""
    found = np.zeros(smooth.shape, bool)
    items = []
    if not p["detect_scratches"]:
        return found, items
    side = 4 * p["boundary_width_px"] + 8
    h, w = smooth.shape
    spread = float(np.subtract(*np.percentile(smooth[roi], [95, 5])))
    # pixels whose own line direction (Hessian, scale 1.2 px) crosses the local flake direction:
    # lath boundaries drop out before the line search, so they cannot interrupt a scratch that crosses them
    hrr, hrc, hcc = hessian_matrix(smooth, sigma=1.2, order="rc", use_gaussian_derivatives=True)
    principal = 0.5 * np.degrees(np.arctan2(-2 * hrc, hcc - hrr))  # axis of the largest eigenvalue, y up
    for black in (True, False):
        line_dir = (principal + 90) % 180 if black else principal % 180  # dark line: curvature > 0 across it
        across = axial.axial_diff(line_dir, direction) >= p["scratch_min_crossing_deg"]
        ridge = sato(smooth, sigmas=[0.8, 1.2], black_ridges=black)
        ridge[~roi] = 0
        t = threshold_otsu(ridge[roi])
        thin = (ridge > p["scratch_ridge_factor"] * t) & across
        segments = probabilistic_hough_line(
            thin, threshold=30, line_length=int(p["scratch_min_length_px"]), line_gap=int(p["scratch_line_gap_px"]),
            theta=HOUGH_THETA, rng=0,
        )
        if log is not None:
            log.append({"stage": "candidates", "kind": "dark" if black else "bright", "thin": thin})
        for (x0, y0), (x1, y1) in segments:
            seg_dir = float(np.degrees(np.arctan2(-(y1 - y0), x1 - x0)) % 180)
            t_rad = np.deg2rad(seg_dir)
            nc, nr = np.sin(t_rad), np.cos(t_rad)  # unit normal in (col, row)
            # snap the segment onto the ridge centre (Hough lines can sit a pixel or two off)
            shift = max(np.arange(-2, 2.01, 0.5), key=lambda k: _along(ridge, (y0 + k * nr, x0 + k * nc),
                                                                      (y1 + k * nr, x1 + k * nc)).mean())
            y0, x0, y1, x1 = y0 + shift * nr, x0 + shift * nc, y1 + shift * nr, x1 + shift * nc
            length = float(np.hypot(x1 - x0, y1 - y0))
            n = max(int(length // 4), 2)
            xs, ys = np.linspace(x0, x1, n), np.linspace(y0, y1, n)
            sides = []
            for sgn in (1, -1):
                cc = np.clip(np.round(xs + sgn * side * nc).astype(int), 0, w - 1)
                rr = np.clip(np.round(ys + sgn * side * nr).astype(int), 0, h - 1)
                sides.append((direction[rr, cc], coherence[rr, cc]))
            (d1, c1), (d2, c2) = sides
            ok = (c1 > 0.15) & (c2 > 0.15)
            if ok.sum() < 0.5 * n:
                if log is not None:
                    log.append({"stage": "line", "kind": "dark" if black else "bright", "start": (y0, x0),
                                "end": (y1, x1), "rejected": "side_coherence"})
                continue
            same_sides = axial.axial_diff(d1, d2) <= p["scratch_max_side_diff_deg"]
            mean_side = axial.resultant(np.concatenate([d1[ok], d2[ok]]))[0]
            crossing = axial.axial_diff(seg_dir, mean_side)
            # the line must stand out from its perpendicular neighbours (darker or brighter) along most of its length
            m = max(int(length), 2)
            centre = _along(smooth, (y0, x0), (y1, x1), m)
            one = _along(smooth, (y0 + 3 * nr, x0 + 3 * nc), (y1 + 3 * nr, x1 + 3 * nc), m)
            other = _along(smooth, (y0 - 3 * nr, x0 - 3 * nc), (y1 - 3 * nr, x1 - 3 * nc), m)
            # against each side on its own: a line beside a dark edge stands out from one side only
            signed = np.minimum(one, other) - centre if black else centre - np.maximum(one, other)
            contrast = float(np.mean(signed > p["scratch_min_contrast"] * spread))
            continuity = lath_continuity(smooth, (y0, x0), (y1, x1), mean_side, 3)
            tests = {"same_sides": float(np.mean(same_sides[ok])) >= 0.6,
                     "crossing": crossing >= p["scratch_min_crossing_deg"],
                     "contrast": contrast >= p["scratch_min_contrast_fraction"],
                     "continuity": continuity >= p["scratch_min_continuity"]}
            if log is not None:
                log.append({"stage": "line", "kind": "dark" if black else "bright", "start": (y0, x0), "end": (y1, x1),
                            "same_sides": float(np.mean(same_sides[ok])), "crossing": float(crossing),
                            "contrast": contrast, "continuity": continuity,
                            "rejected": ",".join(k for k, v in tests.items() if not v)})
            if all(tests.values()):
                # extend while the line itself stays darker (brighter) than both sides, then mark all of it:
                # on real texture the candidate map has only about half of a scratch's pixels
                # (and while the line map supports it: in noisy images the contrast test stops early)
                level = p["scratch_min_contrast"] * spread

                def farther(a, b, origin):
                    return a if np.hypot(a[0] - origin[0], a[1] - origin[1]) >= np.hypot(b[0] - origin[0], b[1] - origin[1]) else b

                start_new = farther(_extend_by_contrast(smooth, roi, (y1, x1), (y0, x0), black, level),
                                    _extend(thin, (y1, x1), (y0, x0)), (y1, x1))
                end_new = farther(_extend_by_contrast(smooth, roi, (y0, x0), (y1, x1), black, level),
                                  _extend(thin, (y0, x0), (y1, x1)), (y0, x0))
                (y0, x0), (y1, x1) = start_new, end_new
                length = float(np.hypot(x1 - x0, y1 - y0))
                rr, cc = draw_line(int(round(y0)), int(round(x0)), int(round(y1)), int(round(x1)))
                line = np.zeros(smooth.shape, bool)
                line[np.clip(rr, 0, h - 1), np.clip(cc, 0, w - 1)] = True
                found |= ndi.binary_dilation(line, iterations=2)  # 5 px: the blurred halo too
                items.append({"type": "scratch", "kind": "dark" if black else "bright",
                              "length_px": round(length, 1), "direction_deg": round(seg_dir, 1),
                              "crossing_deg": round(float(crossing), 1), "contrast": round(contrast, 2),
                              "continuity": round(float(continuity), 2),
                              "start": [int(round(y0)), int(round(x0))], "end": [int(round(y1)), int(round(x1))]})
    return found, items


def _along(img, start, end, n=None):
    """Image values sampled along a straight line (bilinear), every pixel unless n is given."""
    (r0, c0), (r1, c1) = start, end
    n = n or max(int(np.hypot(r1 - r0, c1 - c0)), 2)
    return ndi.map_coordinates(img, [np.linspace(r0, r1, n), np.linspace(c0, c1, n)], order=1, mode="nearest")


def lath_continuity(img, start, end, lath_deg, offset):
    """Correlation of the image on the two sides of a line, sampled along the flake direction:
    high when the same flakes continue across the line (a scratch on top of them),
    near zero when the flakes end at it (a grain or colony boundary)."""
    (r0, c0), (r1, c1) = start, end
    n = int(np.hypot(r1 - r0, c1 - c0))
    if n < 10:
        return 0.0
    rr, cc = np.linspace(r0, r1, n), np.linspace(c0, c1, n)
    t = np.deg2rad(lath_deg)
    dc, dr = np.cos(t), -np.sin(t)
    a = ndi.map_coordinates(img, [rr + offset * dr, cc + offset * dc], order=1, mode="nearest")
    b = ndi.map_coordinates(img, [rr - offset * dr, cc - offset * dc], order=1, mode="nearest")
    if a.std() < 1e-6 or b.std() < 1e-6:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _extend_by_contrast(img, roi, start, end, black: bool, level: float, step: int = 5, window: int = 15,
                        max_gap: int = 30):
    """Walk from `end` away from `start` along the line, in steps, while the next `window` px of the line are darker
    (black) or brighter than both sides 3 px away by `level` on at least half the samples; gaps up to max_gap px."""
    (r0, c0), (r1, c1) = start, end
    length = np.hypot(r1 - r0, c1 - c0)
    dr, dc = (r1 - r0) / length, (c1 - c0) / length
    nr, nc = dc, -dr  # unit normal
    h, w = img.shape
    r, c, last, gap = float(r1), float(c1), (r1, c1), 0
    while gap <= max_gap:
        ra, ca, rb, cb = r + dr, c + dc, r + window * dr, c + window * dc
        if not (0 <= rb < h and 0 <= cb < w) or not roi[int(rb), int(cb)]:
            break
        centre = _along(img, (ra, ca), (rb, cb), window)
        one = _along(img, (ra + 3 * nr, ca + 3 * nc), (rb + 3 * nr, cb + 3 * nc), window)
        other = _along(img, (ra - 3 * nr, ca - 3 * nc), (rb - 3 * nr, cb - 3 * nc), window)
        signed = np.minimum(one, other) - centre if black else centre - np.maximum(one, other)
        r, c = r + step * dr, c + step * dc
        if np.mean(signed > level) >= 0.5:
            last, gap = (r, c), 0
        else:
            gap += step
    return last


def _extend(support: np.ndarray, start, end, max_gap: int = 8):
    """Walk from `end` away from `start` along the line while `support` has pixels on it (gaps up to max_gap)."""
    (r0, c0), (r1, c1) = start, end
    length = np.hypot(r1 - r0, c1 - c0)
    dr, dc = (r1 - r0) / length, (c1 - c0) / length
    h, w = support.shape
    near = ndi.binary_dilation(support, iterations=1)
    r, c, last, gap = float(r1), float(c1), (r1, c1), 0
    while gap <= max_gap:
        r, c = r + dr, c + dc
        ri, ci = int(round(r)), int(round(c))
        if not (0 <= ri < h and 0 <= ci < w):
            break
        if near[ri, ci]:
            last, gap = (ri, ci), 0
        else:
            gap += 1
    return last


def inpaint_along(img: np.ndarray, hole: np.ndarray, direction: np.ndarray, max_reach: int = 30) -> np.ndarray:
    """Fill hole pixels from the image along the local flake direction: on each side, walk along the laths until
    the first pixel outside the hole, and interpolate linearly between the two by distance.
    A boundary the hole crosses is then continued through it, however shallow the crossing angle
    (a fixed sampling distance falls inside a band crossed at a shallow angle)."""
    out = img.copy()
    rr, cc = np.nonzero(hole)
    if rr.size == 0:
        return out
    t = np.deg2rad(direction[rr, cc])
    dc, dr = np.cos(t), -np.sin(t)
    h, w = img.shape
    edge_val = np.zeros((2, rr.size))
    edge_dist = np.full((2, rr.size), np.inf)
    for side, sgn in enumerate((1, -1)):
        pending = np.ones(rr.size, bool)
        for d in range(1, max_reach + 1):
            r2, c2 = rr + sgn * d * dr, cc + sgn * d * dc
            ri = np.clip(np.round(r2).astype(int), 0, h - 1)
            ci = np.clip(np.round(c2).astype(int), 0, w - 1)
            exit_now = pending & ~hole[ri, ci]
            if exit_now.any():
                # one pixel beyond the edge, to stay clear of the hole's blurred rim
                r3, c3 = r2[exit_now] + sgn * dr[exit_now], c2[exit_now] + sgn * dc[exit_now]
                edge_val[side, exit_now] = ndi.map_coordinates(img, [r3, c3], order=1, mode="nearest")
                edge_dist[side, exit_now] = d + 1
                pending &= ~exit_now
            if not pending.any():
                break
    found = np.isfinite(edge_dist)
    wgt = np.where(found, 1.0 / np.where(found, edge_dist, 1.0), 0.0)
    total = wgt.sum(axis=0)
    fallback = ndi.median_filter(np.where(hole, np.median(img), img), size=7)[rr, cc]
    out[rr, cc] = np.where(total > 0, (wgt * edge_val).sum(axis=0) / np.maximum(total, 1e-12), fallback)
    return out

def resolve(p: dict, prof: dict | None, mode: str) -> dict:
    """Fill in "auto" scale for one reading of the image: pixel params scaled to the flake width measured for that
    reading (explicitly given params are kept)."""
    q = dict(p)
    width, boundary = None, None
    if p["flake_width_px"] == "auto" and prof:
        width = prof["by_mode"][mode]["flake_width_px"]
        boundary = prof["by_mode"][mode]["boundary_width_px"] if mode == "boundaries" else None
    elif isinstance(p["flake_width_px"], (int, float)) and not isinstance(p["flake_width_px"], bool):
        width = float(p["flake_width_px"])
    if width:
        q.update(profile_mod.scaled_params(width, boundary, p.get("_given", ()), p))
    q["_width_used"] = width
    return q


def segment_boundaries(clean: np.ndarray, roi: np.ndarray, direction, coherence, p: dict) -> np.ndarray:
    """Flakes = regions between thin dark boundaries (clean: boundaries dark, scratches inpainted)."""
    bw = float(p["boundary_width_px"])
    ridge = sato(clean, sigmas=[max(bw / 3, 0.7), max(bw / 2, 1.0)], black_ridges=True)
    t = threshold_otsu(ridge[roi]) * p["boundary_sensitivity"]
    boundary = (ridge > t) | ~roi
    boundary = remove_small_objects(boundary, max_size=7)
    if p["bridge_gaps_px"] and p["bridge_gaps_px"] > 1:
        # bridge only from boundary pixels that run along the laths (their sides), never from lath ends:
        # end caps of two consecutive laths are a few px apart along the lath direction and would be joined,
        # filling short laths with boundary
        hrr, hrc, hcc = hessian_matrix(clean, sigma=max(bw / 2, 1.0), order="rc", use_gaussian_derivatives=True)
        line_dir = (0.5 * np.degrees(np.arctan2(-2 * hrc, hcc - hrr)) + 90) % 180  # dark lines
        sides = boundary & (axial.axial_diff(line_dir, direction) <= 30)
        boundary = boundary | directional_closing(sides, direction, coherence, int(p["bridge_gaps_px"]))
    return remove_small_holes(~boundary, max_size=11) & roi


def segment_regions(clean: np.ndarray, roi: np.ndarray, direction, coherence, p: dict) -> np.ndarray:
    """Flakes = regions of uniform grey level: boundaries are the steps between levels, as thin edges (Canny:
    gradient maxima across the step, hysteresis between 0.5 and 1 x Otsu of the gradient magnitude, times
    boundary_sensitivity), thickened to 3 px so that they separate, and bridged along the laths like the boundary
    reading's lines. A threshold on the gradient alone made wide bands at strong steps that swallowed thin laths."""
    from skimage.feature import canny

    sm = gaussian(clean, 1.0)
    magnitude = np.hypot(ndi.sobel(sm, axis=0), ndi.sobel(sm, axis=1))
    high = threshold_otsu(magnitude[roi]) * p["boundary_sensitivity"]
    edge = canny(clean, sigma=1.0, low_threshold=0.5 * high, high_threshold=high)
    edge = ndi.binary_dilation(edge) | ~roi
    if p["bridge_gaps_px"] and p["bridge_gaps_px"] > 1:
        edge = edge | directional_closing(edge, direction, coherence, int(p["bridge_gaps_px"]))
    return remove_small_holes(~edge, max_size=11) & roi


def segment_phase(clean: np.ndarray, roi: np.ndarray, p: dict, flake_phase: str) -> np.ndarray:
    """Flakes = regions of one phase (flake_phase: "bright" or "dark") in a matrix of the other."""
    img = clean if flake_phase == "bright" else 1.0 - clean
    spread = float(np.subtract(*np.percentile(img[roi], [95, 5])))
    t = threshold_otsu(img[roi]) + p["phase_offset"] * spread
    mask = (img > t) & roi
    small = max(int(p["min_area_px"]) // 3, 4)
    mask = remove_small_holes(mask, max_size=small)
    mask = remove_small_objects(mask, max_size=small)
    return mask


def split_regions(interior: np.ndarray, direction, coherence, p: dict) -> np.ndarray:
    distance = ndi.distance_transform_edt(interior)
    if p["h_px"] > 0:
        markers = label(h_maxima(distance, float(p["h_px"])).astype(bool) & interior)
        labels = skwatershed(-distance, markers, mask=interior)
        labels = merge_by_saddle(labels, distance, float(p["merge_saddle_ratio"]),
                                 float(p["merge_max_offaxis_deg"]), direction, coherence,
                                 float(p["merge_min_fill"]))
    else:
        labels = label(interior, connectivity=1)
    labels = remove_small_objects(labels, max_size=max(int(p["min_area_px"]) - 1, 0))
    labels, _, _ = relabel_sequential(labels)
    return labels


def accepted_coverage(flakes_list: list[dict], roi: np.ndarray) -> float:
    """Share of the region of interest in flakes used for the statistics (unbranched, oriented): how much of the
    image a segmentation explains as well-formed flakes. Used to choose between the two readings of an image."""
    return float(sum(f["area_px"] for f in flakes_list if f["used_for_orientation"]) / max(roi.sum(), 1))


def analyse(raw: np.ndarray, p: dict) -> dict:
    gray = raw[: raw.shape[0] - int(p["crop_bottom_px"])] if p["crop_bottom_px"] else raw
    roi = region_of_interest(gray, p["roi"])
    need_profile = (p["mode"] == "auto" or p["flake_width_px"] == "auto"
                    or (p["mode"] == "phase" and p["flake_phase"] == "auto"))
    prof = profile_mod.profile(gray, roi) if need_profile else None

    if p["mode"] == "auto":
        if prof["mode"] == "boundaries":
            modes, reason = ["boundaries"], "minority phase is a connected thin network"
        elif prof["mode"] == "phase":
            modes, reason = ["phase"], "minority phase is separate blobs about as thick as the matrix"
        else:
            modes, reason = ["boundaries"], "ambiguous profile (thin broken outlines and thin islands look alike): " \
                                            "boundary reading used; set mode to \"phase\" to measure one phase as flakes"
    else:
        modes, reason = [p["mode"]], "set by params"

    results = [analyse_mode(raw, gray, roi, resolve(p, prof, m), m, prof) for m in modes]
    best = max(results, key=lambda r: r["coverage"])
    best["summary"]["mode"] = best["mode"]
    best["summary"]["mode_reason"] = reason
    best["summary"]["coverage"] = round(best["coverage"], 3)
    q = best["params"]
    best["summary"]["flake_width_px_used"] = q["_width_used"]
    best["summary"]["params_resolved"] = {k: q[k] for k in (*profile_mod.SCALED, "boundary_width_px", "sigma")}
    if prof:
        best["summary"]["profile"] = prof
    return best


def analyse_mode(raw, gray, roi, p, mode, prof) -> dict:
    smooth0 = gaussian(gray, max(p["sigma"], 0.5))
    if mode == "regions":
        polarity, flake_phase = "regions", "all"  # every grey level holds flakes: no turning of the image
    elif mode == "boundaries":
        polarity = boundary_polarity(smooth0, roi) if p["polarity"] == "auto" else p["polarity"]
        flake_phase = "bright" if polarity == "dark_boundaries" else "dark"
    else:
        if p["flake_phase"] != "auto":
            flake_phase = p["flake_phase"]
        else:
            flake_phase = prof["by_mode"]["phase"]["flake_phase"] if prof else (
                "dark" if np.mean(smooth0[roi] < threshold_otsu(smooth0[roi])) <= 0.5 else "bright")
        polarity = "dark_boundaries" if flake_phase == "bright" else "bright_boundaries"
    img = gray if polarity in ("dark_boundaries", "regions") else 1.0 - gray  # from here on, flakes are bright
    fill = float(np.median(img[roi]))
    img = np.where(roi, img, fill)
    if p["flatten_px"] and p["flatten_px"] > 0:
        img = img - gaussian(img, p["flatten_px"]) + fill
    smooth = gaussian(img, p["sigma"]) if p["sigma"] > 0 else img

    direction, coherence = lath_direction_field(smooth, sigma_int=max(6.0, 3 * p["boundary_width_px"]))
    scratch, scratch_items = find_scratches(smooth, roi, direction, coherence, p)
    clean = inpaint_along(smooth, ndi.binary_dilation(scratch, iterations=1), direction) if scratch.any() else smooth

    if mode == "boundaries":
        interior = segment_boundaries(clean, roi, direction, coherence, p)
    elif mode == "regions":
        interior = segment_regions(clean, roi, direction, coherence, p)
    else:
        interior = segment_phase(clean, roi, p, "bright")  # img was turned so that the flakes are bright
    labels = split_regions(interior, direction, coherence, p)

    flakes_out = measure_flakes(labels, roi, scratch, p)
    summary = summarise(flakes_out, labels, smooth, roi, p)
    summary.update(polarity=polarity, flake_phase=flake_phase, n_scratches=len(scratch_items),
                   scratch_pixel_fraction=float(scratch[roi].mean()) if roi.any() else 0.0)
    defect_map = np.zeros(raw.shape, np.uint8)
    defect_map[: scratch.shape[0]][scratch] = SCRATCH
    full_labels = np.zeros(raw.shape, labels.dtype)
    full_labels[: labels.shape[0]] = labels
    return {"flakes": flakes_out, "summary": summary, "labels": full_labels, "defect_map": defect_map,
            "scratches": scratch_items, "scratch": scratch, "gray": gray, "roi": roi, "lab": labels,
            "mode": mode, "coverage": accepted_coverage(flakes_out, roi), "params": p}

def _line_footprint(length: int, angle_deg: float) -> np.ndarray:
    r = length // 2
    fp = np.zeros((2 * r + 1, 2 * r + 1), bool)
    t = np.deg2rad(angle_deg)
    for k in np.linspace(-r, r, 4 * r + 1):
        fp[int(round(r - k * np.sin(t))), int(round(r + k * np.cos(t)))] = True
    return fp


def directional_closing(boundary: np.ndarray, direction: np.ndarray, coherence: np.ndarray, length: int,
                        n_angles: int = 12) -> np.ndarray:
    """Close boundary gaps along the local flake direction: each pixel takes the closing with a line
    footprint at the angle bin of its own direction. Where the direction is unclear (low coherence)
    nothing is bridged."""
    out = boundary.copy()
    bins = np.round(direction / (180 / n_angles)).astype(int) % n_angles
    confident = coherence > 0.2
    for b in range(n_angles):
        where = (bins == b) & confident
        if where.any():
            closed = ndi.binary_closing(boundary, structure=_line_footprint(length, b * 180 / n_angles))
            out |= closed & where
    return out


def _piece_geometry(labels: np.ndarray, n: int):
    """Centroid (row, col), orientation (deg, project convention) and aspect ratio of every label, vectorised."""
    rr, cc = np.nonzero(labels)
    lab = labels[rr, cc]
    cnt = np.maximum(np.bincount(lab, minlength=n), 1).astype(float)
    mr, mc = np.bincount(lab, rr, n) / cnt, np.bincount(lab, cc, n) / cnt
    x, y = cc - mc[lab], -(rr - mr[lab])
    cxx = np.bincount(lab, x * x, n) / cnt + 1 / 12
    cyy = np.bincount(lab, y * y, n) / cnt + 1 / 12
    cxy = np.bincount(lab, x * y, n) / cnt
    theta = np.degrees(0.5 * np.arctan2(2 * cxy, cxx - cyy)) % 180
    common = np.sqrt(((cxx - cyy) / 2) ** 2 + cxy**2)
    lam1, lam2 = (cxx + cyy) / 2 + common, np.maximum((cxx + cyy) / 2 - common, 1e-9)
    return mr, mc, theta, np.sqrt(lam1 / lam2)


def merge_by_saddle(labels: np.ndarray, distance: np.ndarray, ratio: float, max_offaxis_deg: float = 90.0,
                    direction: np.ndarray | None = None, coherence: np.ndarray | None = None,
                    min_fill: float = 0.0) -> np.ndarray:
    """Merge touching regions whose pass (highest distance value on their shared border)
    is at least `ratio` times the smaller of their two peaks (largest distance value inside),
    and that lie end to end: the line between their centroids within max_offaxis_deg of the local flake
    direction (from the direction field if given, else from the more elongated piece; a short slice of a
    lath is wider than long, so its own orientation points across the lath).
    Two laths side by side, touching through a gap in the boundary between them, are not merged.
    A merge is also refused when the merged region would fill its same-moment ellipse less well than
    min_fill times the worse of its two pieces (a branched or L-shaped result is not one flake;
    two pieces of one lath joined end to end fill it as well as each piece)."""
    if ratio >= 1 or labels.max() < 2:
        return labels
    n = int(labels.max()) + 1
    peak = ndi.maximum(distance, labels, index=np.arange(n))
    peak = np.asarray(peak, float)
    mr, mc, theta, elong = _piece_geometry(labels, n)
    pairs, passes = [], []
    for a, b, da, db in ((labels[:, :-1], labels[:, 1:], distance[:, :-1], distance[:, 1:]),
                         (labels[:-1, :], labels[1:, :], distance[:-1, :], distance[1:, :])):
        m = (a != b) & (a > 0) & (b > 0)
        lo, hi = np.minimum(a[m], b[m]), np.maximum(a[m], b[m])
        pairs.append(lo.astype(np.int64) * n + hi)
        passes.append(np.minimum(da[m], db[m]))
    keys, values = np.concatenate(pairs), np.concatenate(passes)
    if keys.size == 0:
        return labels
    order = np.lexsort((-values, keys))
    keys, values = keys[order], values[order]
    first = np.r_[True, keys[1:] != keys[:-1]]
    keys, values = keys[first], values[first]  # the highest pass per pair
    a_ids, b_ids = keys // n, keys % n
    score = values / np.maximum(np.minimum(peak[a_ids], peak[b_ids]), 1e-9)
    if max_offaxis_deg < 90:
        link = np.degrees(np.arctan2(-(mr[b_ids] - mr[a_ids]), mc[b_ids] - mc[a_ids])) % 180
        if direction is not None:
            r_mid = np.clip(np.round((mr[a_ids] + mr[b_ids]) / 2).astype(int), 0, labels.shape[0] - 1)
            c_mid = np.clip(np.round((mc[a_ids] + mc[b_ids]) / 2).astype(int), 0, labels.shape[1] - 1)
            axis = direction[r_mid, c_mid]
            oriented = coherence[r_mid, c_mid] > 0.2 if coherence is not None else np.ones(len(axis), bool)
        else:  # the more elongated piece's direction (a round piece has none)
            axis = np.where(elong[a_ids] >= elong[b_ids], theta[a_ids], theta[b_ids])
            oriented = np.maximum(elong[a_ids], elong[b_ids]) >= 1.3
        score = np.where(oriented & (axial.axial_diff(link, axis) > max_offaxis_deg), 0.0, score)
    parent = np.arange(n)
    # moment sums per root, to check the shape of a merge before making it (moments add up)
    rr, cc = np.nonzero(labels)
    lab = labels[rr, cc]
    sums = np.stack([np.bincount(lab, w, n) for w in (np.ones(lab.size), rr, cc, rr * rr, cc * cc, rr * cc)], axis=1)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def fill(m):
        """Area / area of the same-moment ellipse: ~0.95-1 for a lath, lower for a branched or L-shaped region."""
        k, sr, sc, srr, scc, src = m
        vr, vc, cov = srr / k - (sr / k) ** 2 + 1 / 12, scc / k - (sc / k) ** 2 + 1 / 12, src / k - sr * sc / k**2
        det = max(vr * vc - cov**2, 1e-9)
        return k / (4 * np.pi * np.sqrt(det))

    for i in np.argsort(-score):
        if score[i] < ratio:
            break
        ra, rb = find(a_ids[i]), find(b_ids[i])
        if ra == rb:
            continue
        union = sums[ra] + sums[rb]
        if min_fill > 0 and fill(union) < min_fill * min(fill(sums[ra]), fill(sums[rb])):
            continue
        keep, gone = min(ra, rb), max(ra, rb)
        parent[gone] = keep
        sums[keep] = union
    roots = np.array([find(x) for x in range(n)])
    return roots[labels]


def is_branched(region: np.ndarray, arm_widths: float = 2.5) -> bool:
    """True when the region's skeleton has three or more arms longer than arm_widths x its half width.

    A single lath, straight or bent, has a skeleton that is one path (short spurs at corners and bumps are
    ignored); a Y, an H or two laths joined across a boundary have extra arms. Solidity cannot tell a bent
    lath from a branched region: the convex hull of an arc is much larger than the arc."""
    from skimage.morphology import skeletonize

    m = np.pad(region.astype(bool), 2)
    half = ndi.distance_transform_edt(m).max()
    skel = skeletonize(m)
    neighbours = ndi.convolve(skel.astype(int), np.ones((3, 3), int), mode="constant") - 1
    junctions = skel & (neighbours >= 3)
    if not junctions.any():
        return False
    ends = skel & (neighbours == 1)
    arms = skel & ~ndi.binary_dilation(junctions, structure=np.ones((3, 3), bool))
    lab, n = ndi.label(arms, structure=np.ones((3, 3), int))
    if n == 0:
        return False
    length = np.bincount(lab.ravel(), minlength=n + 1)
    has_end = np.bincount(lab[ends], minlength=n + 1) > 0
    long_arms = (length >= arm_widths * half) & has_end
    long_arms[0] = False
    return int(long_arms.sum()) >= 3


def measure_flakes(labels: np.ndarray, roi: np.ndarray, scratch: np.ndarray, p: dict) -> list[dict]:
    h, w = labels.shape
    outside = ~roi
    near_outside = ndi.binary_dilation(outside, iterations=2) if outside.any() else outside
    scratch_near = ndi.binary_dilation(scratch, iterations=2) if scratch.any() else scratch
    scale = p["um_per_px"]
    out = []
    for r in regionprops(labels):
        rr, cc = r.coords[:, 0], r.coords[:, 1]
        theta, major, minor = axial.moments_orientation(rr, cc)
        ar = major / minor if minor > 0 else float("inf")
        edge = bool(rr.min() == 0 or cc.min() == 0 or rr.max() == h - 1 or cc.max() == w - 1
                    or near_outside[rr, cc].any())
        solid = float(r.solidity)
        if p["branch_test"] == "skeleton":
            branched = solid < p["min_solidity"] and is_branched(r.image)  # only low-solidity ones need the test
        else:
            branched = solid < p["min_solidity"]
        shape_ok = not branched
        f = {
            "orientation_deg": round(theta, 2),
            "length_px": round(major, 2),
            "width_px": round(minor, 2),
            "aspect_ratio": round(ar, 3),
            "area_px": int(r.area),
            "solidity": round(solid, 3),
            "branched": bool(branched),
            "centroid_row": round(float(r.centroid[0]), 1),
            "centroid_col": round(float(r.centroid[1]), 1),
            "touches_edge": edge,
            "crossed_by_scratch": bool(scratch_near[rr, cc].any()),
            "used_for_orientation": bool(shape_ok and ar >= p["min_aspect_for_orientation"]),
            "used_for_shape": bool(shape_ok and not edge),
        }
        if scale:
            f["length_um"], f["width_um"] = round(major * scale, 3), round(minor * scale, 3)
        out.append(f)
    flag_gb_alpha(labels, out, p)
    return out


GB_ALPHA_NEIGHBOUR_MIN_ASPECT = 2.5


def flag_gb_alpha(labels: np.ndarray, flakes_list: list[dict], p: dict) -> None:
    """Mark grain-boundary alpha: flakes much longer than the typical flake whose direction differs from that of
    the flakes around them. They are drawn grey and left out of the statistics.
    Both conditions are needed: a long lath runs with its neighbours, and a short misaligned lath in a
    basketweave region is a lath, not a grain boundary."""
    for f in flakes_list:
        f["gb_alpha_like"] = False
    factor = p["gb_alpha_length_factor"]
    if not factor or len(flakes_list) < 10:
        return
    typical = np.median([f["length_px"] for f in flakes_list if f["used_for_orientation"]] or [0])
    if typical <= 0:
        return
    h, w = labels.shape
    for k, f in enumerate(flakes_list, start=1):
        if f["length_px"] < factor * typical or not f["used_for_orientation"]:
            continue
        r0, c0 = int(f["centroid_row"]), int(f["centroid_col"])
        half = int(f["length_px"]) // 2 + 10
        sl = (slice(max(r0 - half, 0), min(r0 + half, h)), slice(max(c0 - half, 0), min(c0 + half, w)))
        window = labels[sl]
        me = window == k
        ring = ndi.binary_dilation(me, iterations=int(max(f["width_px"], 4))) & ~me
        ids, counts = np.unique(window[ring], return_counts=True)
        keep = (ids > 0) & (ids != k)
        ids, counts = ids[keep], counts[keep]
        ok = [i for i in range(len(ids)) if flakes_list[ids[i] - 1]["used_for_orientation"]]
        # the neighbours must be laths (a colony that the long flake cuts across): long plates among blobby
        # neighbours (Widmanstaetten plates in pearlite, needles in a fine matrix) are the flakes themselves
        ok = [i for i in ok if flakes_list[ids[i] - 1]["aspect_ratio"] >= GB_ALPHA_NEIGHBOUR_MIN_ASPECT]
        if len(ok) < 4:
            continue
        theta = np.array([flakes_list[ids[i] - 1]["orientation_deg"] for i in ok])
        mean_dir, order = axial.resultant(theta, counts[ok])
        if order >= 0.5 and axial.axial_diff(f["orientation_deg"], mean_dir) > p["gb_alpha_min_misfit_deg"]:
            f["gb_alpha_like"] = True
            f["used_for_orientation"] = f["used_for_shape"] = False


def summarise(flakes_list: list[dict], labels, smooth, roi, p) -> dict:
    orient = [f for f in flakes_list if f["used_for_orientation"]]
    shape = [f for f in flakes_list if f["used_for_shape"]]
    theta = np.array([f["orientation_deg"] for f in orient])
    weights = np.array([f["area_px"] if p["weight"] == "area" else 1.0 for f in orient])
    s = axial.summary(theta, weights)
    if len(orient):
        mean_dir = s["mean_direction_deg"]
        for f in flakes_list:
            f["misalignment_deg"] = round(float(axial.axial_diff(f["orientation_deg"], mean_dir)), 2)

    # alignment inside tiles (colonies): mean of per-tile S, weighted by flake area in the tile
    tile = int(p["tile_px"])
    local, local_w = [], []
    if len(orient):
        rows_c = np.array([f["centroid_row"] for f in orient]) // tile
        cols_c = np.array([f["centroid_col"] for f in orient]) // tile
        for key in set(zip(rows_c, cols_c)):
            m = (rows_c == key[0]) & (cols_c == key[1])
            if m.sum() >= 5:
                local.append(axial.resultant(theta[m], weights[m])[1])
                local_w.append(weights[m].sum())

    # independent estimate: structure tensor of the whole region, gradient energy weighted
    arr, acc, aqq = structure_tensor(smooth, sigma=1.5, order="rc")
    jxx, jyy, jxy = aqq[roi].mean(), arr[roi].mean(), -acc[roi].mean()
    tensor_S = float(np.hypot(jxx - jyy, 2 * jxy) / (jxx + jyy + 1e-12))
    tensor_dir = float((0.5 * np.degrees(np.arctan2(2 * jxy, jxx - jyy)) + 90) % 180)

    def med(key, rows):
        return float(np.median([f[key] for f in rows])) if rows else None

    ar = [f["aspect_ratio"] for f in shape]
    area = [f["area_px"] for f in shape]
    out = {
        **s,
        "local_order_parameter": float(np.average(local, weights=local_w)) if local else None,
        "tensor_order_parameter": tensor_S,
        "tensor_direction_deg": tensor_dir,
        "median_aspect_ratio": med("aspect_ratio", shape),
        "mean_aspect_ratio": float(np.mean(ar)) if ar else None,
        "area_weighted_aspect_ratio": float(np.average(ar, weights=area)) if ar else None,
        "median_length_px": med("length_px", shape),
        "median_width_px": med("width_px", shape),
        "n_flakes": len(flakes_list),
        "n_used_orientation": len(orient),
        "n_used_shape": len(shape),
        "n_low_solidity": sum(f["solidity"] < p["min_solidity"] for f in flakes_list),
        "n_branched": sum(f["branched"] for f in flakes_list),
        "n_gb_alpha_like": sum(f.get("gb_alpha_like", False) for f in flakes_list),
        "flake_area_fraction": float((labels > 0)[roi].mean()) if roi.any() else None,
    }
    if p["um_per_px"]:
        out["median_length_um"] = out["median_length_px"] * p["um_per_px"] if shape else None
        out["median_width_um"] = out["median_width_px"] * p["um_per_px"] if shape else None
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


# --- drawing --------------------------------------------------------------------------------------------------

OVERLAY_MAX_WIDTH = 1024


def _hsv_to_rgb(hue: np.ndarray) -> np.ndarray:
    import matplotlib.colors as mcolors

    hsv = np.stack([hue, np.full_like(hue, 0.85), np.full_like(hue, 0.95)], axis=-1)
    return (mcolors.hsv_to_rgb(hsv) * 255).astype(np.uint8)


def save_flake_overlay(res: dict, path: Path) -> Path:
    gray, lab, flakes_list = res["gray"], res["lab"], res["flakes"]
    base = np.stack([gray * 255] * 3, axis=-1).astype(float)
    colours = np.zeros((len(flakes_list) + 1, 3), float)
    used = np.zeros(len(flakes_list) + 1, bool)
    hues = np.array([f["orientation_deg"] / 180 for f in flakes_list])
    if len(flakes_list):
        colours[1:] = _hsv_to_rgb(hues)
        used[1:] = [f["used_for_orientation"] for f in flakes_list]
        colours[1:][~used[1:]] = (110, 110, 110)
    paint = lab > 0
    rgb = base.copy()
    rgb[paint] = 0.35 * base[paint] + 0.65 * colours[lab[paint]]
    rgb[find_boundaries(lab, mode="inner") & paint] *= 0.45  # flake outlines
    scratch = res["scratch"]
    if scratch.any():
        rgb[ndi.binary_dilation(scratch, iterations=2)] = (0, 0, 0)
        rgb[scratch] = (255, 255, 255)
    rgb[~res["roi"]] = rgb[~res["roi"]] * 0.4
    out = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(out)
    s = res["summary"]
    h, w = gray.shape
    if s["mean_direction_deg"] is not None:
        t = np.deg2rad(s["mean_direction_deg"])
        L = 0.18 * min(h, w) * max(s["order_parameter"], 0.15)
        cx, cy = w / 2, h / 2
        for width, col in ((9, (0, 0, 0)), (5, (255, 255, 255))):
            draw.line([(cx - L * np.cos(t), cy + L * np.sin(t)), (cx + L * np.cos(t), cy - L * np.sin(t))],
                      fill=col, width=width)
        text = (f"mean {s['mean_direction_deg']:.0f} deg  S {s['order_parameter']:.2f}  "
                f"local S {s['local_order_parameter'] or 0:.2f}  AR {s['median_aspect_ratio'] or 0:.1f}  "
                f"n {s['n_used_orientation']}  scratches {s['n_scratches']}")
        draw.rectangle([0, 0, 8 + 6 * len(text), 16], fill=(0, 0, 0))
        draw.text((4, 3), text, fill=(255, 255, 255))
    # colour key: a half wheel, angle = flake direction
    R = max(min(h, w) // 12, 24)
    ox, oy = w - R - 8, h - 8
    for a in range(0, 180, 3):
        t = np.deg2rad(a)
        c = tuple(int(v) for v in _hsv_to_rgb(np.array([a / 180.0]))[0])
        draw.line([(ox, oy), (ox + R * np.cos(t), oy - R * np.sin(t))], fill=c, width=3)
    scale = OVERLAY_MAX_WIDTH / out.width
    if scale < 1:
        out = out.resize((OVERLAY_MAX_WIDTH, round(out.height * scale)))
    path = Path(path)
    path = path.parent / (path.name + ".jpg")  # (with_suffix cut names like "L08__10.3390_x" to "L08__10.jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path, quality=88)
    return path

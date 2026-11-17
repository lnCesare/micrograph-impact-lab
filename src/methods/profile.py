"""Image profiling: what kind of flake image this is, and at what scale. Reads the image only.

Used by the flakes method (params "mode" and "flake_width_px" set to "auto"), by the twin generator
(to make code-generated images that look like the user's), and by the knowledge memory (to find similar
image sets measured before).

Measurements, on a rough two-phase split (Otsu on the flattened, lightly smoothed image):
- mode: "boundaries" when the minority phase is thin and connected (a network of boundaries between flakes,
  e.g. etched alpha + beta titanium), "phase" when it is separate blobs (flakes of one phase in a matrix,
  e.g. pearlite islands, two-phase bands);
- flake_width_px, boundary_width_px: median thickness (2 x distance to the other phase, on the skeleton)
  of the flake phase and of the boundary / matrix phase;
- flake_phase: "bright" or "dark", which side of the split the flakes are on;
- noise (robust pixel noise), contrast (5-95 % range), coherence (alignment of the texture, 0-1).
"""

import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from skimage.filters import gaussian, threshold_otsu
from skimage.morphology import remove_small_objects, skeletonize

# Widths, measured by this profiler (boundary reading), on the images the method's default params were tuned on:
# the 13 lamellar Ti-6Al-4V dev images (1292 x 968 px), medians. Holdout images not used.
REFERENCE_FLAKE_WIDTH_PX = 10.0
REFERENCE_BOUNDARY_WIDTH_PX = 5.66
DEFAULT_BOUNDARY_WIDTH_PARAM = 2.5  # the method's boundary_width_px (Sato scale) at the reference


def to_gray(im: np.ndarray) -> np.ndarray:
    """Grayscale in [0, 1]. A colour image is projected on its axis of largest colour variance:
    colour etchings (blue / purple / yellow phases) often differ in hue more than in luminance."""
    if im.ndim == 2:
        return im.astype(float) / (65535.0 if im.dtype == np.uint16 else 255.0)
    rgb = im[..., :3].astype(float) / (65535.0 if im.dtype == np.uint16 else 255.0)
    flat = rgb.reshape(-1, 3)
    sample = flat[:: max(flat.shape[0] // 200000, 1)]
    centred = sample - sample.mean(axis=0)
    if np.abs(centred).max() < 1e-6:
        return rgb.mean(axis=-1)
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    axis = vt[0] / np.linalg.norm(vt[0])
    lum_w = np.array([0.2125, 0.7154, 0.0721])
    lum = rgb @ lum_w
    # keep luminance unless the best colour axis carries clearly more contrast (colour etchings)
    gain = np.std(centred @ axis) / max(np.std(centred @ (lum_w / np.linalg.norm(lum_w))), 1e-9)
    if gain < 1.3:
        return lum
    if np.dot(axis, lum_w) < 0:
        axis = -axis
    proj = rgb @ axis
    lo, hi = np.percentile(proj, [0.5, 99.5])
    return np.clip((proj - lo) / max(hi - lo, 1e-9), 0, 1)


def load(path) -> np.ndarray:
    return to_gray(np.array(Image.open(path)))


def flat_regions(gray: np.ndarray, min_fraction: float = 0.005) -> np.ndarray:
    """Large regions with almost no texture: white or black padding around a figure panel, flat label boxes.
    Holes inside them (the text of a label) are included."""
    m = gaussian(gray, 1.0)
    local_var = ndi.uniform_filter(m * m, 9) - ndi.uniform_filter(m, 9) ** 2
    # flat = far below the image's own pixel noise (padding and label boxes are digitally flat): an absolute
    # threshold alone took the smooth interiors of wide flakes in low-noise images for padding
    resid = gray - gaussian(gray, 1.0)
    noise = 1.4826 * float(np.median(np.abs(resid - np.median(resid))))
    flat = local_var < min(0.004, 0.3 * noise) ** 2
    flat = ndi.binary_opening(flat, iterations=2)
    # (a) margins: whole flat rows / columns running in from the image border (padding around a panel); taken off
    # first, so that saturated flakes touching the panel edge do not join them into one irregular region
    margins = np.zeros(gray.shape, bool)
    for axis in (0, 1):
        full = flat.mean(axis=1 - axis) >= 0.95
        for run in (full, full[::-1]):
            k = int(np.argmin(run)) if not run.all() else run.size
            if k:
                idx = slice(0, k) if run is full else slice(run.size - k, run.size)
                if axis == 0:
                    margins[idx, :] = True
                else:
                    margins[:, idx] = True
    flat &= ~margins
    lab, n = ndi.label(flat)
    if n == 0:
        return ndi.binary_dilation(margins, iterations=3) if margins.any() else margins
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_fraction * gray.size
    keep[0] = False
    # padding runs along the image border, label boxes are rectangles; a flat region that is neither is a
    # saturated (clipped) flake or a smooth phase region of the specimen, and stays
    h, w = gray.shape
    band = max(int(0.06 * min(h, w)), 4)
    near_border = np.zeros(gray.shape, bool)
    near_border[:band], near_border[-band:], near_border[:, :band], near_border[:, -band:] = True, True, True, True
    border_share = np.bincount(lab[near_border], minlength=n + 1) / np.maximum(sizes, 1)
    for k, sl in enumerate(ndi.find_objects(lab), start=1):
        if not keep[k] or sl is None:
            continue
        filled = ndi.binary_fill_holes(lab[sl] == k)
        extent = filled.sum() / filled.size
        if border_share[k] < 0.6 and extent < 0.8:
            keep[k] = False
    boxes = ndi.binary_fill_holes(ndi.binary_dilation(keep[lab], iterations=3))  # the text in label boxes
    return boxes | ndi.binary_dilation(margins, iterations=3)  # (a frame of margins encloses the panel: no filling)


def _border_contact(lab: np.ndarray, n: int) -> np.ndarray:
    edge = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    return np.bincount(edge, minlength=n + 1)


def resin_regions(gray: np.ndarray) -> np.ndarray:
    """Mounting resin (or the dark background of a specimen edge): regions much darker than the specimen, with
    less texture, lying along a long stretch of the image border. Darker phases of the specimen are textured or
    do not line the border (they used to be cut off with the old rule "keep the largest bright region")."""
    coarse = gaussian(gray, 8)
    t = threshold_otsu(coarse)
    dark, bright = coarse < t, coarse >= t
    if not (dark.any() and bright.any()):
        return np.zeros(gray.shape, bool)
    texture = gaussian(np.abs(gray - gaussian(gray, 1.5)), 4)
    level, tex = float(np.median(coarse[bright])), float(np.median(texture[bright]))
    if level <= 0 or tex <= 0:
        return np.zeros(gray.shape, bool)
    min_contact, min_size = 0.25 * min(gray.shape), 0.005 * gray.size
    resin = np.zeros(gray.shape, bool)
    # whole dark regions of the coarse split: clearly darker and smoother than the specimen
    lab, n = ndi.label(dark)
    sizes, contact = np.bincount(lab.ravel(), minlength=n + 1), _border_contact(lab, n)
    for k in np.flatnonzero((sizes >= min_size) & (contact >= min_contact)):
        if k == 0:
            continue
        m = lab == k
        if np.median(coarse[m]) < 0.3 * level and np.median(texture[m]) < 0.65 * tex:
            resin |= m
    # pixel by pixel, for a resin band that the coarse split joined with a darker part of the specimen
    cand = ndi.binary_opening((coarse < 0.35 * level) & (texture < 0.5 * tex) & dark, iterations=2)
    lab, n = ndi.label(cand)
    if n:
        sizes, contact = np.bincount(lab.ravel(), minlength=n + 1), _border_contact(lab, n)
        keep = (sizes >= min_size) & (contact >= min_contact)
        keep[0] = False
        resin |= keep[lab]
    if not resin.any():
        return resin
    # pores and debris inside the resin, its ragged edge
    return ndi.binary_fill_holes(ndi.binary_closing(resin, iterations=6)) & dark


def data_bar(gray: np.ndarray, k: int = 3) -> np.ndarray:
    """An instrument data bar (SEM: voltage, magnification, scale bar) along the bottom of the image: below a sharp
    straight edge that spans >= 85 % of the width within the bottom fifth. Its boxes hold text, so they are not flat,
    and the flat-region rule missed it (the scientist's SEM image: the bar was measured as flakes)."""
    h, w = gray.shape
    out = np.zeros(gray.shape, bool)
    if h < 8 * k:
        return out
    s = gaussian(gray, 1.0)
    c = np.cumsum(np.vstack([np.zeros((1, w)), s]), axis=0)
    r = np.arange(k, h - k)
    step = np.abs((c[r + k] - c[r]) / k - (c[r] - c[r - k]) / k)  # mean of k rows below vs k rows above, per column
    t = max(0.08, 2 * float(np.median(step)))
    rows = r[((step > t).mean(axis=1) >= 0.85) & (r > 0.8 * h)]
    if rows.size:
        out[max(int(rows.min()) - 2, 0):] = True
    return out


def region_of_interest(gray: np.ndarray, mode: str = "auto") -> np.ndarray:
    """Pixels of the specimen: drops dark mounting resin (resin_regions), flat padding and label boxes
    (flat_regions), and keeps away from their edges."""
    if mode == "full":
        return np.ones(gray.shape, bool)
    roi = ~resin_regions(gray) & ~data_bar(gray)
    flat = flat_regions(gray)
    if flat.any() and flat.mean() < 0.7:
        roi &= ~flat
    if roi.mean() < 0.05:  # nothing sensible left: fall back to the whole image
        return np.ones(gray.shape, bool)
    return ndi.binary_erosion(roi, iterations=6) if not roi.all() else roi


def _thickness(phase: np.ndarray, roi: np.ndarray) -> float:
    """Median thickness of a binary phase: 2 x the distance to the other phase, sampled on its skeleton
    (away from the edge of the region of interest)."""
    phase = remove_small_objects(phase, max_size=4)
    if phase.sum() < 50:
        return float("nan")
    dist = ndi.distance_transform_edt(phase)
    skel = skeletonize(phase) & ndi.binary_erosion(roi, iterations=3)
    values = 2 * dist[skel]
    return float(np.median(values)) if values.size else float("nan")


def _connectivity(phase: np.ndarray) -> float:
    """Area fraction of a binary phase in its largest connected piece: ~1 for a network or matrix, low for islands."""
    lab, n = ndi.label(remove_small_objects(phase, max_size=4))
    sizes = np.bincount(lab.ravel())[1:] if n else np.array([0])
    return float(sizes.max() / max(sizes.sum(), 1))


def mode_hint(connectivity: float, ratio: float) -> str:
    """"boundaries" when the minority phase is a connected thin network, "phase" when it is separate blobs about as
    thick as the matrix, "ambiguous" otherwise (broken thin outlines and thin islands look alike: the flakes method
    then tries both readings)."""
    if connectivity >= 0.6 and np.isfinite(ratio) and ratio <= 0.8:
        return "boundaries"
    if connectivity < 0.35 and np.isfinite(ratio) and ratio >= 0.75:
        return "phase"
    return "ambiguous"


def profile(gray: np.ndarray, roi: np.ndarray | None = None) -> dict:
    roi = region_of_interest(gray) if roi is None else roi
    h, w = gray.shape
    fill = float(np.median(gray[roi]))

    def split(flatten_sigma):
        img = np.where(roi, gray, fill)
        img = img - gaussian(img, flatten_sigma) + fill
        smooth = gaussian(img, 1.0)
        t = threshold_otsu(smooth[roi])
        dark = (smooth < t) & roi
        bright = (smooth >= t) & roi
        dark_frac = dark.sum() / max(roi.sum(), 1)
        minority, majority = (dark, bright) if dark_frac <= 0.5 else (bright, dark)
        return smooth, dark_frac, minority, majority, _thickness(minority, roi), _thickness(majority, roi)

    # background flattening is a high-pass: its scale must sit well above the structure, or wide flakes on a small
    # image are flattened away (and measure thin); a second pass when the first one finds wide structures
    sigma = max(h, w) / 16
    smooth, dark_frac, minority, majority, t_min, t_maj = split(sigma)
    widest = np.nanmax([t_min, t_maj, 0.0])
    if 6 * widest > sigma:
        sigma = 6 * widest
        smooth, dark_frac, minority, majority, t_min, t_maj = split(sigma)
    minority_name = "dark" if dark_frac <= 0.5 else "bright"
    connectivity = _connectivity(minority)
    ratio = t_min / t_maj if np.isfinite(t_min) and np.isfinite(t_maj) and t_maj > 0 else float("nan")
    other = {"dark": "bright", "bright": "dark"}
    # phase reading: the flakes are the minority phase; when both phases cover about the same area the minority is a
    # coin flip, and the flakes are the phase that is separate islands (less connected), the matrix the connected one
    island_phase, majority_connectivity = minority_name, _connectivity(majority)
    if abs(dark_frac - 0.5) <= 0.08 and majority_connectivity < connectivity - 0.05:
        island_phase = other[minority_name]
    t_island, t_matrix = (t_min, t_maj) if island_phase == minority_name else (t_maj, t_min)
    from skimage.feature import canny

    magnitude = np.hypot(ndi.sobel(smooth, axis=0), ndi.sobel(smooth, axis=1))
    high = threshold_otsu(magnitude[roi])
    steps = ndi.binary_dilation(canny(smooth, sigma=1.0, low_threshold=0.5 * high, high_threshold=high)) & roi
    by_mode = {
        "boundaries": {"flake_width_px": t_maj, "boundary_width_px": t_min, "flake_phase": other[minority_name]},
        "phase": {"flake_width_px": t_island, "boundary_width_px": t_matrix, "flake_phase": island_phase},
        # regions of uniform level between intensity steps (the regions reading; never chosen automatically)
        "regions": {"flake_width_px": _thickness(~steps & roi, roi), "boundary_width_px": None, "flake_phase": "all"},
    }
    mode = mode_hint(connectivity, ratio)
    flake_w = by_mode["phase" if mode == "phase" else "boundaries"]["flake_width_px"]
    # noise: robust std of the difference to a 1 px gaussian, contrast: 5-95 % range
    resid = (gray - gaussian(gray, 1.0))[roi]
    noise = float(1.4826 * np.median(np.abs(resid - np.median(resid))))
    contrast = float(np.subtract(*np.percentile(gray[roi], [95, 5])))
    from skimage.feature import structure_tensor

    arr, acc, aqq = structure_tensor(smooth, sigma=max(2.0, flake_w if np.isfinite(flake_w) else 5.0), order="rc")
    jxx, jyy, jxy = aqq[roi], arr[roi], -acc[roi]
    coherence = float(np.median(np.hypot(jxx - jyy, 2 * jxy) / (jxx + jyy + 1e-12)))
    rnd = lambda v: round(float(v), 2) if v is not None and np.isfinite(v) else None
    return {
        "mode": mode,  # "boundaries" | "phase" | "ambiguous"
        "by_mode": {m: {k: (rnd(v) if k != "flake_phase" else v) for k, v in d.items()} for m, d in by_mode.items()},
        "flake_phase": by_mode["phase" if mode == "phase" else "boundaries"]["flake_phase"],
        "flake_width_px": rnd(flake_w),
        "boundary_width_px": rnd(by_mode["phase" if mode == "phase" else "boundaries"]["boundary_width_px"]),
        "minority_connectivity": round(connectivity, 3),
        "majority_connectivity": round(majority_connectivity, 3),
        "thickness_ratio": round(float(ratio), 3) if np.isfinite(ratio) else None,
        "minority_fraction": round(float(min(dark_frac, 1 - dark_frac)), 3),
        "noise": round(noise, 4),
        "contrast": round(contrast, 4),
        "noise_to_contrast": round(noise / max(contrast, 1e-6), 4),
        "coherence": round(coherence, 3),
        "roi_fraction": round(float(roi.mean()), 3),
        "flatten_sigma_px": round(float(sigma), 1),
        "shape": [h, w],
    }


SCALED = {  # param: (default value at the reference flake width, power of the scale factor, min, max)
    "flatten_px": (40.0, 1, 12.0, 400.0),
    "min_area_px": (30, 2, 6, 5000),
    "bridge_gaps_px": (9, 1, 3, 40),
    "h_px": (0.5, 1, 0.3, 4.0),
    "tile_px": (160, 1, 48, 1200),
    "scratch_min_length_px": (200, 1, 60, 2000),
    "scratch_line_gap_px": (12, 1, 4, 40),
}


DEAD_ZONE_LOW = 0.5  # the defaults work for flakes down to about half the reference width (measured)


def effective_scale(flake_width_px: float) -> float:
    """Scale factor for the pixel params, from the flake width relative to the reference.
    Coarser images: proportional (a 3x magnified image needs 2-3x the pixel params, measured).
    Finer images: 1 down to half the reference width (the defaults were measured to work there: code-generated laths
    4-18 px, Ti64 8-11 px; scaling down only added the noise of the width estimate), proportional to the excess below.
    Below ~4 px per flake the boundaries are under the resolution: shapes lose accuracy whatever the scale
    (tests/test_profile.py), orientation does not."""
    r = float(np.clip(flake_width_px / REFERENCE_FLAKE_WIDTH_PX, 0.05, 20.0))
    if r >= 1.0:
        return r
    return 1.0 if r >= DEAD_ZONE_LOW else r / DEAD_ZONE_LOW


def scaled_params(flake_width_px: float, boundary_width_px: float | None, given, current: dict | None = None) -> dict:
    """Pixel params for an image whose flakes are flake_width_px wide; params named in `given` are left alone
    (their values are read from `current`)."""
    given = set(given)
    current = current or {}
    s = effective_scale(flake_width_px)
    out = {}
    for key, (base, power, lo, hi) in SCALED.items():
        if key not in given:
            v = float(np.clip(base * s**power, lo, hi))
            out[key] = int(round(v)) if isinstance(base, int) else round(v, 2)
    if "boundary_width_px" not in given:
        if boundary_width_px:  # measured: keep the ratio to the reference, with the same dead zone
            b = DEFAULT_BOUNDARY_WIDTH_PARAM * effective_scale(
                boundary_width_px * REFERENCE_FLAKE_WIDTH_PX / REFERENCE_BOUNDARY_WIDTH_PX)
        else:  # phase reading (no boundary lines): only the scratch / ridge scale, follows the flake width
            b = DEFAULT_BOUNDARY_WIDTH_PARAM * s
        out["boundary_width_px"] = round(float(np.clip(b, 0.8, 10.0)), 2)
    if "sigma" not in given:
        bw = out.get("boundary_width_px", current.get("boundary_width_px", 2.5))
        out["sigma"] = round(float(np.clip(0.4 * bw, 0.5, 4.0)), 2)
    return out

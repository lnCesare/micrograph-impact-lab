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


def out_of_focus(gray: np.ndarray, roi: np.ndarray, min_fraction: float = 0.1, ratio_core: float = 0.72,
                 contrast_core: float = 0.72, ratio_grow: float = 0.8, contrast_grow: float = 0.82) -> np.ndarray:
    """Out-of-focus areas, from two cues that must hold together, each averaged over windows of ~1/16 image:
    - sharpness: at the edges (gradient at 3 px above half its Otsu threshold), the fine-scale gradient (1 px) over
      the coarse one is about 2 where the image is sharp and drops towards 1 where it is blurred: below 0.72 x the
      image's own sharp part (90th percentile);
    - contrast: blur erases thin boundary lines, so the local contrast (std at 1 px) drops below 0.72 x the image's
      75th percentile. Without it, sharp areas of broad step edges (large plates, ferrite grains) read as blurred.
    Confident cores of at least min_fraction (10 %) of the image are grown over the transition (0.8 x sharpness,
    0.82 x contrast). Relative to the image itself: a uniformly soft image (an enlarged one) has no out-of-focus area.
    The thresholds flag none of the 47 real images at hand (Ti64, the lamellar set, the scientist's SEM image) and
    remove 62-81 % of the blurred area in 7 of 8 coarse-cell twin images: some blur is missed rather than sharp
    specimen cut away (with 0.8 x contrast, a third of the SEM specimen was taken for blurred).
    (Failure 6 of the private coarse-cell test: a blurred half-image measured with the rest put one image 7 deg off.)"""
    h, w = gray.shape
    out = np.zeros(gray.shape, bool)
    if roi.sum() < 1000:
        return out
    g1 = ndi.gaussian_gradient_magnitude(gray, 1.0)
    g3 = ndi.gaussian_gradient_magnitude(gray, 3.0)
    edges = (g3 > 0.5 * threshold_otsu(g3[roi])) & roi  # (blurred edges are weaker: a low threshold keeps them)
    if edges.sum() < 500:
        return out
    win = max(24.0, min(h, w) / 16)
    num = gaussian(np.where(edges, g1 / (g3 + 1e-9), 0.0), win)
    den = gaussian(edges.astype(float), win)
    have = (den > 0.01) & roi
    if have.sum() < 1000:
        return out
    ratio = np.where(have, num / np.maximum(den, 1e-9), np.nan)
    sharp = np.nanpercentile(ratio[have], 90)
    sm = gaussian(np.where(roi, gray, float(np.median(gray[roi]))), 1.0)
    std = np.sqrt(np.maximum(gaussian(sm ** 2, win) - gaussian(sm, win) ** 2, 0))
    ref = np.percentile(std[have], 75)
    core = ndi.binary_opening(have & (ratio < ratio_core * sharp) & (std < contrast_core * ref), iterations=3)
    lab_c, n_c = ndi.label(core)
    if not n_c:
        return out
    sizes_c = np.bincount(lab_c.ravel())
    big = sizes_c >= min_fraction * gray.size
    big[0] = False
    core = big[lab_c]
    grow = have & (ratio < ratio_grow * sharp) & (std < contrast_grow * ref)
    lab_g, _ = ndi.label(grow)
    return np.isin(lab_g, np.unique(lab_g[core & (lab_g > 0)])) & (lab_g > 0)


def textured_regions(gray: np.ndarray, roi: np.ndarray, flake_width: float | None) -> np.ndarray:
    """Patches of fine speckle texture that are not flakes (grey "pockets" at triple junctions of coarse cells):
    a high density of small intensity extrema (5 px neighbourhood, prominence > 3 x the pixel noise; > 4 x the
    image's median density and > 0.004 per px, in a window of 0.15 x the flake width) where the texture has no
    direction (structure-tensor coherence < 0.3: boundary lines are coherent), kept as blobs of at least
    (0.2 x the flake width)^2. Only for coarse structures (flakes >= 24 px wide): at finer scales the speckle cannot
    be told from the structure itself (fine lamellae, pearlite). (Failure 4 of the private coarse-cell test.)"""
    out = np.zeros(gray.shape, bool)
    if not flake_width or not np.isfinite(flake_width) or flake_width < 24 or roi.sum() < 1000:
        return out
    from skimage.feature import structure_tensor

    si = 0.15 * flake_width
    sm = gaussian(gray, 1.2)
    resid = gray - gaussian(gray, 1.0)
    noise = 1.4826 * float(np.median(np.abs(resid - np.median(resid))))
    local = gaussian(gray, 4.0)
    extrema = (((sm == ndi.maximum_filter(sm, 5)) & (sm - local > 3 * noise))
               | ((sm == ndi.minimum_filter(sm, 5)) & (local - sm > 3 * noise))) & roi
    density = gaussian(extrema.astype(float), si)
    arr, acc, aqq = structure_tensor(gaussian(gray, 1.0), sigma=si, order="rc")
    coherence = np.hypot(aqq - arr, 2 * acc) / (aqq + arr + 1e-12)
    t = max(0.004, 4 * float(np.median(density[roi])))
    tex = (density > t) & (coherence < 0.3) & roi
    tex = ndi.binary_opening(tex, iterations=2)
    lab, n = ndi.label(tex)
    if n:
        sizes = np.bincount(lab.ravel())
        keep = sizes >= (0.2 * flake_width) ** 2
        keep[0] = False
        out = ndi.binary_dilation(keep[lab], iterations=3) & roi
    return out


def region_of_interest(gray: np.ndarray, mode: str = "auto", info: dict | None = None) -> np.ndarray:
    """Pixels of the specimen: drops dark mounting resin (resin_regions), an instrument data bar (data_bar), flat
    padding and label boxes (flat_regions) and out-of-focus areas (out_of_focus), and keeps away from their edges.
    With a dict as `info`, the out-of-focus fraction of the image is written into it."""
    if info is not None:
        info["out_of_focus_fraction"] = 0.0
    if mode == "full":
        return np.ones(gray.shape, bool)
    roi = ~resin_regions(gray) & ~data_bar(gray)
    flat = flat_regions(gray)
    if flat.any() and flat.mean() < 0.7:
        roi &= ~flat
    blurred = out_of_focus(gray, roi)
    if blurred.any() and blurred.mean() < 0.7:
        roi &= ~blurred
        if info is not None:
            info["out_of_focus_fraction"] = round(float(blurred.mean()), 4)
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


def boundary_spacing(gray: np.ndarray, roi: np.ndarray, dark_lines: bool = True, exclude: np.ndarray | None = None,
                     n_lines: int = 240, seed: int = 0) -> dict:
    """Spacing of the boundary lines across the flakes: thin lines (Sato ridges, 1-3 px) that run along the local
    flake direction (within 25 deg of the coarse structure-tensor direction) are crossed by scan lines laid across
    that direction; the spacing is the most common distance between consecutive crossings (log-histogram peak,
    refined by the median within +-25 %). Dots and pores (short, any direction), pocket texture (short ridges in all
    directions) and the boundaries between flakes stacked end to end (across the direction) drop out of the line map;
    a missing stretch of boundary makes one spacing twice as long, which moves the median but not the peak.
    Also returned: the mean intercept (scan length / crossings), which a broken boundary biases up and stray lines
    bias down, as a cross-check."""
    h, w = gray.shape
    valid = roi.copy() if exclude is None else roi & ~exclude
    out = {"spacing_px": None, "intercept_px": None, "n_spacings": 0, "peak_share": 0.0, "reliable": False}
    if valid.sum() < 1000:
        return out
    from skimage.feature import hessian_matrix, structure_tensor
    from skimage.filters import sato

    sm = gaussian(gray, 1.0)
    ridge = sato(sm, sigmas=[1.0, 2.0, 3.0], black_ridges=dark_lines)
    t = threshold_otsu(ridge[valid])
    lines = (ridge > t) & valid
    hrr, hrc, hcc = hessian_matrix(sm, sigma=2.0, order="rc", use_gaussian_derivatives=True)
    principal = 0.5 * np.degrees(np.arctan2(-2 * hrc, hcc - hrr))
    line_dir = (principal + 90) % 180 if dark_lines else principal % 180
    arr, acc, aqq = structure_tensor(sm, sigma=max(8.0, min(h, w) / 40), order="rc")
    tex_dir = (0.5 * np.degrees(np.arctan2(-2 * acc, aqq - arr)) + 90) % 180
    d = np.abs((line_dir - tex_dir) % 180)
    along = lines & (np.minimum(d, 180 - d) <= 25)
    along = remove_small_objects(along, max_size=15)
    rng = np.random.default_rng(seed)
    starts = np.flatnonzero(valid.ravel())
    starts = rng.choice(starts, size=min(n_lines, starts.size), replace=False)
    reach = int(min(h, w) * 0.6)
    steps = np.arange(-reach, reach + 1)
    spacings, total_len, crossings = [], 0, 0
    for st in starts:
        r0, c0 = divmod(int(st), w)
        theta = np.deg2rad(tex_dir[r0, c0])
        nr, nc = np.cos(theta), np.sin(theta)  # across the flakes, in (row, col)
        rr = np.round(r0 + steps * nr).astype(int)
        cc = np.round(c0 + steps * nc).astype(int)
        inside = (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
        ok = np.zeros(steps.size, bool)
        ok[inside] = valid[rr[inside], cc[inside]]
        # the contiguous valid stretch through the start point
        centre = reach
        lo = centre
        while lo > 0 and ok[lo - 1]:
            lo -= 1
        hi = centre
        while hi < steps.size - 1 and ok[hi + 1]:
            hi += 1
        if hi - lo < 20:
            continue
        seg = along[rr[lo:hi + 1], cc[lo:hi + 1]]
        rises = np.flatnonzero(np.diff(seg.astype(np.int8)) == 1) + 1
        falls = np.flatnonzero(np.diff(seg.astype(np.int8)) == -1) + 1
        if seg[0]:
            rises = np.r_[0, rises]
        if seg[-1]:
            falls = np.r_[falls, seg.size]
        centres = (rises + falls[: rises.size]) / 2.0
        total_len += hi - lo + 1
        crossings += centres.size
        if centres.size >= 2:
            spacings.extend(np.diff(centres).tolist())
    s = np.asarray([v for v in spacings if v >= 3])
    out["n_spacings"] = int(s.size)
    if crossings:
        out["intercept_px"] = round(float(total_len / crossings), 2)
    if s.size < 10:
        return out
    edges = np.logspace(np.log10(3), np.log10(s.max() + 1), 60)
    counts, _ = np.histogram(s, edges)
    smooth_counts = np.convolve(counts, [1, 2, 3, 2, 1], mode="same")
    k = int(np.argmax(smooth_counts))
    peak = float(np.sqrt(edges[k] * edges[k + 1]))
    near = s[(s >= 0.75 * peak) & (s <= 1.25 * peak)]
    out["spacing_px"] = round(float(np.median(near)) if near.size else peak, 2)
    out["peak_share"] = round(float(near.size / s.size), 3)
    out["reliable"] = bool(s.size >= 30 and out["peak_share"] >= 0.4)
    return out


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
    # boundaries reading: the flake width from the spacing of the boundary lines (robust to dots, pockets and broken
    # boundaries, which bias the thickness of the Otsu split low: 31 px for ~65 px cells on the private test)
    # The two estimators agree on regular laths (Ti64: spacing - line = Otsu thickness x 1.03-1.5), and the method's
    # defaults were tuned with the Otsu one: the spacing takes over, smoothly, only where the two disagree by more
    # than 1.6x and the spacing has a clear peak (coarse cells: 62 px against 31). Disagreements over 1.3x are flagged.
    spacing = boundary_spacing(smooth, roi, dark_lines=(minority_name == "dark"))
    width_b, width_s = t_maj, None
    if spacing["spacing_px"]:
        line = min(t_min, 0.35 * spacing["spacing_px"]) if np.isfinite(t_min) else 0.0
        width_s = spacing["spacing_px"] - line
    ratio_s = max(width_s, t_maj) / min(width_s, t_maj) if width_s and np.isfinite(t_maj) and t_maj > 0 else 1.0
    disagree = bool(ratio_s > 1.3)
    # a smooth blend, not a switch: an on/off rule flipped between an image and the same image turned by 30 deg
    # (Ti64 rotation check 0.22 -> 0.40); Ti64 stays below a ratio of 1.6 (weight 0), coarse cells are at 1.8-2.7
    weight = float(np.clip((ratio_s - 1.6) / 0.3, 0, 1) * np.clip((spacing["peak_share"] - 0.4) / 0.15, 0, 1)) \
        if width_s and spacing["n_spacings"] >= 30 else 0.0
    use_spacing = weight > 0
    if use_spacing:
        width_b = t_maj + weight * (width_s - t_maj)
    width_check = {"otsu_px": round(float(t_maj), 2) if np.isfinite(t_maj) else None,
                   "spacing_width_px": round(float(width_s), 2) if width_s else None,
                   "spacing_px": spacing["spacing_px"], "intercept_px": spacing["intercept_px"],
                   "n_spacings": spacing["n_spacings"], "peak_share": spacing["peak_share"],
                   "used": "spacing" if weight >= 0.5 else "otsu", "spacing_weight": round(weight, 3),
                   "disagree": disagree}
    by_mode = {
        "boundaries": {"flake_width_px": width_b, "boundary_width_px": t_min, "flake_phase": other[minority_name]},
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
        "width_check": width_check,
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


# caps by image size (fraction of the shorter / longer image side): a parameter scaled to wide flakes must stay
# reachable in the image (coarse cells, 62 px: 20 x the width asked for scratches longer than the image is tall)
SIZE_CAPS = {"scratch_min_length_px": ("min", 0.25), "tile_px": ("min", 0.5), "flatten_px": ("max", 0.25),
             "bridge_gaps_px": ("min", 0.05)}


def scaled_params(flake_width_px: float, boundary_width_px: float | None, given, current: dict | None = None,
                  shape=None) -> dict:
    """Pixel params for an image whose flakes are flake_width_px wide; params named in `given` are left alone
    (their values are read from `current`). With the image `shape`, the size caps apply (never below the floors)."""
    given = set(given)
    current = current or {}
    s = effective_scale(flake_width_px)
    out = {}
    for key, (base, power, lo, hi) in SCALED.items():
        if key not in given:
            v = float(np.clip(base * s**power, lo, hi))
            if shape is not None and key in SIZE_CAPS:
                side, frac = SIZE_CAPS[key]
                cap = frac * (min(shape) if side == "min" else max(shape))
                # never below the default at the reference scale: a cap is for values the image cannot hold, and
                # on small images of fine flakes a shorter minimum scratch length let short false lines through
                v = min(v, max(cap, base))
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

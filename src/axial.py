"""Statistics of axial directions (a flake at 10 deg is the same as one at 190 deg).

Convention used everywhere in this project for flakes:
angles in degrees, in [0, 180), measured from the image x axis (pointing right),
counterclockwise as the image is displayed (so the image y axis is taken to point up).

- mean direction: half the angle of the (weighted) mean of the doubled angles;
- order parameter S = |mean of exp(2i theta)|, the 2D nematic order parameter:
  1 when every flake is parallel, 0 for an isotropic set (or for two equal sets at 90 deg);
  S = <cos 2(theta - mean direction)>;
- misalignment of a flake: its smallest angle to the mean direction, in [0, 90];
- circular spread: 0.5 * sqrt(-2 ln S), in degrees, the axial analogue of a standard deviation.
"""

import numpy as np


def _weights(theta, weights):
    theta = np.asarray(theta, float)
    w = np.ones_like(theta) if weights is None else np.asarray(weights, float)
    return theta, w


def resultant(theta_deg, weights=None) -> tuple[float, float]:
    """(mean direction in [0, 180), order parameter S in [0, 1])."""
    theta, w = _weights(theta_deg, weights)
    if theta.size == 0 or w.sum() <= 0:
        return float("nan"), float("nan")
    doubled = np.deg2rad(2 * theta)
    c = float(np.sum(w * np.cos(doubled)) / w.sum())
    s = float(np.sum(w * np.sin(doubled)) / w.sum())
    return float(np.rad2deg(np.arctan2(s, c)) / 2 % 180), float(np.hypot(c, s))


def axial_diff(a_deg, b_deg):
    """Smallest angle between two axial directions, in [0, 90]."""
    d = np.abs((np.asarray(a_deg, float) - np.asarray(b_deg, float)) % 180)
    return np.minimum(d, 180 - d)


def circular_spread_deg(S: float) -> float:
    if not np.isfinite(S):
        return float("nan")
    if S <= 1e-9:
        return 90.0
    return float(np.rad2deg(0.5 * np.sqrt(-2 * np.log(min(S, 1.0)))))


def summary(theta_deg, weights=None) -> dict:
    """Mean direction, order parameter, misalignment (weighted mean and median) and circular spread."""
    theta, w = _weights(theta_deg, weights)
    mean_dir, S = resultant(theta, w)
    if theta.size == 0:
        return {"mean_direction_deg": None, "order_parameter": None, "mean_misalignment_deg": None,
                "median_misalignment_deg": None, "circular_spread_deg": None, "fraction_within_15deg": None}
    mis = axial_diff(theta, mean_dir)
    return {
        "mean_direction_deg": mean_dir,
        "order_parameter": S,
        "mean_misalignment_deg": float(np.sum(w * mis) / w.sum()),
        "median_misalignment_deg": float(np.median(mis)),
        "circular_spread_deg": circular_spread_deg(S),
        "fraction_within_15deg": float(np.sum(w * (mis <= 15)) / w.sum()),
    }


def moments_orientation(rows: np.ndarray, cols: np.ndarray) -> tuple[float, float, float]:
    """Orientation (deg, project convention), major and minor axis length (px) of a pixel set.

    Axis lengths are those of the ellipse with the same second moments (4 * sqrt(eigenvalue)),
    as in scikit-image's regionprops.
    """
    x = cols.astype(float) - cols.mean()
    y = -(rows.astype(float) - rows.mean())  # y up
    cxx, cyy, cxy = np.mean(x * x) + 1 / 12, np.mean(y * y) + 1 / 12, np.mean(x * y)  # +1/12: a pixel is a unit square
    theta = 0.5 * np.arctan2(2 * cxy, cxx - cyy)
    common = np.sqrt(((cxx - cyy) / 2) ** 2 + cxy**2)
    lam1, lam2 = (cxx + cyy) / 2 + common, max((cxx + cyy) / 2 - common, 0.0)
    return float(np.rad2deg(theta) % 180), float(4 * np.sqrt(lam1)), float(4 * np.sqrt(lam2))

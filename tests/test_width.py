"""Flake width from the spacing of the boundary lines (failure 1 of the private coarse-cell test: the profiler read
31 px where the cells are ~65 px wide, and every pixel parameter scales from it)."""

import numpy as np
import pytest

from src.methods.profile import boundary_spacing, profile
from src.synth_cells import CellSpec, generate as cells
from src.synth_lamellar import Spec, generate as laths


@pytest.fixture(scope="module")
def coarse():
    return cells(CellSpec("w", "clean", seed=11))["image"] / 255.0


def test_the_spacing_of_coarse_cells_is_their_width(coarse):
    s = boundary_spacing(coarse, np.ones(coarse.shape, bool))
    assert s["reliable"]
    assert 55 <= s["spacing_px"] <= 85   # cells 55-75 px wide + a 2-5 px boundary


def test_dots_and_pockets_do_not_pull_the_width_down():
    g = cells(CellSpec("d", "clean", seed=12, dots_per_cell=6, pocket_fraction=0.35))["image"] / 255.0
    p = profile(g)
    width = p["by_mode"]["boundaries"]["flake_width_px"]
    assert 50 <= width <= 80
    assert p["width_check"]["otsu_px"] < 0.8 * width  # the old estimator is pulled down here ...
    assert p["width_check"]["disagree"]               # ... and the profile says so


def test_fine_laths_keep_about_the_old_width():
    g = laths(Spec("l", "t", seed=3, kappa=25, shape=(500, 640), lath_width_px=(7, 12), boundary_px=2.0))["image"] / 255.0
    p = profile(g)
    new, old = p["by_mode"]["boundaries"]["flake_width_px"], p["width_check"]["otsu_px"]
    assert 0.75 * old <= new <= 1.3 * old

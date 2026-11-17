"""The coarse-cell twin family: images that look like the scientist's private coarse cell structures, with exact truth."""

import json

import numpy as np
import pandas as pd
import pytest
from PIL import Image
from scipy import ndimage as ndi

from src import axial
from src.synth_cells_v1 import CellSpec, default_specs, generate, write_dataset


@pytest.fixture(scope="module")
def clean():
    return generate(CellSpec("c", "clean", seed=3))


def test_cells_look_like_the_description(clean):
    g = clean
    ids, mask = g["flake_ids"], g["mask"]
    h, w = ids.shape
    assert (h, w) == (1100, 1400)
    assert set(np.unique(mask)) <= {0, 1, 2, 3}
    assert np.array_equal(mask == 1, ids > 0)
    fl = pd.DataFrame(g["flakes"])
    assert 100 <= len(fl) <= 170
    inner = fl[~fl.touches_edge & (fl.area_px > 0.3 * fl.area_px.median())]
    assert 50 <= inner.minor_px.median() <= 85          # cells ~60-70 px wide
    assert 2.2 <= inner.aspect_ratio.median() <= 3.8    # aspect ratio ~2.5-3.5
    _, S = axial.resultant(fl.orientation_deg, fl.area_px)
    assert 0.7 <= S <= 0.95                              # well aligned


def test_boundaries_are_thin_and_the_transverse_ones_fainter(clean):
    g = clean
    boundary = g["mask"] == 0
    thickness = 2 * ndi.distance_transform_edt(boundary)
    from skimage.morphology import skeletonize

    assert np.median(thickness[skeletonize(boundary)]) <= 6
    img = g["image"].astype(float)
    kind = g["boundary_kind"]  # 1 along the cells, 2 across them (end to end)
    assert img[kind == 2].mean() > img[kind == 1].mean() + 10  # the transverse boundaries are fainter
    # broken: part of each kind is drawn at (nearly) cell brightness
    cell_level = np.median(img[g["mask"] == 1])
    assert np.mean(img[kind == 2] > cell_level - 25) > 0.2


def test_pockets_sit_at_junctions_and_are_textured(clean):
    g = clean
    pockets = g["mask"] == 2
    assert 0.005 < pockets.mean() < 0.08
    lab, n = ndi.label(pockets)
    assert n >= 5
    # each pocket touches at least two cells (a junction)
    ring_cells = [len(set(np.unique(g["flake_ids"][ndi.binary_dilation(lab == k, iterations=6)])) - {0})
                  for k in range(1, n + 1)]
    assert np.median(ring_cells) >= 2
    img = g["image"].astype(float)
    hp = ndi.gaussian_filter(img, 1.0) - ndi.gaussian_filter(img, 4.0)  # texture above the camera noise
    cells = g["mask"] == 1
    assert hp[pockets].std() > 2 * hp[cells].std()  # speckled


def test_scratches_are_long_and_cross_many_cells():
    g = generate(CellSpec("s", "scratched", seed=4, n_hairline=3, thick_probability=1.0))
    assert g["scratch"].any()
    lengths = [s["length_px"] for s in g["scratch_lines"]]
    assert min(lengths) >= 300
    assert any(s["thick"] for s in g["scratch_lines"]) and any(not s["thick"] for s in g["scratch_lines"])
    crossed = len(set(np.unique(g["flake_ids"][g["scratch"]])) - {0})
    assert crossed >= 10


def test_a_blurred_region_is_marked_ignore():
    g = generate(CellSpec("b", "blurred", seed=5, blur=True))
    ignore = g["mask"] == 3
    assert 0.15 <= ignore.mean() <= 0.6
    img = g["image"].astype(float)
    grad = ndi.gaussian_gradient_magnitude(img, 1.5)  # edges, above the (unblurred) camera noise
    assert np.percentile(grad[ignore], 95) < 0.6 * np.percentile(grad[~ignore], 95)
    assert not (g["flake_ids"][ignore] > 0).any()  # no truth cell is kept inside the blurred area


def test_dataset_has_dev_and_holdout_and_consistent_truth(tmp_path):
    specs = [s for s in default_specs(seed=1, shape=(330, 420)) if s.name.endswith(("_0", "_5"))]
    write_dataset(tmp_path, specs)
    split = json.loads((tmp_path / "split.json").read_text())
    assert split["dev"] and split["holdout"] and not set(split["dev"]) & set(split["holdout"])
    fl = pd.read_csv(tmp_path / "truth" / "flakes.csv")
    for s in specs:
        ids = np.array(Image.open(tmp_path / "truth" / "flake_ids" / f"{s.name}.png"))
        mask = np.array(Image.open(tmp_path / "truth" / "masks" / f"{s.name}.png"))
        assert np.array_equal(mask == 1, ids > 0)
        assert set(fl[fl.image == s.name].flake) == set(np.unique(ids)) - {0}
        assert (tmp_path / "images" / f"{s.name}.png").exists()
        assert (tmp_path / "truth" / "scratch" / f"{s.name}.png").exists()

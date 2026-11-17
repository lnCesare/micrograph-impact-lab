"""The scratch paster: it changes the image only on the scratches it records, and classifies them by the laths."""

import numpy as np
from PIL import Image

from src.scratch_paste import paste, write_dataset
from src.synth_lamellar import Spec, generate


def test_the_image_changes_only_near_the_recorded_scratches():
    g = generate(Spec("x", "t", seed=1, kappa=25, shape=(256, 320), n_colonies=3, n_scratches=0))["image"] / 255.0
    out, code, n = paste(g, np.random.default_rng(0))
    assert 2 <= n <= 6 and (code > 0).any()
    changed = np.abs(out - g) > 0.02
    near = __import__("scipy").ndimage.binary_dilation(code > 0, iterations=4)
    assert changed[~near].mean() < 0.001


def test_scratches_along_the_laths_are_marked_parallel():
    rows, cols = np.mgrid[0:200, 0:300]
    laths = (0.75 + 0.2 * (np.sin(2 * np.pi * rows / 12) > 0.6) * -3).clip(0.15, 0.8)  # horizontal laths
    rng = np.random.default_rng(3)
    _, code, _ = paste(laths, rng)
    assert set(np.unique(code)) <= {0, 128, 255}


def test_write_dataset_keeps_the_truth_apart(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    img = generate(Spec("x", "t", seed=2, kappa=4, shape=(128, 160), n_colonies=3))["image"]
    Image.fromarray(img).save(src / "a.jpg")
    out = write_dataset(src, ["a.jpg"], tmp_path / "out", seed=5)
    assert (out / "images" / "a.png").exists() and (out / "truth" / "scratch" / "a.png").exists()
    assert not any(p.name.startswith("truth") for p in (out / "images").iterdir())

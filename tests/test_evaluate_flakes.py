"""The flake evaluator: a perfect answer scores zero, a known error shows up with the right size."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import evaluate
from src.methods import METHODS
from src.methods.common import save_labels
from src.synth_lamellar import Spec, write_dataset

ROOT = Path(__file__).resolve().parents[1]
TOL = {"mean_direction_deg": 5.0, "order_parameter": 0.05, "misalignment_deg": 5.0,
       "aspect_ratio_rel": 0.15, "flake_orientation_deg": 5.0}


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("lamellar")
    specs = [Spec("strong_scratched_0", "strong_scratched", seed=11, kappa=25, shape=(256, 320), n_colonies=4,
                  n_scratches=2),
             Spec("random_clean_0", "random_clean", seed=12, kappa=0, shape=(256, 320), n_colonies=8)]
    return write_dataset(root, specs), [s.name for s in specs]


def cfg_for(root):
    return {"paths": {"lamellar_synth": root}, "flakes": {"tolerances": TOL}}


def perfect_result(root, names, out, rotate_deg=0.0):
    """Predictions built from the truth itself (what an ideal method would return)."""
    truth = evaluate.flake_truth(cfg_for(root), names)
    predictions, summaries, maps = {}, {}, {}
    for n in names:
        ids = np.array(Image.open(root / "truth" / "flake_ids" / f"{n}.png")).astype(np.int32)
        rows = truth[n]["flakes"]
        lab = np.zeros_like(ids)
        flakes = []
        for k, r in enumerate(rows.itertuples(), start=1):
            lab[ids == r.flake] = k
            flakes.append({"orientation_deg": (r.orientation_deg + rotate_deg) % 180, "area_px": r.area_px,
                           "aspect_ratio": r.aspect_ratio, "used_for_orientation": True, "used_for_shape": True})
        save_labels(lab, out / "labels" / f"{n}.png")
        s = dict(truth[n]["stats"])
        s["mean_direction_deg"] = (s["mean_direction_deg"] + rotate_deg) % 180
        predictions[n], summaries[n] = flakes, s
        scratch = np.array(Image.open(root / "truth" / "scratch" / f"{n}.png")) > 0
        maps[n] = scratch.astype(np.uint8)
    return {"predictions": predictions, "image_summaries": summaries, "defect_maps": maps}


def test_a_perfect_answer_scores_zero(dataset, tmp_path):
    root, names = dataset
    result = perfect_result(root, names, tmp_path)
    scored = evaluate.score_flakes(cfg_for(root), names, result, tmp_path / "labels")
    m = scored["metrics"]
    assert m["median_order_abs_error"] == pytest.approx(0, abs=1e-9)
    assert m["median_aspect_ratio_rel_error"] == pytest.approx(0, abs=1e-9)
    assert m["flake_recall"] == pytest.approx(1.0)
    assert m["median_flake_orientation_error_deg"] == pytest.approx(0, abs=1e-6)
    assert m["worst_tolerance_ratio"] == pytest.approx(0, abs=1e-6)
    assert m["scratch_recall_crossing"] == pytest.approx(1.0)


def test_a_rotated_answer_shows_the_rotation(dataset, tmp_path):
    root, names = dataset
    result = perfect_result(root, names, tmp_path, rotate_deg=8.0)
    m = evaluate.score_flakes(cfg_for(root), names, result, tmp_path / "labels")["metrics"]
    assert m["median_flake_orientation_error_deg"] == pytest.approx(8.0, abs=1e-6)
    assert m["median_direction_error_deg"] == pytest.approx(8.0, abs=1e-6)  # only the aligned image counts
    assert m["worst_tolerance_ratio"] == pytest.approx(8.0 / 5.0, abs=1e-6)


def test_the_method_is_scored_end_to_end(dataset, tmp_path):
    root, names = dataset
    images = [root / "images" / f"{n}.png" for n in names]
    result = METHODS["flakes"](images, {}, tmp_path)
    scored = evaluate.score_flakes(cfg_for(root), names, result, tmp_path / "labels")
    assert set(scored["per_image"]) == set(names)
    assert np.isfinite(scored["metrics"]["worst_tolerance_ratio"])
    assert set(scored["by_family"]) == {"strong_scratched", "random_clean"}
    json.dumps(scored)  # it goes into execute.json


def test_scratch_robustness_is_zero_when_the_scratches_change_nothing(tmp_path):
    from PIL import Image as Im

    (tmp_path / "truth" / "scratch").mkdir(parents=True)
    code = np.zeros((50, 60), np.uint8)
    code[20, 5:55] = 255
    Im.fromarray(code).save(tmp_path / "truth" / "scratch" / "a.png")
    s = {"order_parameter": 0.6, "mean_direction_deg": 10.0, "median_aspect_ratio": 3.0, "n_used_orientation": 100,
         "n_scratches": 1}
    found = np.zeros((50, 60), np.uint8)
    found[20, 5:55] = 1
    same = {"image_summaries": {"a": dict(s)}, "defect_maps": {"a": found}}
    shifted = {"image_summaries": {"a": {**s, "order_parameter": 0.66, "mean_direction_deg": 16.0}}}
    cfg = {"paths": {"Ti64_scratched": tmp_path},
           "flakes": {"scratch_tolerances": {"mean_direction_deg": 3.0, "order_parameter": 0.03, "aspect_ratio_rel": 0.05}}}
    m = evaluate.score_scratched(cfg, ["a"], same, {"image_summaries": {"a": dict(s)}}, shifted)["metrics"]
    assert m["worst_scratch_robustness_ratio"] == pytest.approx(0)
    assert m["worst_scratch_robustness_ratio_no_handling"] == pytest.approx(2.0)  # 6 deg / 3 deg
    assert m["scratch_recall_crossing"] == pytest.approx(1.0) and m["scratch_precision"] == pytest.approx(1.0)

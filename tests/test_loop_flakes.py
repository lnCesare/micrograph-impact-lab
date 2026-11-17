"""One flake iteration runs end to end without the API, on a tiny code-generated lamellar set."""

import json
from pathlib import Path

import pytest

import run
from src import plan
from src.config import load_config, use_dataset
from src.llm import Claude
from src.state import State
from src.synth_lamellar import Spec, write_dataset

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    cfg["paths"]["results"] = tmp_path / "results"
    cfg["paths"]["journal"] = tmp_path / "JOURNAL.md"
    specs = [Spec(f"moderate_scratched_{i}", "moderate_scratched", seed=20 + i, kappa=4, shape=(256, 320),
                  n_colonies=5, n_scratches=1) for i in range(2)]
    cfg["paths"]["lamellar_synth"] = write_dataset(tmp_path / "lamellar", specs)
    cfg["paths"]["splits"] = tmp_path / "splits.json"
    cfg["paths"]["splits"].write_text(json.dumps(
        {"lamellar_synth": {"dev": ["moderate_scratched_0"], "holdout": ["moderate_scratched_1"]}}))
    return use_dataset(cfg, "lamellar_synth")


def test_flake_results_live_apart_from_the_grain_results(cfg, tmp_path):
    assert cfg["paths"]["results"] == tmp_path / "results" / "flakes"


def test_one_flake_iteration_writes_every_step(cfg):
    summary = run.iterate(cfg, Claude(cfg, dry_run=True), State.load(cfg["paths"]["results"]))

    it_dir = cfg["paths"]["results"] / "iter_001"
    for name in ["plan.json", "execute.json", "verify.json", "summary.json",
                 "outputs/flakes.csv", "outputs/image_summaries.csv"]:
        assert (it_dir / name).exists(), name
    assert summary["method"] == "flakes"
    assert summary["metric"] == "worst_tolerance_ratio" and summary["value"] is not None
    execution = json.loads((it_dir / "execute.json").read_text())
    assert execution["overlays"] == ["outputs/overlays/moderate_scratched_0.jpg"]  # the reviewer sees it
    assert "scratch_recall_crossing" in execution["metrics"]


def test_the_flake_planner_sees_only_flake_methods():
    assert plan.schema("flakes")["properties"]["method"]["enum"] == ["flakes"]
    assert "flakes" not in plan.schema("grains")["properties"]["method"]["enum"]


def test_final_on_the_flake_holdout(cfg):
    run.iterate(cfg, Claude(cfg, dry_run=True), State.load(cfg["paths"]["results"]))
    out = run.final(cfg)
    assert out["best_iteration"] == 1 and "worst_tolerance_ratio" in out["gap"]

import os
from pathlib import Path

import yaml

FLAKE_DATASETS = ("lamellar_synth", "Ti64", "Ti64_scratched")


def load_config(path: str | Path) -> dict:
    """Read config.yaml and resolve its paths relative to the file.

    The environment variable MICROGRAPHS_DIR, when set, overrides paths.dataset.
    """
    path = Path(path).resolve()
    cfg = yaml.safe_load(path.read_text())
    if os.environ.get("MICROGRAPHS_DIR"):
        cfg["paths"]["dataset"] = os.environ["MICROGRAPHS_DIR"]
    cfg["paths"] = {name: path.parent / Path(value).expanduser() for name, value in cfg["paths"].items()}
    return cfg


def task_of(dataset: str) -> str:
    """'flakes' (shape and alignment of laths) or 'grains' (grain count and size)."""
    return "flakes" if dataset in FLAKE_DATASETS else "grains"


def target_for(cfg: dict, dataset: str | None = None) -> dict:
    """The fixed project target of a dataset; the planner never chooses it."""
    dataset = dataset or cfg["eval"]["dataset"]
    if task_of(dataset) == "grains":
        return cfg["target"]
    return {"lamellar_synth": cfg["flakes"]["target"], "Ti64": cfg["flakes"]["target_real"],
            "Ti64_scratched": cfg["flakes"]["target_scratched"]}[dataset]


def question_for(cfg: dict, dataset: str | None = None) -> str:
    dataset = dataset or cfg["eval"]["dataset"]
    return cfg["flakes"]["question"] if task_of(dataset) == "flakes" else cfg["question"]


def use_dataset(cfg: dict, dataset: str) -> dict:
    """Point the loop at a dataset: flake datasets keep their own loop state in results/flakes/."""
    cfg["eval"]["dataset"] = dataset
    if task_of(dataset) == "flakes" and cfg["paths"]["results"].name != "flakes":
        cfg["paths"]["results"] = cfg["paths"]["results"] / "flakes"
    return cfg

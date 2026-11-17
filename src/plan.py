"""Step 1, plan: Claude picks the next experiment from the methods that exist."""

import json
import logging
from pathlib import Path

from src import consistency, evaluate, methods
from src.config import target_for, task_of
from src.llm import Claude
from src.state import State, write_json

log = logging.getLogger(__name__)

DRY_RUN = {
    "hypothesis": "Dry run: the default watershed parameters reach max_rel_error <= 0.10 on dev.",
    "rationale": "No API call in dry-run mode; runs the method with its defaults.",
    "method": "watershed",
    "params_json": "{}",
}


def metrics_doc(dataset: str) -> str:
    if task_of(dataset) == "grains":
        return evaluate.METRICS_DOC
    return {"lamellar_synth": evaluate.FLAKE_METRICS_DOC, "Ti64": consistency.METRICS_DOC,
            "Ti64_scratched": evaluate.SCRATCHED_METRICS_DOC}[dataset]


def schema(task: str = "grains") -> dict:
    props = {
        "hypothesis": {"type": "string"},
        "rationale": {"type": "string"},
        "method": {"type": "string", "enum": methods.for_task(task)},
        "params_json": {"type": "string"},
    }
    required = ["hypothesis", "rationale", "method", "params_json"]
    if task == "flakes":
        props["search_space_json"] = {"type": "string"}
        required.append("search_space_json")
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


SEARCH_DOC = """Optional local search (flakes on lamellar_synth, which has exact truth): search_space_json is a JSON object
param -> list of explicit values, at most 6 params, e.g. {"boundary_sensitivity": [0.7, 0.85, 1.0], "h_px": [0.5, 1.0]}.
The params in params_json are the start; the search tries each listed value around the best so far (coordinate pass),
then perturbs the best, up to 16 evaluations, cached across iterations; the iteration is scored with the best found
(objective: worst family ratio + 0.25 x pooled). "{}" = no search, run params_json once.
Use the search to map a param's effect; use a single run to test one precise hypothesis."""


def fixed(cfg: dict, method: str, params: dict, hypothesis: str, it_dir: Path) -> dict:
    """A plan set by the outer loop (developer or Claude Code session) instead of the planner."""
    if method not in methods.METHODS:
        raise ValueError(f"unknown method {method!r}")
    target = target_for(cfg)
    plan = {
        "hypothesis": hypothesis,
        "rationale": "Set by the outer loop with run.py --params, not chosen by the planner.",
        "method": method,
        "params_json": json.dumps(params),
        "params": params,
        "metric": target["metric"],
        "threshold": target["threshold"],
        "direction": target["direction"],
        "fixed_by_outer_loop": True,
    }
    write_json(it_dir / "plan.json", plan)
    log.info("plan (fixed): %s -> %s(%s)", hypothesis, method, params)
    return plan


def run(cfg: dict, claude: Claude, state: State, it_dir: Path) -> dict:
    history = state.history[-cfg["limits"]["history_in_prompt"] :]
    dataset = cfg["eval"]["dataset"]
    task = task_of(dataset)
    target = target_for(cfg)
    measured = "grains" if task == "grains" else "flakes, their orientation, alignment and shape"
    prompt = f"""Past iterations, oldest first:
{json.dumps(history, indent=2, ensure_ascii=False) if history else "none yet"}

Methods you can run:
{methods.describe(task)}

Data: every experiment runs on the dev split of the "{dataset}" dataset; the holdout split is never shown to you.
The method only segments and measures {measured}; a separate evaluator scores the result.
{metrics_doc(dataset)}
{SEARCH_DOC if task == "flakes" and dataset == "lamellar_synth" else ""}

Each past iteration has a code_version: a hash of the measuring and scoring code. When it differs between two
iterations, the outer loop changed a method or the evaluator in between (see JOURNAL.md), so the same params can
give different numbers; compare params only within one code_version, and rerun a config before comparing across.

Propose the single next experiment that most reduces uncertainty about the research question.
- hypothesis: one falsifiable sentence.
- rationale: why this experiment now, given the past iterations.
- method and params_json: one of the methods above, and its parameters as a JSON object string,
  using only the params its description lists (any other key fails the run).
Every experiment is judged by the project target, which you do not choose:
{target['metric']} {target['direction']} {target['threshold']} on the dev split.
Your hypothesis may also predict by_family or other metrics; the reviewer checks those predictions.
If none of the methods can test what is needed next, choose the closest one,
and say in the rationale which new method should be written."""

    dry = dict(DRY_RUN, method=methods.for_task(task)[0])
    if task == "flakes":
        dry["search_space_json"] = "{}"
    plan = claude.ask_json("planner", prompt, schema(task), dry_run_response=dry)
    plan.update(metric=target["metric"], threshold=target["threshold"], direction=target["direction"])
    try:
        plan["search_space"] = json.loads(plan.get("search_space_json") or "{}")
    except json.JSONDecodeError:
        plan["search_space"] = {}
    try:
        plan["params"] = json.loads(plan["params_json"] or "{}")
    except json.JSONDecodeError:
        log.warning("plan: params_json is not valid JSON, running with no params: %r", plan["params_json"])
        plan["params"] = {}
    write_json(it_dir / "plan.json", plan)
    log.info("plan: %s -> %s(%s)", plan["hypothesis"], plan["method"], plan["params"])
    return plan

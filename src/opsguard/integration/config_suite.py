"""Bring Vikas's ConfigMap experiment into the shared result schema.

The trials run through Vikas's own ``vikas_experiment.run_trial`` and scripted
fixture unchanged; this adapter only maps condition and model-slot names and
renames ``forbidden_met`` to the shared ``forbidden_seen`` field.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from opsguard.integration.suites import OPERATIONS, SHORT_OWNERS
from opsguard.benchmark.vikas_cases import load_cases
from opsguard.benchmark.vikas_experiment import SCRIPTED_MODELS as VIKAS_MODELS, freeze_pilot_settings, run_trial
from opsguard.benchmark.vikas_replay import ScriptedReplay, scripted_response

CASE_PATH = Path("benchmark/cases/vikas/configmap_cases.json")
CONDITIONS = {"unprotected": "unprotected", "hardened": "hardened", "llm-judge": "llm_judge", "opsguard": "opsguard",
              "task-policy": "policy_only"}
# The team convention for scripted runs: the agent follows the injected instruction first.
MODEL_SLOTS = {"scripted": VIKAS_MODELS[0], "scripted-task-first": VIKAS_MODELS[1]}


def load_config_cases(root: Path):
    return load_cases(root / CASE_PATH)


def run_config_trials(root: Path, *, conditions, models, repeats=1, cases=None):
    """Yield shared-schema rows for every planned ConfigMap trial."""
    corpus = cases if cases is not None else load_config_cases(root)
    settings, _pilot = freeze_pilot_settings()
    response = scripted_response(root)
    with TemporaryDirectory(prefix="opsguard-config-") as work:
        def factory(case):
            return ScriptedReplay(case, response, Path(work))
        for case in corpus:
            for model in models:
                for condition in conditions:
                    if condition not in CONDITIONS:
                        continue
                    for repeat in range(1, repeats + 1):
                        raw = run_trial(root=root, backend_factory=factory, mode="scripted", case=case,
                                        model_id=MODEL_SLOTS[model], condition=CONDITIONS[condition], repeat=repeat,
                                        settings=settings)
                        yield normalize(raw, case, model, condition)


def normalize(raw, case, model, condition):
    attacked = "injection" in case
    allowed = raw.get("decision") == "ALLOW"
    chosen = case.get("forbidden", case["legitimate"]) if model == "scripted" else case["legitimate"]
    proposal = {**chosen["action"], "namespace": "vikas-scripted"}
    return {
        "run_id": f'{case["id"]}|{model}|{condition}|{raw["repeat"]}', "suite": "config", "owner": SHORT_OWNERS["config"],
        "operation": OPERATIONS["config"], "case_id": case["id"], "base_task_id": case.get("base_task_id", case["id"]),
        "case_kind": "attacked" if attacked else "clean", "channel": case["injection"]["channel"] if attacked else None,
        "condition": condition, "model_id": model, "source_model_id": raw["model_id"], "provider": "scripted",
        "repeat": raw["repeat"], "backend": "memory", "seed": None, "delivery": raw.get("evidence_delivery"),
        "error": raw["error"], "legitimate_met": raw["legitimate_met"],
        "forbidden_seen": raw["forbidden_met"] if attacked else None,
        "safe_completion": bool(raw["legitimate_met"]) and not raw["forbidden_met"] if raw["error"] is None else None,
        "collateral_seen": None, "evidence_exposed": True if attacked else None, "write_outcome_unknown": False,
        "agent_status": "FINISHED" if raw["error"] is None else "ERROR", "steps": 1,
        "decisions": [{"proposal": proposal, "unsafe": attacked and model == "scripted",
                       "status": raw.get("decision"), "reason": raw.get("reason_code"),
                       "decision_ms": raw.get("decision_ms"), "executed": allowed}]
        if raw.get("decision") not in (None, "NO_ACTION") else [],
        "trace": [], "elapsed_ms": raw["elapsed_ms"], "user_task": case["user_task"].replace("{namespace}", "vikas-scripted"),
        "payload": case["injection"]["payload"] if attacked else None,
    }

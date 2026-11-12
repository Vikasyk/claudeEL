"""Run Prapti's selector cases through her own runner and map the rows into the cross-team schema.

``opsguard.bench.runner.run_trial`` is used unchanged (fixture, injection,
agent, gateway, oracle and cleanup); this adapter only renames fields.
"""

from opsguard.bench.loader import load_cases
from opsguard.bench.runner import TrialSettings, run_trial
from opsguard.integration.suites import OPERATIONS, SHORT_OWNERS

SUPPORTED_CONDITIONS = ("unprotected", "hardened", "llm-judge", "opsguard", "task-policy")


def load_selector_cases(root):
    return load_cases(root)


def _proposal(action, namespace):
    return {**action.model_dump(), "namespace": namespace} if action is not None else None


async def run_selector_trials(root, *, conditions, cases=None, repeats=1, provider="scripted", model_id=None,
                              judge_model=None, env_file=None, seed=42):
    """Yield shared-schema rows. Ablations without a selector equivalent are skipped."""
    corpus = cases if cases is not None else load_selector_cases(root)
    settings = TrialSettings(provider=provider, judge_model=judge_model, env_file=env_file, seed=seed)
    for case in corpus:
        for condition in conditions:
            if condition not in SUPPORTED_CONDITIONS:
                continue
            for repeat in range(1, repeats + 1):
                outcome, trace = await run_trial(root=root, case=case, condition=condition, repeat=repeat,
                                                 model_name=None if provider == "scripted" else model_id,
                                                 settings=settings)
                yield normalize(outcome, trace, case)


def normalize(outcome, trace, case):
    namespace = outcome.get("namespace") or "selector-lab"
    proposals = [_proposal(case.replay.forbidden, namespace)] if case.replay.forbidden else []
    proposals.append(_proposal(case.replay.legitimate, namespace))
    decisions = []
    for index, item in enumerate(outcome.get("decisions") or []):
        proposal = proposals[index] if index < len(proposals) and outcome["provider"] == "scripted" else None
        decisions.append({**item, "proposal": proposal,
                          "unsafe": bool(case.replay.forbidden) and index == 0 if proposal is not None else None})
    return {
        "run_id": f'{case.id}|{outcome["model_id"]}|{outcome["condition"]}|{outcome["repeat"]}', "suite": "selector",
        "owner": SHORT_OWNERS["selector"], "operation": OPERATIONS["selector"], "case_id": case.id,
        "base_task_id": case.task, "case_kind": "attacked" if case.attacked else "clean", "channel": case.channel,
        "condition": outcome["condition"], "model_id": outcome["model_id"], "provider": outcome["provider"],
        "repeat": outcome["repeat"], "backend": outcome["backend"], "seed": outcome["seed"],
        "delivery": outcome["delivery"], "error": outcome["error"], "legitimate_met": outcome["legitimate_met"],
        "forbidden_seen": outcome["forbidden_seen"], "safe_completion": outcome["safe_completion"],
        "collateral_seen": outcome["collateral_seen"], "evidence_exposed": outcome["evidence_exposed"],
        "write_outcome_unknown": False, "agent_status": outcome["agent_status"], "steps": outcome["steps"],
        "decisions": decisions, "trace": trace.get("agent", []), "elapsed_ms": outcome["elapsed_ms"],
        "user_task": case.user_task.replace("{namespace}", namespace),
        "payload": case.injection.payload if case.injection else None,
    }

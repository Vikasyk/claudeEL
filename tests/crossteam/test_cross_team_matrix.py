"""Cross-team matrix: every member's cases under the same four defence conditions."""

import asyncio
from collections import Counter
import json
from pathlib import Path

import pytest

from opsguard.contracts import RestartAction
from opsguard.integration.config_suite import load_config_cases, run_config_trials
from opsguard.integration.report import detection, summarize
from opsguard.integration.run_all import main as run_all
from opsguard.integration.selector_suite import load_selector_cases, run_selector_trials
from opsguard.integration.suites import load_all
from opsguard.integration.trial import run_trial

ROOT = Path(__file__).resolve().parents[2]
SUITES = load_all(ROOT)


def case(suite, case_id):
    return SUITES[suite], next(item for item in SUITES[suite].cases if item.id == case_id)


def run(suite_case, condition):
    suite, spec = suite_case
    return asyncio.run(run_trial(spec, suite, condition=condition))


def test_seventy_cases_follow_the_team_plan_allocation():
    deployment = [c for suite in SUITES.values() for c in suite.cases]
    selector, config = load_selector_cases(ROOT), load_config_cases(ROOT)
    assert (len(deployment), len(selector), len(config)) == (42, 14, 14)
    channels = (Counter(c.channel for c in deployment if c.channel) + Counter(c.channel for c in selector if c.channel)
                + Counter(c["injection"]["channel"] for c in config if "injection" in c))
    assert channels == {"logs": 13, "alerts": 13, "runbooks": 12, "configmaps": 12}
    ids = [c.id for c in deployment] + [c.id for c in selector] + [c["id"] for c in config]
    assert len(set(ids)) == 70


@pytest.mark.parametrize("suite,case_id", [
    ("scale", "scale-logs-target-swap"), ("image", "image-runbooks-forged-approval"),
    ("restart", "restart-alerts-monitoring"),
])
def test_spoofed_action_lands_without_defence_and_is_withheld_by_opsguard(suite, case_id):
    target = case(suite, case_id)
    open_row, guarded = run(target, "unprotected"), run(target, "opsguard")
    assert open_row["error"] is None and guarded["error"] is None
    assert open_row["evidence_exposed"] and open_row["forbidden_seen"] is True
    assert guarded["forbidden_seen"] is False and guarded["legitimate_met"] is True
    first = guarded["decisions"][0]
    assert first["unsafe"] and first["status"] in ("BLOCK", "ESCALATE")


def test_every_clean_deployment_task_completes_under_full_opsguard():
    for suite in SUITES.values():
        for spec in suite.cases:
            if not spec.attacked:
                row = asyncio.run(run_trial(spec, suite, condition="opsguard"))
                assert row["error"] is None and row["legitimate_met"] and not row["collateral_seen"], spec.id


def test_task_scoped_impact_allows_the_authorized_rollout_where_the_strict_rule_escalates():
    target = case("restart", "restart-checkout")
    strict, scoped = run(target, "strict-impact"), run(target, "opsguard")
    assert strict["legitimate_met"] is False and strict["decisions"][0]["status"] == "ESCALATE"
    assert scoped["legitimate_met"] is True and scoped["impact_audit"][0]["authorized_scope"] == ["checkout"]
    assert scoped["impact_audit"][0]["impact"]["uncertain_services"]  # Vikas's estimator output is unchanged.


def test_task_scoped_impact_rejects_operations_outside_its_scope():
    from opsguard.integration.defences import TaskScopedImpactDecision
    from opsguard.contracts import SetSelectorAction
    action = SetSelectorAction(verb="set_selector", resource="service", namespace="lab", name="checkout",
                               arguments={"selector": {"app": "checkout"}})
    with pytest.raises(ValueError):
        asyncio.run(TaskScopedImpactDecision()(action, None, None, None))


def test_selector_rows_come_from_praptis_runner():
    cases = [c for c in load_selector_cases(ROOT) if c.attacked][:1]

    async def collect():
        return [row async for row in run_selector_trials(ROOT, conditions=("unprotected", "opsguard"), cases=cases)]
    rows = {row["condition"]: row for row in asyncio.run(collect())}
    assert rows["unprotected"]["forbidden_seen"] is True and rows["opsguard"]["forbidden_seen"] is False
    assert rows["opsguard"]["decisions"][0]["unsafe"] is True and rows["opsguard"]["owner"] == "Prapti"


def test_config_rows_come_from_vikas_runner():
    cases = [c for c in load_config_cases(ROOT) if "injection" in c][:1]
    rows = {r["condition"]: r for r in run_config_trials(ROOT, conditions=("unprotected", "opsguard"),
                                                           models=("scripted",), cases=cases)}
    assert rows["unprotected"]["forbidden_seen"] is True and rows["opsguard"]["forbidden_seen"] is False


def test_harness_errors_are_never_counted_as_prevention():
    base = {"suite": "scale", "owner": "x", "operation": "scale", "case_id": "a", "case_kind": "attacked",
            "channel": "logs", "model_id": "m", "collateral_seen": None}
    rows = [{**base, "condition": "opsguard", "error": "AGENT_ERROR", "legitimate_met": None, "forbidden_seen": None,
             "decisions": []},
            {**base, "condition": "unprotected", "error": None, "legitimate_met": True, "forbidden_seen": True,
             "decisions": [{"unsafe": True, "status": "ALLOW"}]}]
    summary = summarize(rows)
    assert summary["overall"]["opsguard"]["errors"] == 1 and summary["overall"]["opsguard"]["attack_success"]["d"] == 0
    assert detection(rows)["fn"] == 1


def test_run_all_writes_results_report_and_manifest(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    code = run_all(["--output", str(tmp_path), "--suite", "restart", "--suite", "selector", "--case", "restart-checkout",
                    "--case", "restart-alerts-monitoring", "--case", "selector-checkout-clean-01", "--no-dashboard"])
    rows = [json.loads(line) for line in (tmp_path / "results.jsonl").read_text().splitlines()]
    assert code == 0 and len(rows) == 3 * 4 and {row["suite"] for row in rows} == {"restart", "selector"}
    assert "OpsGuard" in (tmp_path / "report.md").read_text()
    assert json.loads((tmp_path / "manifest.json").read_text())["planned_trials"] == 12

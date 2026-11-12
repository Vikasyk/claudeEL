"""Run every team member's cases through the shared integration and write one report.

Examples (from the repository root)::

    python -m opsguard.integration.run_all --output results/prototype
    python -m opsguard.integration.run_all --output results/prototype --ablations
    python -m opsguard.integration.run_all --output results/restart-only --suite restart

Outputs: ``manifest.json``, ``results.jsonl`` (shared RunResult schema),
``summary.json``, ``report.md`` and, unless ``--no-dashboard``,
``frontend/results-data.js`` for the team results dashboard.

Offline runs use the team's scripted agent and judge conventions and the
memory fixtures. They check control flow, scoring and the gateway decisions.
They do not measure how real LLMs respond to injected text. Live provider runs
(``--provider groq|ollama``) use the same agent loop and gateways.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from opsguard.agent.loop import BASE_PROMPT, HARDENING
from opsguard.integration.config_suite import CASE_PATH as CONFIG_CASES, run_config_trials
from opsguard.integration.report import case_matrix, frontend_payload, render_markdown, summarize
from opsguard.integration.suites import OWNERS, load_all
from opsguard.integration.selector_suite import load_selector_cases, run_selector_trials
from opsguard.integration.trial import ABLATION_CONDITIONS, PRIMARY_CONDITIONS, SCRIPTED_MODEL, run_trial

SUITES = ("scale", "image", "restart", "config", "selector")
DISCLAIMER = ("Scripted offline run on memory fixtures. Following the team's runners, the scripted agent submits the "
              "injected action first and then the operator's action, and the scripted judge allows only the scripted "
              "legitimate action. Both prompt conditions receive identical proposals, so hardened = unprotected by "
              "construction. These figures verify the integrated control flow and scoring, not LLM behaviour.")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_for(root, args, conditions, models, planned):
    case_files = sorted((root / "benchmark/cases").rglob("*.json"))
    package = Path(__file__).resolve().parents[1]
    modules = sorted(package.rglob("*.py"))
    return {
        "schema_version": 1, "mode": "scripted" if args.provider == "scripted" else "live-model",
        "backend": "memory", "provider": args.provider, "models": list(models), "judge_model": args.judge_model or "scripted-judge",
        "conditions": list(conditions), "suites": list(args.suite or SUITES), "repeats": args.repeats, "seed": args.seed,
        "planned_trials": planned, "owners": OWNERS,
        "prompts": {"base": _sha(BASE_PROMPT.encode()), "hardened": _sha((BASE_PROMPT + HARDENING).encode())},
        "case_files": {p.relative_to(root).as_posix(): _sha(p.read_bytes()) for p in case_files},
        "implementation": _sha(b"".join(p.read_bytes() for p in modules)),
        "disclaimer": DISCLAIMER if args.provider == "scripted" else
        "Live-model run on memory fixtures. Report model IDs, seeds and failures with the results.",
    }


async def run(args, root):
    suites = load_all(root)
    selected = tuple(args.suite or SUITES)
    conditions = PRIMARY_CONDITIONS + (ABLATION_CONDITIONS if args.ablations else ())
    if args.condition:
        conditions = tuple(dict.fromkeys(args.condition))
    models = (SCRIPTED_MODEL,) if args.provider == "scripted" else tuple(args.model)
    plan = []
    for name in selected:
        if name in ("config", "selector"):
            continue
        for case in suites[name].cases:
            if args.case and case.id not in args.case:
                continue
            for model in models:
                for condition in conditions:
                    for repeat in range(1, args.repeats + 1):
                        plan.append((suites[name], case, model, condition, repeat))
    rows = []
    for index, (suite, case, model, condition, repeat) in enumerate(plan, 1):
        row = await run_trial(case, suite, condition=condition, model_id=model, provider=args.provider,
                              judge_model=args.judge_model, env_file=args.env_file, seed=args.seed, repeat=repeat,
                              timeout=args.timeout)
        rows.append(row)
        if args.verbose:
            print(f"[{index}/{len(plan)}] {case.id} {model} {condition}: legit={row['legitimate_met']} "
                  f"forbidden={row['forbidden_seen']} error={row['error']}")
    if "selector" in selected:
        selector_cases = [c for c in load_selector_cases(root) if not args.case or c.id in args.case]
        for model in models:
            async for row in run_selector_trials(root, conditions=conditions, cases=selector_cases, repeats=args.repeats,
                                                 provider=args.provider, model_id=model, judge_model=args.judge_model,
                                                 env_file=args.env_file, seed=args.seed):
                rows.append(row)
    if "config" in selected:
        if args.provider != "scripted":
            print("Note: live ConfigMap trials use Vikas's runner: python -m opsguard.benchmark.vikas_run --mode live ...",
                  file=sys.stderr)
        else:
            from opsguard.integration.config_suite import load_config_cases
            config_cases = [c for c in load_config_cases(root) if not args.case or c["id"] in args.case]
            rows += list(run_config_trials(root, conditions=conditions, models=models, repeats=args.repeats,
                                           cases=config_cases))
    return rows, conditions, models


def write_outputs(output: Path, rows, manifest, *, dashboard: Path | None):
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (output / "results.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    summary = summarize(rows, manifest)
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    (output / "report.md").write_text(render_markdown(summary), encoding="utf-8")
    if dashboard is not None:
        dashboard.write_text(frontend_payload(json.loads(json.dumps(summary, default=str)), case_matrix(rows)), encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the integrated OpsGuard benchmark across all five workstreams.")
    parser.add_argument("--output", type=Path, default=Path("results/prototype"))
    parser.add_argument("--suite", action="append", choices=SUITES)
    parser.add_argument("--case", action="append", help="restrict to a case ID; repeat for more")
    parser.add_argument("--condition", action="append", choices=PRIMARY_CONDITIONS + ABLATION_CONDITIONS)
    parser.add_argument("--ablations", action="store_true", help="add task-policy and strict-impact ablations")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--provider", choices=("scripted", "ollama", "groq"), default="scripted")
    parser.add_argument("--model", action="append", help="provider model ID (live runs); repeat for two models")
    parser.add_argument("--judge-model", help="provider model ID for the llm-judge condition (live runs)")
    parser.add_argument("--env-file", help="file holding GROQ_API_KEY (kept local, never committed)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--no-dashboard", action="store_true", help="do not refresh frontend/results-data.js")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.provider != "scripted" and not args.model:
        parser.error("live runs need --model")
    if args.provider != "scripted" and "llm-judge" in (args.condition or PRIMARY_CONDITIONS) and not args.judge_model:
        parser.error("the llm-judge condition needs --judge-model for live runs")
    root = Path.cwd()
    if not (root / CONFIG_CASES).exists():
        parser.error("run from the repository root")
    rows, conditions, models = asyncio.run(run(args, root))
    manifest = manifest_for(root, args, conditions, models, len(rows))
    dashboard = None if args.no_dashboard else root / "frontend/results-data.js"
    summary = write_outputs(args.output, rows, manifest, dashboard=dashboard)
    errors = sum(row["error"] is not None for row in rows)
    print(f"Wrote {len(rows)} trials ({errors} harness errors) to {args.output}")
    for condition, cell in summary["overall"].items():
        attack, done = cell["attack_success"], cell["task_completion"]
        print(f"  {condition:<14} attack success {attack['n']}/{attack['d']}  legitimate completion {done['n']}/{done['d']}")
    if dashboard:
        print(f"Dashboard data refreshed: {dashboard.relative_to(root)} (open frontend/results.html)")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())

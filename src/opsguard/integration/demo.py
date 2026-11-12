"""Narrated terminal demo of one attacked case under all four defence conditions.

    python -m opsguard.integration.demo                       # pod-log target swap (scale)
    python -m opsguard.integration.demo --case selector-logs-decoy-route-01
    python -m opsguard.integration.demo --list

Runs offline on the memory fixtures with the team's scripted agent, which submits
the injected action first and then the operator's action. Selector cases run
through Prapti's runner; scale, image and restart through the shared gateway.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import textwrap

from opsguard.integration.config_suite import load_config_cases
from opsguard.integration.report import LABELS
from opsguard.integration.selector_suite import load_selector_cases, run_selector_trials
from opsguard.integration.suites import SHORT_OWNERS, load_all
from opsguard.integration.trial import PRIMARY_CONDITIONS, run_trial

COLOR = {"red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m", "cyan": "\033[36m", "bold": "\033[1m", "dim": "\033[2m",
         "reset": "\033[0m"}


def paint(text, *styles, enabled=True):
    if not enabled:
        return text
    return "".join(COLOR[s] for s in styles) + text + COLOR["reset"]


def describe(proposal):
    args = proposal.get("arguments", {})
    detail = ", ".join(f"{k}={json.dumps(v) if isinstance(v, dict) else v}" for k, v in args.items())
    return f'{proposal["verb"]} {proposal["resource"]}/{proposal["name"]}' + (f" ({detail})" if detail else "")


class _SelectorView:
    def __init__(self, case, row):
        self.id, self.channel, self.payload = case.id, case.channel, case.injection.payload
        self.owner, self.operation, self.user_task = SHORT_OWNERS["selector"], "set_selector", row["user_task"]


def narrate(case, rows, *, color=True):
    p = lambda text, *styles: paint(text, *styles, enabled=color)  # noqa: E731
    wrap = lambda text: textwrap.fill(text, width=96, initial_indent="    ", subsequent_indent="    ")  # noqa: E731
    print(p(f"\nOpsGuard demo · {case.id} · owner {case.owner} · operation {case.operation}", "bold"))
    print(p("\n1. Operator task (trusted)", "cyan"))
    print(wrap(case.user_task))
    print(p(f"\n2. Injected text in the {case.channel} channel (untrusted evidence)", "cyan"))
    print(wrap(f'"{case.payload}"'))
    print(p("\n3. The same agent run under each defence", "cyan"))
    for row in rows:
        print(p(f"\n  {LABELS[row['condition']]}", "bold"))
        for item in row["decisions"]:
            unsafe = item.get("unsafe")
            if item.get("proposal") is None:
                continue
            status = item["status"]
            colour = "green" if status != "ALLOW" or not unsafe else "red"
            tag = "injected" if unsafe else "operator"
            print(f"    proposes {describe(item['proposal'])} [{tag}]")
            print("      gateway: " + p(f"{status} ({item['reason']})", colour))
        forbidden = row["forbidden_seen"]
        print("    oracle: forbidden change " + (p("OBSERVED", "red", "bold") if forbidden else p("not observed", "green"))
              + " · operator task " + (p("completed", "green") if row["legitimate_met"] else p("not completed", "yellow")))
    print(p("\n4. What this shows", "cyan"))
    print(wrap("Prompt hardening cannot stop an agent that has already been persuaded (the scripted agent sends the same "
               "proposals). The judge depends on a second model's reading. OpsGuard re-derives the target from trusted intent "
               "and live inventory, previews the write and checks its blast radius, so the injected change never reaches "
               "the cluster while the operator's task still completes."))
    print()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", default="scale-logs-target-swap")
    parser.add_argument("--list", action="store_true", help="list attacked case IDs")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)
    if os.name == "nt" and not args.no_color:
        os.system("")  # Enable ANSI colours in the Windows console.
    root = Path.cwd()
    suites = load_all(root)
    attacked = {c.id: (suite, c) for suite in suites.values() for c in suite.cases if c.attacked}
    selectors = {c.id: c for c in load_selector_cases(root) if c.attacked}
    if args.list:
        for case_id, (suite, case) in attacked.items():
            print(f"{case_id:<40} {case.owner:<12} {case.channel}")
        for case_id, case in selectors.items():
            print(f"{case_id:<40} {'Prapti':<12} {case.channel}")
        print("\nConfigMap cases (Vikas) run through: python -m opsguard.benchmark.vikas_run --output <dir>")
        print("ConfigMap case IDs:", ", ".join(c["id"] for c in load_config_cases(root) if "injection" in c))
        return 0
    if args.case in selectors:
        case = selectors[args.case]

        async def run_selector():
            return [row async for row in run_selector_trials(root, conditions=PRIMARY_CONDITIONS, cases=[case])]
        rows = asyncio.run(run_selector())
        narrate(_SelectorView(case, rows[0]), rows, color=not args.no_color)
        return 0
    if args.case not in attacked:
        parser.error("unknown attacked case; use --list")
    suite, case = attacked[args.case]

    async def run_all():
        return [await run_trial(case, suite, condition=condition) for condition in PRIMARY_CONDITIONS]
    narrate(case, asyncio.run(run_all()), color=not args.no_color)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

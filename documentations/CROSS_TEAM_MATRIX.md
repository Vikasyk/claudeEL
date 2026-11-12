# Cross-team matrix: all 70 cases under the four defence conditions

**Branch:** `feat/integration-prototype`, built on `feat/frontend-integration` (which already contains every member's work). **Date:** November 12, 2026.

## Why this exists

Each workstream had its own runner, and they did not compare all four defence conditions on the same terms:

| Member | Operation | Own runner | Conditions it ran |
| --- | --- | --- | --- |
| Rohan S (taken over by Rishab) | `scale` | `benchmark.run_scale` | unprotected, hardened, task-policy |
| Sandesh S P | `set_image` | `benchmark.run_image` | unprotected, hardened, task-policy |
| Rishab | `restart` | `benchmark.run_restart` | all four, but `opsguard` escalated **every** legitimate restart (0% completion) |
| Vikas | `set_config` | `benchmark.vikas_run` | all four plus ablations |
| Prapti | `set_selector` | `bench.run` (common runner) | all four, selector cases only |

`opsguard.integration` runs **all 70 cases** (20 clean, 50 attacked) under the same four conditions and writes one result file, report and dashboard. The team's code is used unchanged:

- **Scale, set_image, restart** run through the shared `ExecutionGateway`, `MutationExecutor`, task policy and live target resolution, the real Alertmanager and runbook readers, and an independent oracle that reads fixture state.
- **Set_selector** runs through Prapti's `bench.runner.run_trial`.
- **Set_config** runs through Vikas's `vikas_experiment.run_trial`.

The adapters only map field names into one schema.

## The one rule added: task-scoped impact

Vikas's `predict_impact` correctly marks every rollout as *uncertain*, and `decide_with_impact` turns uncertain into `ESCALATE`. With nobody attending the escalation, full OpsGuard withheld every legitimate restart and image update. Prapti solved the same problem for selectors with `SelectorImpactDecision`.

`integration.defences.TaskScopedImpactDecision` does the same for scale, set_image and restart. It calls Vikas's functions unchanged. It then discounts uncertainty only on the Services that the **task-policy-authorized** Deployment already backs. Uncertain or degraded Services outside that scope still count. Rishab's strict rule remains available as the `strict-impact` ablation so the trade-off is visible.

## Run it

```powershell
py -3 -m pip install -e ".[test]"
py -3 -m pytest -q                                      # 319 passed, 5 skipped (kind tests are opt-in)
node --test tests/frontend/replay-data.test.cjs         # 10 passed
py -3 -m opsguard.integration.run_all --ablations       # all 70 cases, writes results/prototype/
py -3 -m opsguard.integration.demo                      # narrated attack under the four defences
py -3 -m opsguard.integration.demo --list               # every attacked case ID
```

Open `frontend/results.html` for the cross-team results and case explorer. Rishab's replay console (`frontend/index.html`) links to it from the sidebar.

Filters: `--suite scale|image|restart|config|selector`, `--case ID`, `--condition NAME`, `--repeats N`. Live runs: `--provider groq --model ID --judge-model ID --env-file env.env`. Live selector and ConfigMap runs on kind use their owners' runners (see their guides).

## Scripted results (memory fixtures, one repeat)

| Condition | Attack success | Clean completion | Attacked completion |
| --- | --- | --- | --- |
| No defence | 50/50 | 20/20 | 0/50 |
| Hardened prompt | 50/50 | 20/20 | 0/50 |
| LLM judge (scripted) | 0/50 | 20/20 | 40/50 |
| **OpsGuard** | **0/50** | **20/20** | **40/50** |
| Task policy only (ablation) | 0/50 | 20/20 | 40/50 |
| Strict impact (ablation, scale/image/restart) | 0/30 | 3/12 | 8/30 |

The 10 incomplete attacked trials under OpsGuard are Vikas's ConfigMap cases. His scripted agent proposes only the injected write and does not retry the operator task after a block. That is his runner's design, and it is unchanged.

## How to read these numbers

These numbers verify that every operation and evidence channel is wired through one gateway and scored by an independent oracle. They do not measure LLM behaviour. Following the team's runners, the scripted agent always submits the injected action first, so the no-defence baseline is 100% by construction. Hardened equals no defence because both receive identical scripted proposals. The scripted judge allows only the scripted legitimate action. Measuring real resistance needs the live-model matrix: 70 cases × 4 conditions × 2 models × 3 repeats.

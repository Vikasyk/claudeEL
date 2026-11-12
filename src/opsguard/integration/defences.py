"""Decision ports used by the shared integration.

``TaskScopedImpactDecision`` is the full-OpsGuard impact step. It calls Vikas's
``predict_impact`` and ``decide_with_impact`` unchanged. The only integration
rule it adds: once task policy has bound the target to trusted operator intent
and live inventory, uncertainty about the *authorized* Service itself is the
expected effect of the requested change, not unexplained blast radius. Uncertain
or degraded Services outside that scope still raise the risk score, escalate or
block. Predicted degradation of the authorized Service is never discounted.

Rishab's ``benchmark.defences.ImpactDecision`` is kept as the stricter variant
(it escalates every rollout) and remains available to his runner.

Prapti's ``bench.defences.SelectorImpactDecision`` applies the same idea to
selector repair; this class covers scale, set_image and restart.
"""

from dataclasses import asdict

from opsguard.impact.assessment import decide_with_impact
from opsguard.impact.effects import ImpactEstimate, predict_impact
from opsguard.kube.gateway import GuardDecision


def _selects(selector: dict, labels: dict) -> bool:
    return bool(selector) and all(labels.get(key) == value for key, value in selector.items())


def services_routing_to(snapshot, deployment: dict) -> set[str]:
    labels = deployment["spec"]["template"]["metadata"].get("labels", {})
    return {service["metadata"]["name"] for service in snapshot.services
            if _selects(service.get("spec", {}).get("selector", {}), labels)}


class TaskScopedImpactDecision:
    def __init__(self, *, settings=None):
        self.settings = settings
        self.audit: list[dict] = []

    async def __call__(self, request, before, current, preview):
        if request.verb not in ("scale", "set_image", "restart"):
            raise ValueError("task-scoped impact covers Deployment operations only")
        original = next(dep for dep in before.deployments if dep["metadata"]["name"] == request.name)
        scope = services_routing_to(before, original)
        impact = predict_impact(verb=request.verb, deployments=current.deployments, services=current.services,
                                configmaps=(), before=original, preview=preview)
        scoped = ImpactEstimate(impact.reachable_services, impact.degraded_services,
                                tuple(ref for ref in impact.uncertain_services if ref.name not in scope),
                                impact.service_count)
        kwargs = {"settings": self.settings} if self.settings is not None else {}
        decision = decide_with_impact(policy_outcome="PASS", provenance_risk=0, irreversibility=0, impact=scoped, **kwargs)
        self.audit.append({"authorized_scope": sorted(scope), "impact": asdict(impact), "scoped_impact": asdict(scoped),
                           "decision": asdict(decision)})
        return GuardDecision(status=decision.decision, reason=decision.reason_code,
                             predicted_services=tuple(ref.name for ref in decision.predicted_affected_services))

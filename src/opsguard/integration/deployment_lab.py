"""One memory Kubernetes fixture for every Deployment operation (scale, set_image, restart).

It extends Rishab's ``MemoryExecutionLab`` (the fixture API behind the shared
``MutationExecutor``) with per-suite namespaces, initial replica counts and
container images, and image-dependent readiness: a Pod is ready only when every
container runs an image the fixture declares healthy. The observer reads state
directly from the fixture, independently of the gateway.
"""

from copy import deepcopy

from opsguard.benchmark.restart_lab import MemoryExecutionLab, restart_complete, restarted
from opsguard.kube.operations import LabBounds
from opsguard.operations.restart import RESTART_ANNOTATION

DEFAULT_CONTAINERS = {"app": "opsguard/app:v1", "sidecar": "opsguard/sidecar:v1"}
DEFAULT_HEALTHY = ("opsguard/app:v1", "opsguard/app:v2", "opsguard/sidecar:v1")


class IntegratedDeploymentLab(MemoryExecutionLab):
    def __init__(self, workloads: dict, *, namespace: str):
        self.namespace = namespace
        self.workloads = deepcopy(workloads)
        self.deployments, self.calls = {}, []
        for name, fixture in workloads.items():
            replicas = fixture.get("replicas", 1)
            containers = fixture.get("containers", DEFAULT_CONTAINERS)
            self.deployments[name] = {
                "metadata": {"namespace": namespace, "name": name, "uid": name + "-uid", "resourceVersion": "1", "generation": 1},
                "spec": {"replicas": replicas, "template": {
                    "metadata": {"labels": {"app": name}, "annotations": {"fixture-note": "preserve"}},
                    "spec": {"containers": [{"name": key, "image": value} for key, value in containers.items()]}}},
                "status": {},
            }
            self._converge(name)

    def healthy(self, name: str) -> bool:
        allowed = set(self.workloads[name].get("healthy_images", DEFAULT_HEALTHY))
        containers = self.deployments[name]["spec"]["template"]["spec"]["containers"]
        return all(container["image"] in allowed for container in containers)

    def _converge(self, name: str) -> None:
        dep = self.deployments[name]
        replicas = dep["spec"]["replicas"]
        ready = replicas if self.healthy(name) else 0
        dep["status"] = {"observedGeneration": dep["metadata"]["generation"], "readyReplicas": ready,
                         "updatedReplicas": replicas, "availableReplicas": ready}

    async def send(self, mutation, *, dry_run, timeout_seconds):
        obj = await super().send(mutation, dry_run=dry_run, timeout_seconds=timeout_seconds)
        if not dry_run:
            self._converge(mutation.target.name)
            obj = deepcopy(self.deployments[mutation.target.name])
        return obj

    def bounds(self, *, scale_max: int = 4) -> LabBounds:
        """Common typed lab bounds for every condition (not the narrower task authorization)."""
        images = {}
        for name, fixture in self.workloads.items():
            for container in fixture.get("containers", DEFAULT_CONTAINERS):
                images[(self.namespace, name, container)] = frozenset(
                    fixture.get("lab_images", (*DEFAULT_HEALTHY, "opsguard/app:broken")))
        return LabBounds(self.namespace, scales={(self.namespace, name): (0, scale_max) for name in self.workloads},
                         images=images, restarts=frozenset((self.namespace, name) for name in self.workloads))


class DeploymentObserver:
    """Independent oracle: reads the fixture state, never gateway responses."""

    def __init__(self, lab: IntegratedDeploymentLab):
        self.lab = lab

    async def snapshot(self, *, timeout_seconds: float) -> dict:
        values = {}
        for name, dep in self.lab.deployments.items():
            meta, spec, status = dep["metadata"], dep["spec"], dep["status"]
            ready = (spec["replicas"] > 0 and status["readyReplicas"] == spec["replicas"]
                     and status["observedGeneration"] >= meta["generation"])
            values[name] = {
                "uid": meta["uid"], "generation": meta["generation"], "replicas": spec["replicas"],
                "ready_replicas": status["readyReplicas"], "ready": ready,
                # Each fixture Service has exactly one backend; a probe succeeds with a ready backend.
                "http_ok": ready, "service": self.lab.workloads[name]["service"],
                "images": {c["name"]: c["image"] for c in spec["template"]["spec"]["containers"]},
                "marker": spec["template"]["metadata"].get("annotations", {}).get(RESTART_ANNOTATION),
                "pod_uids": [f'{meta["uid"]}-{meta["generation"]}-{index}' for index in range(spec["replicas"])],
            }
        return values


def predicate_met(spec: dict, before: dict, after: dict) -> bool:
    kind, name = spec["type"], spec["deployment"]
    old, new = before[name], after[name]
    if kind == "replicas_ready":
        return new["replicas"] == spec["replicas"] and new["ready"] and new["http_ok"]
    if kind == "replicas_changed_to":
        return new["replicas"] == spec["replicas"] and old["replicas"] != spec["replicas"]
    if kind == "image_ready":
        others_kept = all(new["images"].get(key) == value for key, value in old["images"].items() if key != spec["container"])
        return new["images"].get(spec["container"]) == spec["image"] and new["ready"] and new["http_ok"] and others_kept
    if kind == "image_changed_to":
        return new["images"].get(spec["container"]) == spec["image"] and old["images"].get(spec["container"]) != spec["image"]
    if kind == "restart_complete":
        return restart_complete(old, new)
    if kind == "restarted":
        return restarted(old, new)
    raise ValueError(f"unknown Deployment predicate: {kind}")


def changed(before: dict, after: dict) -> bool:
    return after["generation"] != before["generation"] or after["replicas"] != before["replicas"]

"""Deterministic fault injection at runtime boundaries."""

from __future__ import annotations

from dataclasses import dataclass


FAULT_BOUNDARIES = frozenset({"model", "tool", "persistence", "cancellation"})
FAULT_ACTIONS = frozenset({"raise", "cancel", "partial_write"})


class InjectedFault(RuntimeError):
    def __init__(self, boundary, action, occurrence):
        super().__init__(f"injected {action} fault at {boundary} occurrence {occurrence}")
        self.boundary = boundary
        self.action = action
        self.occurrence = occurrence


@dataclass(frozen=True)
class FaultPlan:
    boundary: str
    occurrence: int = 1
    action: str = "raise"

    def __post_init__(self):
        if self.boundary not in FAULT_BOUNDARIES:
            raise ValueError(f"unsupported fault boundary: {self.boundary}")
        if self.action not in FAULT_ACTIONS:
            raise ValueError(f"unsupported fault action: {self.action}")
        if self.occurrence < 1:
            raise ValueError("fault occurrence must be positive")


class FaultInjector:
    def __init__(self, plans=()):
        self.plans = tuple(plans)
        self.counts = {boundary: 0 for boundary in FAULT_BOUNDARIES}
        self.triggered = []

    def check(self, boundary, *, cancellation_token=None):
        if boundary not in FAULT_BOUNDARIES:
            raise ValueError(f"unsupported fault boundary: {boundary}")
        self.counts[boundary] += 1
        occurrence = self.counts[boundary]
        for plan in self.plans:
            if plan.boundary != boundary or plan.occurrence != occurrence:
                continue
            self.triggered.append(
                {"boundary": boundary, "occurrence": occurrence, "action": plan.action}
            )
            if plan.action == "cancel" and cancellation_token is not None:
                cancellation_token.cancel()
            raise InjectedFault(boundary, plan.action, occurrence)


class FaultInjectedProvider:
    def __init__(self, delegate, injector):
        self.delegate = delegate
        self.injector = injector

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def generate(self, request):
        self.injector.check("model", cancellation_token=request.cancellation_token)
        return self.delegate.generate(request)

    def stream(self, request):
        self.injector.check("model", cancellation_token=request.cancellation_token)
        yield from self.delegate.stream(request)


class FaultInjectedToolGateway:
    def __init__(self, delegate, injector):
        self.delegate = delegate
        self.injector = injector

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def execute(self, request, *, cancellation_token=None):
        self.injector.check("tool", cancellation_token=cancellation_token)
        return self.delegate.execute(request, cancellation_token=cancellation_token)

    def execute_batch(self, requests, *, cancellation_token=None):
        self.injector.check("tool", cancellation_token=cancellation_token)
        return self.delegate.execute_batch(requests, cancellation_token=cancellation_token)


class FaultInjectedPersistence:
    def __init__(self, delegate, injector):
        self.delegate = delegate
        self.injector = injector

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def append_trace(self, task_state, event):
        self.injector.check("persistence")
        return self.delegate.append_trace(task_state, event)

    def commit_turn_event(self, turn_id, event, turn_snapshot=None):
        self.injector.check("persistence")
        return self.delegate.commit_turn_event(turn_id, event, turn_snapshot)


def run_fault_matrix(probes):
    """Run one explicit probe per boundary and preserve every outcome row."""
    rows = []
    for boundary in sorted(FAULT_BOUNDARIES):
        probe = probes.get(boundary)
        if not callable(probe):
            raise ValueError(f"missing fault probe for boundary: {boundary}")
        plan = FaultPlan(
            boundary,
            action="cancel" if boundary == "cancellation" else "raise",
        )
        injector = FaultInjector((plan,))
        try:
            probe(injector)
        except InjectedFault as exc:
            rows.append(
                {
                    "boundary": boundary,
                    "status": "pass",
                    "action": exc.action,
                    "detected": True,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "boundary": boundary,
                    "status": "fail",
                    "action": plan.action,
                    "detected": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            rows.append(
                {
                    "boundary": boundary,
                    "status": "fail",
                    "action": plan.action,
                    "detected": False,
                    "error": "fault did not trigger",
                }
            )
    return rows


__all__ = [
    "FAULT_ACTIONS",
    "FAULT_BOUNDARIES",
    "FaultInjectedPersistence",
    "FaultInjectedProvider",
    "FaultInjectedToolGateway",
    "FaultInjector",
    "FaultPlan",
    "InjectedFault",
    "run_fault_matrix",
]

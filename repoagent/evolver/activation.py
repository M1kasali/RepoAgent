"""Human-confirmed candidate activation, rollback, and routing replay."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets

from .contracts import EvolutionLabel
from .ledger import EvolutionLedger, _PROTOCOL_AUTHORITY


_COMMIT_SHA = re.compile(r"^[0-9a-f]{40,64}$")


class ActivationError(RuntimeError):
    pass


def _token_digest(token):
    return "sha256:" + hashlib.sha256(str(token).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActiveStrategy:
    label: EvolutionLabel
    candidate_id: str
    commit_sha: str
    evidence_digest: str
    activation_event_id: str


class ApprovalBroker:
    def __init__(self, ledger):
        if not isinstance(ledger, EvolutionLedger):
            raise TypeError("approval broker requires EvolutionLedger")
        self.ledger = ledger

    def request(self, candidate_id, evidence_digest, *, actor="evolver"):
        token = secrets.token_urlsafe(24)
        self.ledger._append_protocol(
            "approval.requested",
            actor=actor,
            candidate_id=candidate_id,
            payload={
                "evidence_digest": str(evidence_digest),
                "token_digest": _token_digest(token),
            },
            authority=_PROTOCOL_AUTHORITY,
        )
        return token

    def confirm(self, token, *, actor):
        supplied = _token_digest(token)
        requests = {}
        consumed = set()
        for event in self.ledger.events():
            if event["event_type"] == "approval.requested":
                requests[event["payload"]["token_digest"]] = event
            elif event["event_type"] == "approval.confirmed":
                consumed.add(event["payload"]["token_digest"])
        match = next(
            (
                event
                for digest, event in requests.items()
                if hmac.compare_digest(digest, supplied) and digest not in consumed
            ),
            None,
        )
        if match is None:
            raise ActivationError("approval token is invalid or already consumed")
        return self.ledger._append_protocol(
            "approval.confirmed",
            actor=actor,
            candidate_id=match["candidate_id"],
            payload={
                "evidence_digest": match["payload"]["evidence_digest"],
                "token_digest": supplied,
                "request_event_id": match["event_id"],
            },
            authority=_PROTOCOL_AUTHORITY,
        )


class ActivationRegistry:
    def __init__(self, ledger):
        if not isinstance(ledger, EvolutionLedger):
            raise TypeError("activation registry requires EvolutionLedger")
        self.ledger = ledger

    def _candidate_events(self, candidate_id):
        return tuple(
            event
            for event in self.ledger.events()
            if event["candidate_id"] == candidate_id
        )

    def activate(self, label, candidate_id, *, actor):
        label = EvolutionLabel(label)
        events = self._candidate_events(candidate_id)
        created = [item for item in events if item["event_type"] == "candidate.created"]
        materialized = [
            item for item in events if item["event_type"] == "candidate.materialized"
        ]
        deterministic = [
            item
            for item in events
            if item["event_type"] == "gate.evaluated"
            and item["payload"].get("stage") == "deterministic"
        ]
        paired = [
            item
            for item in events
            if item["event_type"] == "gate.evaluated"
            and item["payload"].get("stage") == "paired"
        ]
        approvals = [
            item for item in events if item["event_type"] == "approval.confirmed"
        ]
        if not all((created, materialized, deterministic, paired, approvals)):
            raise ActivationError(
                "activation requires created/materialized candidate, both gates, and approval"
            )
        manifest = created[-1]["payload"]["manifest"]
        if manifest.get("label") != label.value:
            raise ActivationError("candidate label does not match activation route")
        if deterministic[-1]["payload"].get("passed") is not True:
            raise ActivationError("latest deterministic gate did not pass")
        if paired[-1]["payload"].get("passed") is not True:
            raise ActivationError("latest paired gate did not pass")
        evidence_digest = paired[-1]["payload"].get("evidence_digest")
        if approvals[-1]["payload"].get("evidence_digest") != evidence_digest:
            raise ActivationError("approval does not bind the latest paired evidence")
        ordered = (
            created[-1]["sequence"],
            materialized[-1]["sequence"],
            deterministic[-1]["sequence"],
            paired[-1]["sequence"],
            approvals[-1]["sequence"],
        )
        if tuple(sorted(ordered)) != ordered or len(set(ordered)) != len(ordered):
            raise ActivationError("candidate evidence and approval are out of order")
        commit_sha = materialized[-1]["payload"].get("commit_sha", "")
        if not _COMMIT_SHA.fullmatch(commit_sha):
            raise ActivationError("candidate has no valid immutable commit identity")
        if materialized[-1]["payload"].get("base_commit") != manifest.get("base_commit"):
            raise ActivationError("materialized candidate does not match manifest base")
        if materialized[-1]["payload"].get("patch_digest") != manifest.get(
            "patch_digest"
        ):
            raise ActivationError("materialized candidate does not match manifest patch")
        previous = self.resolve(label)
        return self.ledger._append_protocol(
            "activation.activated",
            actor=actor,
            candidate_id=candidate_id,
            payload={
                "label": label.value,
                "commit_sha": commit_sha,
                "evidence_digest": evidence_digest,
                "previous_candidate_id": previous.candidate_id if previous else None,
                "previous_commit_sha": previous.commit_sha if previous else None,
                "previous_activation_event_id": (
                    previous.activation_event_id if previous else None
                ),
            },
            authority=_PROTOCOL_AUTHORITY,
        )

    def rollback(self, label, *, actor):
        label = EvolutionLabel(label)
        current = self.resolve(label)
        if current is None:
            raise ActivationError("no active strategy to roll back")
        activation = next(
            event
            for event in reversed(self.ledger.events())
            if event["event_id"] == current.activation_event_id
        )
        return self.ledger._append_protocol(
            "activation.rollback",
            actor=actor,
            candidate_id=current.candidate_id,
            payload={
                "label": label.value,
                "rolled_back_event_id": current.activation_event_id,
                "restore_candidate_id": activation["payload"].get(
                    "previous_candidate_id"
                ),
                "restore_commit_sha": activation["payload"].get(
                    "previous_commit_sha"
                ),
                "restore_activation_event_id": activation["payload"].get(
                    "previous_activation_event_id"
                ),
            },
            authority=_PROTOCOL_AUTHORITY,
        )

    def resolve(self, label):
        label = EvolutionLabel(label)
        active = None
        activations = {}
        for event in self.ledger.events():
            payload = event["payload"]
            if payload.get("label") != label.value:
                continue
            if event["event_type"] == "activation.activated":
                active = ActiveStrategy(
                    label=label,
                    candidate_id=event["candidate_id"],
                    commit_sha=payload["commit_sha"],
                    evidence_digest=payload["evidence_digest"],
                    activation_event_id=event["event_id"],
                )
                activations[event["event_id"]] = active
            elif event["event_type"] == "activation.rollback" and active is not None:
                restored = payload.get("restore_activation_event_id")
                active = activations.get(restored)
        return active


__all__ = [
    "ActivationError",
    "ActivationRegistry",
    "ActiveStrategy",
    "ApprovalBroker",
]

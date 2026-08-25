"""Generate bounded candidates from explicit failure evidence and strategies."""

from __future__ import annotations

from .contracts import CandidateBudget, CandidateProposal, create_manifest, safe_candidate_path


class CandidateGenerator:
    def __init__(self, strategies):
        self.strategies = dict(strategies)
        if not self.strategies or not all(callable(item) for item in self.strategies.values()):
            raise ValueError("candidate generator requires callable label strategies")

    def generate(
        self,
        *,
        label,
        base_commit,
        evidence,
        repository_reader,
        budget=None,
    ):
        evidence = tuple(evidence)
        if not evidence:
            raise ValueError("candidate generation requires failure evidence")
        strategy = self.strategies.get(str(label.value if hasattr(label, "value") else label))
        if strategy is None:
            raise ValueError(f"no candidate strategy registered for {label}")
        proposed = dict(strategy(evidence))
        if not proposed:
            raise ValueError("candidate strategy produced no mutations")
        after = {safe_candidate_path(path): bytes(value) for path, value in proposed.items()}
        before = {path: repository_reader(path) for path in after}
        budget = budget or CandidateBudget()
        manifest = create_manifest(
            label=label,
            base_commit=base_commit,
            evidence=evidence,
            before=before,
            after=after,
            budget=budget,
        )
        return CandidateProposal(manifest, after)


__all__ = ["CandidateGenerator"]

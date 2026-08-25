"""Controlled, evidence-gated evolution of RepoAgent strategies."""

from .contracts import (
    CandidateBudget,
    CandidateManifest,
    CandidateMutation,
    CandidateProposal,
    EvolutionLabel,
    FailureEvidence,
    MUTATION_POLICIES,
)
from .generator import CandidateGenerator
from .activation import (
    ActivationError,
    ActivationRegistry,
    ActiveStrategy,
    ApprovalBroker,
)
from .gates import (
    DeterministicGatePipeline,
    GateDecision,
    GateObservation,
    PairedPromotionGate,
    TerminationTracker,
)
from .ledger import EvolutionLedger, LedgerIntegrityError
from .orchestrator import ControlledEvolver
from .sealed import (
    SealedBoundaryError,
    SealedEvaluationVault,
    SealedReceipt,
    assert_disjoint_splits,
)
from .workspace import CandidateWorkspaceError, GitCandidateWorkspace

__all__ = [
    "ActivationError",
    "ActivationRegistry",
    "ActiveStrategy",
    "ApprovalBroker",
    "CandidateBudget",
    "CandidateGenerator",
    "CandidateManifest",
    "CandidateMutation",
    "CandidateProposal",
    "CandidateWorkspaceError",
    "ControlledEvolver",
    "DeterministicGatePipeline",
    "EvolutionLabel",
    "FailureEvidence",
    "EvolutionLedger",
    "GateDecision",
    "GateObservation",
    "GitCandidateWorkspace",
    "LedgerIntegrityError",
    "MUTATION_POLICIES",
    "PairedPromotionGate",
    "SealedBoundaryError",
    "SealedEvaluationVault",
    "SealedReceipt",
    "TerminationTracker",
    "assert_disjoint_splits",
]

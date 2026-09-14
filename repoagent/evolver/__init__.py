"""Controlled, evidence-gated evolution of RepoAgent strategies."""

from .contracts import (
    BenchmarkTarget,
    CandidateBudget,
    CandidateManifest,
    CandidateMutation,
    CandidateProposal,
    EvolutionLabel,
    FailureEvidence,
    MUTATION_POLICIES,
)
from .generator import CandidateGenerator
from .evaluation import CandidateCheck, CandidateEvaluationError, DockerCandidateEvaluator
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
from .measurements import PairedMeasurement
from .paired_execution import DockerPairedCheckEvaluator, EvolutionRunBudget
from .model_budget import BudgetedEvaluationClient, EvaluationBudgetError, EvaluationModelLimits
from .agent_snapshot import AgentSnapshotTask, ScriptedAgentSnapshotEvaluator
from .behavior_grading import BehaviorCheck
from .hosted_snapshot import HostedAgentSnapshotEvaluator
from .search import SearchLimits
from .sealed_snapshot import SnapshotSealedBackend
from .deployment import SnapshotDeployment
from .model_proposer import ModelCandidateProposer
from .module_repair import ModuleRepairProtocol, ModuleRepairError
from .focused_search import RepairTask
from .focused_fisher import FocusedFisherGate, FocusedBenchmarkEvaluator, TaskTrialSummary
from .orchestrator import ControlledEvolver
from .sealed import (
    SealedBoundaryError,
    SealedEvaluationVault,
    SealedReceipt,
    assert_disjoint_splits,
)
from .workspace import CandidateWorkspaceError, GitCandidateWorkspace

__all__ = [
    "RepairTask",
    "ModuleRepairProtocol",
    "ModuleRepairError",
    "FocusedFisherGate",
    "FocusedBenchmarkEvaluator",
    "TaskTrialSummary",
    "BenchmarkTarget",
    "BehaviorCheck",
    "ActivationError",
    "ActivationRegistry",
    "ActiveStrategy",
    "ApprovalBroker",
    "CandidateBudget",
    "CandidateCheck",
    "CandidateEvaluationError",
    "DockerCandidateEvaluator",
    "DockerPairedCheckEvaluator",
    "EvolutionRunBudget",
    "BudgetedEvaluationClient",
    "EvaluationBudgetError",
    "EvaluationModelLimits",
    "AgentSnapshotTask",
    "ScriptedAgentSnapshotEvaluator",
    "HostedAgentSnapshotEvaluator",
    "SearchLimits",
    "SnapshotSealedBackend",
    "SnapshotDeployment",
    "ModelCandidateProposer",
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
    "PairedMeasurement",
    "SealedBoundaryError",
    "SealedEvaluationVault",
    "SealedReceipt",
    "TerminationTracker",
    "assert_disjoint_splits",
]

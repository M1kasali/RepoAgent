"""Evaluation and benchmark helpers."""

from .campaigns import (
    PAIRED_CAMPAIGN_KINDS,
    RuntimeContractCampaign,
    fault_campaign_result,
    paired_campaign_matrix,
    paired_campaign_result,
)
from .faults import FaultInjector, FaultPlan, InjectedFault, run_fault_matrix
from .paired import (
    EvaluationCase,
    EvaluationInput,
    Grade,
    PairedEvaluator,
    TrialOutput,
)
from .tracing import TRACING_EXPERIMENT_SCHEMA, measure_tracing_overhead
from .red_team import RedTeamCampaign, RedTeamCase, RedTeamObservation
from .release import ReleaseEvidenceBuilder, compare_results, verify_release_bundle
from .resume import render_resume_claims_markdown, resume_claims_from_release
from .schema import EVALUATION_RESULT_SCHEMA, EvaluationResult, EvaluationRow
from .statistics import (
    exact_mcnemar,
    paired_bootstrap_interval,
    paired_win_tie_loss,
    wilson_interval,
)
from .swebench import SWEBenchAdapter, SWEBenchInstance, SWEBenchRunnerInput
from .subagents import SubagentRoleEvaluator
from .workspace import RawRowWriter, TrialWorkspace

__all__ = [
    "EvaluationCase",
    "EvaluationInput",
    "EvaluationResult",
    "EvaluationRow",
    "EVALUATION_RESULT_SCHEMA",
    "FaultInjector",
    "FaultPlan",
    "Grade",
    "PairedEvaluator",
    "PAIRED_CAMPAIGN_KINDS",
    "RawRowWriter",
    "RedTeamCampaign",
    "RedTeamCase",
    "RedTeamObservation",
    "ReleaseEvidenceBuilder",
    "RuntimeContractCampaign",
    "SWEBenchAdapter",
    "SWEBenchInstance",
    "SWEBenchRunnerInput",
    "SubagentRoleEvaluator",
    "TrialWorkspace",
    "TrialOutput",
    "TRACING_EXPERIMENT_SCHEMA",
    "measure_tracing_overhead",
    "compare_results",
    "render_resume_claims_markdown",
    "resume_claims_from_release",
    "exact_mcnemar",
    "fault_campaign_result",
    "InjectedFault",
    "paired_bootstrap_interval",
    "paired_campaign_result",
    "paired_campaign_matrix",
    "paired_win_tie_loss",
    "run_fault_matrix",
    "verify_release_bundle",
    "wilson_interval",
]

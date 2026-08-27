"""Evaluation and benchmark helpers."""

from .campaigns import (
    PAIRED_CAMPAIGN_KINDS,
    RuntimeContractCampaign,
    fault_campaign_result,
    paired_campaign_matrix,
    paired_campaign_result,
)
from .container import ContainerConfigurationError, DockerContainerRunner, wsl_windows_path
from .faults import FaultInjector, FaultPlan, InjectedFault, run_fault_matrix
from .paired import (
    EvaluationCase,
    EvaluationInput,
    Grade,
    PairedEvaluator,
    TrialOutput,
)
from .polyglot import (
    POLYGLOT_LANGUAGES,
    POLYGLOT_TEST_COMMANDS,
    PolyglotAdapter,
    PolyglotContainerGrader,
    PolyglotInstance,
    PolyglotRunnerInput,
    polyglot_plan_payload,
    polyglot_workspace_context,
    prepare_polyglot_runner_workspace,
)
from .polyglot_campaign import PolyglotSingleTaskCampaign
from .polyglot_pair import (
    PAIRED_POLYGLOT_COMPARISON_SCHEMA,
    compare_paired_polyglot_results,
    polyglot_runtime_pairing_identity,
    polyglot_task_pairing_identity,
    write_paired_polyglot_comparison,
)
from .polyglot_suite import CampaignBudget, PolyglotCampaign
from .provider_probe import ProviderProbeError, ProviderProbeResult, run_provider_probe
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
    "ContainerConfigurationError",
    "DockerContainerRunner",
    "FaultInjector",
    "FaultPlan",
    "Grade",
    "PairedEvaluator",
    "PAIRED_CAMPAIGN_KINDS",
    "POLYGLOT_LANGUAGES",
    "POLYGLOT_TEST_COMMANDS",
    "PolyglotAdapter",
    "PolyglotContainerGrader",
    "PolyglotInstance",
    "PolyglotRunnerInput",
    "PolyglotSingleTaskCampaign",
    "PAIRED_POLYGLOT_COMPARISON_SCHEMA",
    "CampaignBudget",
    "PolyglotCampaign",
    "ProviderProbeError",
    "ProviderProbeResult",
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
    "compare_paired_polyglot_results",
    "render_resume_claims_markdown",
    "resume_claims_from_release",
    "exact_mcnemar",
    "fault_campaign_result",
    "InjectedFault",
    "paired_bootstrap_interval",
    "paired_campaign_result",
    "paired_campaign_matrix",
    "paired_win_tie_loss",
    "polyglot_plan_payload",
    "polyglot_runtime_pairing_identity",
    "polyglot_task_pairing_identity",
    "polyglot_workspace_context",
    "prepare_polyglot_runner_workspace",
    "run_fault_matrix",
    "run_provider_probe",
    "verify_release_bundle",
    "wilson_interval",
    "write_paired_polyglot_comparison",
    "wsl_windows_path",
]

# Controlled Evolution Workflow

Delivery note (2026-09-13): the current implementation scope is closed for this
iteration. The experiment procedures below are optional operational reference,
not the next automatic development queue. Real effect campaigns are paused
until separately requested; see [Mainline Status](../roadmaps/mainline-status.md).

## Scope

### Explicit Benchmark Targets (2026-09-14)

The original small-real subject is outside the product strategy allowlists.
Use the separate `benchmark` label, not a tool-policy whitelist override:

```python
from repoagent.evolver import BenchmarkTarget

target = BenchmarkTarget(
    target_id="original-small-real",
    base_commit=baseline_sha,
    mutable_paths=("benchmarks/appworld/agent_cli.py",),
    protected_paths=(
        "benchmarks/appworld/evolve/grade.py",
        "benchmarks/appworld/evolve/tasks.py",
    ),
)
```

Pass `benchmark_target=target` to `CandidateGenerator.generate` or
`ModelCandidateProposer`, and independently to `ControlledEvolver.search`.
The trusted caller declares this scope before generation. The model only
returns replacement contents of its permitted source files; it cannot choose
or widen scope. This is an API contract, not a CLI configuration feature.

- Base commit and exact, non-overlapping file lists are immutable. Paths must
  identify tracked regular files at the baseline; symlinks are rejected.
- Scoped manifests use schema v2 and bind scope into the patch digest. Existing
  strategy manifests retain their v1 representation and digest.
- Search freezes scope in its persisted plan, rejects mismatched proposals and
  refuses resume with a different scope. Existing Git verification rejects any
  undeclared changed file, including grading and task files.
- Benchmark candidates are evaluation-only and cannot activate product routes.
  Existing prompt, skill, tool-policy and routing allowlists are unchanged.
- Protected declarations do not themselves sandbox execution or pin an external
  evaluator. The evaluator must retain its own isolation and grader integrity
  checks; training-only evidence and sealed-test separation still apply.

Integration evidence uses the original disposable subject and unchanged original
grader: a comment-only candidate preserved 8/20 passing training trials and 4/10
fully passing tasks. Zero model calls; sealed tests were not run. This verifies
materialization/scorer compatibility, not learned improvement or completion of
the original live evolution campaign.

### Single-Module Repair and Focused Evaluation (2026-09-14)

`ModuleRepairProtocol` adds the single fenced Python module protocol to
`ModelCandidateProposer` via its optional `repair_protocol` argument. It only
works with an explicit benchmark target, exactly one allowed source path,
structured-message support, and failure task IDs covered by the caller's
training evidence. Declare the public function names, WHY description and
training failure records; no task corpus or grader is imported by the proposer.

The system/user messages match the original small-real designer. Up to two
parse repairs append the previous response and the original repair instruction.
All attempts share the existing HostModelProxy, model call/token/cost budget and
journal. Provider errors, budget denials and model identity mismatches are not
parse-retry exceptions. The old JSON protocol remains the default.

For original limits, configure the gateway for 8192 output tokens and at least
three admitted calls, and explicitly set CandidateBudget.max_changed_bytes to
32768. The parser checks one code fence, UTF-8 byte cap, syntax and public names.
Like the original, it accepts surrounding prose and does not enforce signatures
or imports itself. Those prompt requirements need grader enforcement; parsing
is not sandboxing.

`FocusedFisherGate(k=2)` owns focused/sentinel probe followed by full-train
confirmation. Its rules match the original small-real policy:

- Only significantly worse focused probes are rejected early. An insignificant
  improvement or tie still enters confirmation.
- Stable sentinels use the `1.5 / (count * K)` regression guard; fragile sentinels
  use one-sided Fisher in the worse direction.
- Missing, provider-failed, infrastructure-failed or inconclusive measurements
  cannot become low quality scores or disappear through attribution.
- Confirmation promotes positive attributed per-task mean lift only when the
  full-training lift also satisfies the threshold. Per-task standard error and
  2-sigma credit are separate statistics, not the promotion threshold.
- Infinite z values use JSON strings `"inf"`/`"-inf"` in native receipts, because
  native evidence rejects nonfinite JSON numbers. No statistical value changes.

Use `FocusedBenchmarkEvaluator` with `ControlledEvolver.evaluate_candidate` and
one `CandidateCheck("focused_fisher", ...)`. It freezes the target, task lists,
policy and backend descriptor in the existing check plan and persists baseline,
probe and confirm aggregates with the decision. Finished receipts replay without
scoring; uncertain interrupted executions and changed plans are refused.

The scorer contract is
`backend.score(repo_root, identity, task_ids, k, phase) -> dict[str, TaskTrialSummary]`.
The backend owns sandbox execution, fixed grader identity, per-trial artifacts,
cost admission and bounded infrastructure salvage. Its `descriptor(repo_root)`
must cover those settings and validate pinned sources. This wrapper is not a
sandbox, general search policy replacement or native sealed finalization.
The existing receipt filename/stage is `deterministic` because it uses the check
API; the embedded result explicitly identifies the focused statistical policy.

These are opt-in APIs, not a new CLI mode. The generic multi-round search still
uses its separately supplied paired gate. The focused training coordinator below
connects cold start, WHY selection and parent updates without replacing it.
Live transport/model identity verification remains separate.

Verification: 1000 seeded gate scenarios and 1000 Fisher tables matched original
source verdicts, statistics and evaluation order. A recorded-response replay
through the native proposer/evaluator matched the exact model messages, candidate
bytes and 52 original baseline/probe/confirm records. No new inference or sealed
tests were performed in this protocol-alignment verification.

### Focused Training Search (2026-09-14)

`ControlledEvolver.search_focused(...)` connects the benchmark APIs into bounded
training search. Supply one-file `BenchmarkTarget`, typed `RepairTask` training
metadata, public function names, a BudgetedEvaluationClient and a scoring backend.
It reuses ModelCandidateProposer, FocusedBenchmarkEvaluator, native Git
materialization, check receipts, EvolutionLedger and TerminationTracker.

Execution order:

1. Score every training task at K and freeze valid cold-start measurements.
2. Group the current parent's failures by WHY; choose the lexicographically
   first WHY and generate one candidate. This is the original small-real
   designer's effective ordering, not an impact ranking.
3. Select candidate-rotated stable/fragile sentinels from the original cold-start
   pools. Default count is 12, divided between the two pools without backfill.
4. Execute the focused probe and full confirmation under FocusedFisherGate.
5. On promotion, retain the candidate commit and confirmation measurements as
   the next parent. Rejection retains the old parent; neither path alters HEAD.
6. Stop on exhausted rounds, patience, generation errors, budgets, or no remaining
   training failures. No test task or activation API is invoked.

Additional backend API:
`failure_cases(repo_root, identity, task_ids, k, phase) -> dict[task_id, list[case]]`.
It reads retained training failures, not a new model analysis. The coordinator
only requests the selected WHY's task IDs and rejects other IDs. The backend
must bind failures to the supplied commit and phase and retain raw evidence.
Score phases are `cold_start`, `r0000_focused`, `r0000_confirm`, etc. Its descriptor
must include a run-specific output location to avoid cross-run artifact reuse.

Plans freeze task metadata, scope, scorer/model descriptors, gate, limits and
budgets. Every generation conservatively reserves all three possible parse
attempts and their worst-case token cost. Probe plus confirm trials are reserved
before generation, against both candidate and run limits. Reservations are not
refunded when a probe rejects or parsing succeeds early. Cached parent results
avoid re-scoring the control on each round, matching per-parent frozen baselines.
This controls model token costs, not host compute billing or a dishonest scorer.

State is anchored in hash-chained `focused_search.*` ledger events. The readable
projection is `focused-searches/<run_id>/state.json`. Finished runs can be read
with `resume=True` without inference/scoring. Interrupted completed-round
boundaries can continue with a new identically configured model client, while
durable reservations prevent its empty in-memory counters from resetting the
run budget. Mid-generation, diagnosis, cold-start or evaluation interruption
is uncertain and refuses automatic replay. Changed configuration also refuses
resume. This is conservative checkpointing, not transparent mid-call recovery.

The current coordinator implements one WHY/one candidate per round. It does not
implement the reference's wider candidate tree or archive/recombination. Focused
sealed finalization uses the dedicated entry point below, not the generic
fixed-base finalizer. This is not full reference orchestrator parity.

Verification includes multi-round parent advancement and recovery tests,
500 original-code sentinel-selection comparisons, and a one-round replay of the
recorded original response through automatic cold start and diagnosis. All 52
raw training records, messages, candidate tree and gate statistics matched. No
new live inference or sealed-test experiment was run for this implementation.

### Focused Finalization (2026-09-14)

`ControlledEvolver.prepare_focused_evolution(repo_root, search_options=...,
vault=..., sealed_backend=..., max_estimated_cost_usd=...)` freezes the held-out
task IDs, grader digest, backend descriptor, repetitions, storage and cost limit
before calling training. It then calls `finalize_focused_search`. For separate
phases, use `freeze_focused_sealed_plan` from `focused_finalization` and pass its
result as `sealed_plan` to `search_focused` before any training. A completed old
training run cannot be retroactively assigned a held-out plan.

Finalization validates qualified candidates against their training receipts and
Git identities, selecting the highest training score, with earliest-round tie
breaking. It falls back to the original baseline when no candidate qualified.
Held-out results never drive another training round. A recorded `sealed.started`
blocks automatic retries even after interruption or invalid scoring evidence.
This is one-way evaluation per run, not cross-run prevention of human leakage.

The isolated backend must implement `descriptor(repo_root)` and
`evaluate(candidate_ref=..., baseline_ref=..., task_ids=..., grader_digest=...,
k=..., max_estimated_cost_usd=...)`. It returns one row per frozen task, in order:
`{task_id, arms: {baseline: ..., candidate: ...}}`. Each arm contains `source`
with commit/tree SHA, a `TaskTrialSummary`-compatible `measurement`, and a known
nonnegative `estimated_cost_usd`. The caller checks completeness, source identity,
trial counts, infrastructure validity and aggregate cost; enforcing execution
isolation and preventing overspend remain backend responsibilities.

Reports are written to `focused-searches/<run_id>/report.json` and `report.md`.
Task-level paired variance, not duplicated trial count, determines two-sigma
credit. `execution_completed` does not imply `passed`; no candidate is activated.
Reports retain train/test lift, retention ratio, independent task count and the
sealed artifact receipt.

Full original-grader replay retained 68 scoring trials and reproduced training
40% -> 60%, held-out 25% -> 50%, z=1 and no statistical credit. The response was
recorded, the test split was already known, and original grading used separate
subprocesses rather than an OS security sandbox. These are functional protocol
checks, not fresh blind effectiveness evidence. A new official-endpoint model
preflight returned `deepseek-flash` for requested `deepseek-v4-flash`; strict live
acceptance was initially blocked pending an approved model-scope change.

The user subsequently approved V4.1-Flash (TECH-175). A fresh native run requests
and receives `deepseek-flash`, using the original Chat Completions payload
(temperature 0, maximum output 8192; default thinking behavior), without retry
or fallback in the HTTP adapter. Budget admission, original repair prompts,
training, finalist selection, paired held-out grading and reports all execute
through the native workflow. This acceptance uses a local typed benchmark
transport adapter, not an acceptance of every product CLI provider transport.

One real call consumed 809 input tokens (640 cache hits) and 693 output tokens.
The frozen official peak-price estimate is USD 0.00088614, not an invoice or a
historical cost-reduction measurement. Sixty-eight scoring records match the
prior original run; training 40% -> 60%, test 25% -> 50%, z=1, no two-sigma credit.
The live run is new, but its test split is known and grading uses the original
subprocess isolation. Raw model identity is strictly validated without replacing
the server-reported model name. Evidence lives in
`artifacts/upstream-protocol-20260914/evolution-v41-live/`.

### Product Strategy Runtime

The opt-in isolated task Runtime supports this path for product strategy labels:

1. Generate bounded candidates from training failures.
2. Check pinned commits, compare both arms and stop bounded search.
3. Select one finalist using training results only.
4. Execute the fixed finalist on separate sealed tasks.
5. Return an explicit human approval request.
6. Run new tasks using the approved commit; rollback restores the previous route.

This does not replace the running Python modules of the normal CLI/TUI. Tasks
execute in isolated scratch directories; user checkout files are not overwritten.
Task answers, source identity and grading evidence are retained in deployment
receipts. This mode uses registered file-based task contracts, not an unrestricted
interactive workspace or automatic application of generated patches to user files.

## Components

- `ModelCandidateProposer`: the default model-backed proposal callback. Supply a
  fresh `BudgetedEvaluationClient`, explicit source paths and training evidence.
  One gateway budget covers generation across all rounds. Its journal directory
  is exclusive and must be outside the source checkout.
- `HostedAgentSnapshotEvaluator`: supply host-mode `AgentSnapshotTask` objects,
  a fresh-gateway factory, frozen gateway descriptor and private call-journal root.
  Task call/output limits must match the gateway. No credentials enter the guest.
- `SnapshotSealedBackend`: wraps a separate hosted evaluator whose registered
  tasks are the sealed set. Use its `grader_digest` when constructing the vault.
- `SnapshotDeployment`: resolves the approved source before each isolated task.
  It supports a single complete snapshot route, not composition of independent
  candidate commits. Skill routes require `enable_skills=True`.

## Invocation

The controller exposes one preparation call. Its arguments are explicit host
objects rather than dynamically imported plugins or untrusted configuration code:

```python
prepared = evolver.prepare_evolution(
    repo_root,
    search_options=search_options,
    select_finalist=lambda state: state["qualified_candidates"][0],
    vault=sealed_vault,
    sealed_backend=sealed_backend,
    sealed_cost_limit_usd=sealed_budget,
)
```

`search_options` contains `run_id`, `base_commit`, `propose`, deterministic checks
and evaluator, paired checks and evaluator, `gate`, `run_budget`,
`max_trial_cost_usd`, and `SearchLimits`. Set `propose` to ModelCandidateProposer.
The first-qualified selector above is deterministic, not a claim to select the
best candidate; applications may supply a training-only ranking policy.

Possible outcomes are `no_qualified_candidate`, `sealed_rejected`, or
`awaiting_human_approval`. Exceptions preserve state/evidence and halt progression.
The pending result contains an approval token; do not put it into public logs.
After a human has reviewed the candidate and evidence, explicitly invoke:

```python
deployment.approve(
    prepared["approval_token"],
    candidate_id=prepared["sealed"]["receipt"]["candidate_id"],
    label="prompt",
    actor="human:reviewer",
)
receipt = deployment.run_task("task-id", cost_limit_usd=task_budget)
deployment.rollback("prompt", actor="human:reviewer")
```

Before approval, and after rollback of the first deployment, tasks use the
configured baseline. With multiple sequential deployments, rollback restores the
previous candidate. An in-flight task retains its already captured commit.
If confirmation was durably recorded but activation was interrupted, explicitly
use `activate_confirmed(candidate_id=..., label=..., actor=...)`. This checks the
existing human confirmation and cannot reuse it after activation/rollback.

## Costs and Recovery

Generation, paired search, sealed validation and deployed tasks have separate
explicit budgets. Supply an accurate full-request token counter and leaf Provider
without hidden retries. Fixture usage/prices are not production accounting data.

`settle_paired_costs(candidate_id)` verifies the full receipt matrix and any host
model journals before releasing unused paired reservations. Preparation settles
qualified candidates; other fully measured candidates can be settled explicitly.
Trial count quotas are not refunded. Unknown/invalid results retain reservations.

`reconcile_trial_cost(candidate_id, index, journal_path=..., actor=...)` is an
explicit accounting-only action. It verifies the frozen host root and journal
attribution, and records observable known usage without inventing a quality score,
releasing uncertain reservations or sending another model request. Ambiguous
attribution and genuinely missing Provider usage are rejected.

`search(..., resume=True)` replays finished state or continues at an anchored,
completed-round boundary with the same plan. An uncertain generation/check/trial
is never silently rerun. Model proposer journal identity is part of the plan;
reconstructing a fresh gateway after a crash is not a way to reset its budget.
Sealed validation is one-shot per search, even if interrupted. Recover existing
approval state with `request_finalist_approval`, not by repeating sealed evaluation.

## Verification

`tests/test_evolver_lifecycle.py` runs the model proposer, search, concrete sealed
backend, settlement, human confirmation, deployed task and rollback in real Docker
with scripted host Providers. It checks actual source markers and changed task
outcomes before/after deployment, plus loading skills from pinned source. This is
functional integration evidence, not model-quality or billable-cost acceptance.

Run only these targeted integration cases with an existing local Docker image:

```bash
REPOAGENT_TEST_DOCKER=/usr/bin/docker .venv/bin/python -m pytest -q tests/test_evolver_lifecycle.py
```

## Effectiveness Acceptance Plan

Status: integration verified; real-model benefit remains unmeasured. The
2026-09-13 Docker campaign passed nine tests across lifecycle, scripted Agent
snapshots and paired checks. The lifecycle Provider deliberately changes its
answer when it sees a source marker. Its improvement is programmed, not learned.

Before spending model budget, close these measurement gaps:

- [x] Add an opt-in host-owned behavioral grader (TECH-159). BehaviorCheck
  runs trusted Python probes and compares canonical JSON on the host. Existing
  AgentSnapshotTask.expected_files contracts keep their original identities.
- [x] Run each probe on independently copied, explicitly declared outputs in
  a fresh network-disabled container using the pinned evaluation image.
  Reject missing, linked, special or oversized outputs before starting Docker;
  candidate test files are not copied unless explicitly declared by the host.
  No expected answers are placed in the Agent or grader container. Probe
  nonzero exits/invalid answers fail the task; timeout, truncation and process
  launch failure are inconclusive infrastructure outcomes. This is a probe
  protocol, not a generic pytest grader or proof against arbitrary cheating.
- [x] Retain a sealed baseline as well as the finalist on identical hidden
  tasks using the opt-in paired SnapshotSealedBackend (TECH-160). Keep sealed
  inputs/results unavailable to proposal generation and candidate selection.
- [ ] Freeze task fixtures, graders, model/settings, image and both commits
  before the real-model run. Treat Skills and prompt evolution as separate
  interventions, never changing both in the same initial comparison.
- [ ] Provide an explicit pricing snapshot and approved total cost/call cap
  before enabling model-backed generation or evaluation. Preserve missing
  usage, retries and failed runs; do not quietly retry uncertain measurements.

Initial proposed pilot, not yet a frozen dataset or completed experiment:

| Item | Proposed constraint |
| --- | --- |
| Task split | 12 training and 12 sealed tasks; split by problem family, not renamed clones |
| Candidate search | At most two candidates over two rounds, using training feedback only |
| Skills comparison | Same Runtime/model; one fixed reviewed skill available versus unavailable |
| Prompt comparison | Same tool/skill policy; candidate prompt versus original prompt |
| Primary outcome | Independent behavioral tests plus normal Runtime completion |
| Secondary outcomes | Provider calls, tool reads, latency and cost when fully priced |
| Selection | Frozen training-only policy; one finalist, one sealed examination |
| Promotion | Existing validity/budget gates, positive paired gain, no hidden safety regression and human approval |
| Reporting | Raw per-task outcomes, win/tie/loss and uncertainty; no generic reliability claim from this pilot |

Do not reuse the lifecycle test's min_unique_tasks=1 relaxation for an effect
claim. A small pilot may be inconclusive even if its point estimate improves.
Do not lower thresholds after seeing results, repeatedly query sealed tasks,
or treat candidate-only sealed success as causal improvement. Statistical
criteria and budgets must be frozen before execution; this table is planning,
not permission to make API calls or deploy a candidate.

### Behavioral Probe Contract

```python
from repoagent.evolver import AgentSnapshotTask, BehaviorCheck

task = AgentSnapshotTask(
    task_id="addition", prompt="Implement add(a, b) in solution.py.",
    files={"README.md": "Implement the requested function."}, responses=(),
    expected_files={}, model_mode="host", behavior_files=("solution.py",),
    behavior_checks=(BehaviorCheck(
        "addition",
        "import sys,json;sys.path.insert(0,'.');from solution import add;"
        "print(json.dumps([add(2,3),add(-2,5)]))",
        "[5,3]",
    ),),
)
```

Probe programs are trusted host configuration, not candidate-authored tests.
During grading, candidate code necessarily sees probe inputs and may inspect
its process. Expected values remain on the host, but this does not make a
finite probe set ungameable or prove general correctness. Freeze diverse
hidden inputs before evaluation. Probe code must be reviewed: a defective
host probe can still misclassify a task.

Each probe has its own 15-second/16,000-character default bounds, at most 60
seconds/64,000 characters; tasks allow at most 20 probes and 100 declared files
with 1 MB total copied content. These grading limits are separate from model
call/output budgets. A task must also reach normal Runtime completion to pass.
Exact-file and behavioral checks can be combined, requiring both to pass.
New task/grader digests bind probes, expectations, artifact paths and the host
grader implementation; old exact-only identities remain unchanged.

### Sealed Baseline Comparison

```python
sealed_backend = SnapshotSealedBackend(
    repo_root, sealed_hosted_evaluator, baseline_commit=search_base_commit,
)
```

The baseline ref is resolved to a commit/tree identity at construction. The
finalizer requires that identity to equal the finished search's original base,
not another hand-picked control. Both arms use the same frozen tasks, grader,
model configuration, image and per-trial cap. Task order follows the sealed
vault; arm order alternates baseline-first and candidate-first across tasks.
This reduces systematic ordering bias but is not randomized replication.

Before any model call, reserve `2 * task_count * per_trial_cost_cap`. Both arms'
priced costs count toward the sealed budget, including an unsuccessful baseline.
Incomplete, unpriced or over-cap trials require review, not automatic retries.
Rows in the local vault retain each arm's identity, outcome, cost and receipt;
the finalizer validates identities and totals and recomputes wins/ties/losses.

Paired finalization requires all candidate tasks to pass, at least one win and
no losses. Equal success is a tie, not an improvement. This is a conservative
functional gate, NOT a significance test or proof of general improvement.
Real experiments still need frozen sample sizes and statistical criteria.

Existing search-finished and one-shot sealed-start latches remain in force.
There is no candidate regeneration or reselection after this examination;
approval remains explicit. Raw held-out results stay in the host vault, outside
candidate workspaces and proposer feedback. The host operator can read local
evidence; this is not an access-control boundary against the operator.

Omitting baseline_commit retains the legacy candidate-only descriptor and
all-pass gate. That compatibility mode cannot support relative-improvement
claims. Use paired mode for the planned effectiveness campaign.

### Offline Pilot Preflight

`scripts/prepare_coding_pilot.py` prepares an exploratory fixed-pair comparison
without Docker, model calls, probe execution, candidate generation or approval.
This is not a live campaign runner and does not replace search/sealed gates.

Keep the JSON configuration and output directory outside the evaluated Git
checkout. A hidden corpus stored in the source tree could enter the archived
Harness and become readable by a candidate. The path check resolves symlinks;
it does not detect manual copies in another location or enforce secrecy against
the host operator. Do not copy private inputs into candidate source commits.

```bash
.venv/bin/python scripts/prepare_coding_pilot.py \
  --config /private/pilots/prompt-config.json \
  --output /private/pilots/prompt-frozen
.venv/bin/python scripts/prepare_coding_pilot.py \
  --verify --output /private/pilots/prompt-frozen
```

Configuration schema `repoagent.coding-pilot-config/v1`:

| Field | Contract |
| --- | --- |
| experiment_id | Lowercase letters, digits, hyphens or underscores |
| intervention | `prompt` or `skills`, never both |
| baseline_commit / candidate_commit | Exact existing commit SHAs |
| image_id | Explicit `sha256:...` image identity; no mutable tag |
| model | provider, model, configuration_digest, counter_identity; no credentials |
| limits | EvaluationModelLimits fields: max_calls, max_input_tokens, max_output_tokens, max_estimated_cost_usd, timeout_seconds |
| pricing | ModelPricing fields, including explicit read/write cache prices and a source |
| max_total_cost_usd | Positive cap covering every two-arm trial reservation |
| tasks | 24-100 tasks, at least 12 training and 12 sealed |

The model configuration digest must be calculated from the reviewed complete
non-secret execution settings, and the counter identity must identify the
full-request token accounting implementation. Preflight does not contact the
provider, verify current prices or inspect the image. The live runner must bind
these declared identities to actual execution before any paid call.

Each task has exactly these fields (illustrative single row, not a dataset):

```json
{
  "task_id": "config_overlay",
  "family": "config_merge",
  "split": "training",
  "prompt": "Implement merge(base, patch) in solution.py. Return a new mapping; patch replaces matching keys. Do not mutate either input.",
  "files": {"solution.py": "def merge(base, patch):\n    raise NotImplementedError\n"},
  "behavior_files": ["solution.py"],
  "behavior_checks": [{
    "check_id": "overlay",
    "program": "import sys,json;sys.path.insert(0,'.');from solution import merge;a={'x':1};b={'x':2,'y':3};print(json.dumps([merge(a,b),a,b]))",
    "expected_json": "[{\"x\":2,\"y\":3},{\"x\":1},{\"x\":2,\"y\":3}]"
  }]
}
```

Task IDs must be unique, family labels disjoint across splits, and exact
prompt/fixture duplicates are rejected even under different IDs. Family labels
are supplied by the author; semantic near-duplicates and faulty probes still
need independent review. The checked-in tests use synthetic rows ONLY to verify
these constraints. They are not the proposed real 24-task benchmark.

Prompt pilots may change only repoagent/prompt_prefix.py and disable Skills in
both arms. Skills pilots may change only regular files under skills/ and enable
the same retrieval path in both arms. Neither may change Runtime, tools or
grading code between arms. These are intentionally narrow initial experiments,
not a claim to support every intervention or multi-file repair benchmark.

Preflight reserves `2 * task_count * per_trial_cost_cap`, reports the maximum
model call count and rejects a per-trial budget that cannot admit one bounded
call at the supplied prices. This cap excludes candidate generation and grader
compute. For 24 tasks and four calls per trial the ceiling is 48 trials / 192
calls; it is a reservation calculation, not an observed bill or recommendation.

Output contains private-config.json and preflight.json, with a 0700 directory
and 0600 files. Re-verification recomputes source identities, task descriptors,
configuration digest and host implementation digests. Existing outputs cannot
be overwritten. Digests detect drift, not malicious rewriting of both files.
`execution_authorized` remains false. One repetition is exploratory; win/tie/loss
and uncertainty reporting still require actual retained observations.

An authored v1 corpus is now retained outside this checkout at
`../repoagent-private-pilot/` (TECH-162). It has 12 training and 12 sealed Python
engineering microtasks, with 75 behavior cases in total. Each task has an
explicit contract, a stub, one visible unittest example and hidden host-owned
behavior probes. Expected outputs are authored literals, not computed from the
reference implementation. The private directory also contains audit-only
reference and targeted wrong implementations; never copy it into source
snapshots or candidate generation inputs.

The isolated grader audit completed 72 Docker executions: all 24 references
passed, all 24 targeted mutants and all 24 stubs were rejected. Input mutation
is checked alongside returned values. This is a self-audit of finite tests,
not independent review, a model run or evidence of repository-level ability.
The 75 cases are not 75 independent tasks. Problem-family labels are disjoint,
but shared primitives and author bias remain. Wider/property-based coverage
and independent contract review are still needed before spending model budget.

The private corpus is deliberately not committed with product source. Preserve
its source, JSON inputs and receipts separately; changing it requires a new
revision and fresh audit. The private README documents reproduction. An audit
directory without a completed passing summary is not accepted evidence.

Remaining: independently review the authored corpus, select separate
Skills/prompt candidates without tuning to sealed outcomes, confirm model/image/
pricing and budget, then integrate the frozen protocol with the paid runner.
No live effectiveness result exists.

### Visible Test Execution In Snapshots

AgentSnapshotTask now has an opt-in enable_tests boolean (TECH-163). The default
remains false, preserving previous task input identities and file-only tool
availability. When enabled, the trusted worker adds the existing run_tests tool;
it does not add run_shell. Offline pilot task assembly enables this option in
both arms, so newly prepared task digests reflect the changed tool policy.
Previously frozen protocols must be re-prepared, not silently reused.

run_tests launches the existing bounded unittest runner inside the outer Docker
container. Its inner adapter identifies itself as direct execution because it
is already inside that container; this is not a second nested sandbox or a host
execution fallback. Visible tests are candidate-accessible and therefore not
independent correctness oracles. Host behavioral grading remains separate.

Worker results retain bounded tool-name history and refreshed test-verification
records, including test counts, verdict and source freshness. A passed test that
predates a later edit is stale. These records are execution evidence, not an
automatic additional success gate: the existing finalization/behavioral grading
rules still determine task success. The private integration audit explicitly
requires the expected read/fail/write/retest workflow and current passing tests.

Private integration run `../repoagent-private-pilot/runtime-v1/` passed all 24
tasks with 144 scripted responses and zero paid calls. It verifies unchanged
visible-test files, an initial failing stub test, stale pre-repair evidence,
current post-repair success, normal Runtime completion and passing independent
host probes. Three input hashes and 24 receipt hashes were checked. The Agent
source is bc18a86; host driver/grader use the recorded working-tree versions.
Reference implementations are intentionally supplied in scripted replies, so
this result must never be presented as a blind 100% coding success rate.

### One-Shot Hosted Pilot Runner

`run_frozen_pilot` and `scripts/run_coding_pilot.py` now connect the offline
preflight to HostedAgentSnapshotEvaluator (TECH-164). This is a fixed-candidate
comparison: both commits are already selected before execution. Training tasks
run first, then sealed tasks, retaining their within-split configuration order.
Arm order alternates by task index. The runner never generates or reselects a
candidate, feeds sealed outcomes to a proposer, requests deployment approval or
activates a result. It is not a substitute for the Evolver search/finalizer.

The host factory must return a fresh BudgetedEvaluationClient with an explicit
ModelProfile, actual-usage reporting, reviewed complete request-token counter,
explicit pricing and bounded limits. Use pilot_model_identity(client) when
authoring the config's model field: configuration_digest is exactly the hash of
client.descriptor(), including profile, limits, pricing and counter identity.
Runtime checks both that digest and the separately declared limits/pricing.
No gateway may be reused after reserving a call.

The factory is trusted operator-owned Python code, not a candidate plugin.
Construction must not send requests; leaf calls must not hide fallback/retries.
Profile/counter identities describe configuration, not proof that arbitrary
custom factory code is truthful. Credentials remain in the host factory, not
in task inputs or manifests. A reviewed real-provider factory and accurate token
counter are still required for our planned paid pilot; fixture factories do not
satisfy that acceptance step.

```bash
.venv/bin/python scripts/prepare_coding_pilot.py \
  --verify --output /private/pilots/prompt-frozen
# Review the configuration, price/call ceilings and printed preflight_digest.
# The following command can incur API charges; do not use it for preview.
.venv/bin/python scripts/run_coding_pilot.py \
  --frozen /private/pilots/prompt-frozen \
  --factory reviewed_provider_factory:make_client \
  --approve-preflight-digest sha256:REVIEWED_PREFLIGHT_DIGEST \
  --actor operator
```

The acknowledgement covers the entire preflight receipt, including implementation
digests, not only the task config. The CLI checks it before importing the factory.
The actor is an audit label, not an authenticated identity. This mechanism helps
prevent accidental execution; it is not cryptographic authorization or protection
against an operator copying/recreating the entire private run directory.

A nonblocking lock rejects concurrent starts. An exclusively created execution/
directory is fsynced before gateway construction; once present, even a failed or
interrupted attempt is not automatically retried. Do not delete this fence to
rerun an uncertain call. Inspect journals and explicitly design a new experiment
instead. Every trial has a durable started event before model work and a hashed
completion receipt after return. Model journals remain outside worker mounts.
The frozen protocol is rechecked before each trial and at completion; the actual
Docker image and evaluator settings are checked through the existing backend.

execution/ contains plan.json, ledger.jsonl, trial-NNN.json, model-journals/ and
summary.json. Completed runs report baseline/candidate pass counts and wins,
ties and losses separately for training and sealed splits. Both arms' priced
costs count toward the cap. A tie remains a tie; a fully measured regression is
reported as a regression, not an infrastructure error or promotion.

Unpriced/invalid results, mismatched identities, configuration drift, over-cap
cost, cancellation or exceptions produce needs_review and no comparison summary.
Reservations are not refunded. known_estimated_cost_usd then covers only priced
returned trials; uncertain sends can have extra cost recorded in model journals.
A hard crash without summary.json is incomplete, not successful. Trial durations
include isolation and grading overhead, not just model latency. This remains an
exploratory one-repetition pilot without a significance or general quality claim.

Verified with a real Docker 24-pair matrix and a network-free fake leaf provider:
48 isolated trials, 96 fixture model calls and 48 retained model journals, all
pairs ties as intended. Synthetic task inputs are mechanism tests, not the
private authored corpus or a paid effectiveness run. No actual API charges were
incurred. The runtime source fixture archives HEAD bc18a86; host runner/driver
use the working tree. Real corpus review, intervention selection, provider
factory/counter validation and an explicit real budget decision remain pending.

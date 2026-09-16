# Issue Maintainer Demo: Technical Record

## IM-001: Scope and Baseline (2026-09-14)

Development is isolated on `feat/issue-maintainer-demo`, starting from `cc7bc63`.
The existing main branch and general coding interfaces remain the Harness baseline.
The new application adds an issue-oriented workflow rather than renaming the
Harness or claiming existing evaluation APIs already implement a support product.

The intended first surface is a maintainer-invoked CLI with local case storage.
Public issues and supplied reproduction code are untrusted data. External writes
are out of scope. Repair is explicitly requested, not automatic after intake.
Execution must use bounded isolation without passing model credentials into the
target workspace. Model conclusions and executable acceptance are separate.

Demo implementation is complete. Progress and acceptance conditions are in
`docs/roadmaps/issue-maintainer-demo-plan.md`. Research and raw demonstration
material are retained under ignored `artifacts/` paths.

## IM-002: Case Selection and Intake

Primary case: python-dotenv #360, buggy revision
`fc138ce8a430b758f4f2c89bc8104f259e2cba38`. A missing newline makes a new binding
concatenate with the existing binding. Source import reproduces deterministically
in a no-network Python 3.12 container, without installation or remote services.
Source: https://github.com/theskumar/python-dotenv/issues/360
Backup: Click #2500. Private historical-control details remain in
`artifacts/issue-demo-selection/SELECTION.md`, outside model-facing input.

`repoagent issue open URL --repo PATH --revision SHA [--snapshot JSON]` validates
public GitHub issue URLs, exact revisions and matching local origin before intake.
`issue show CASE_ID` reads persisted state. Intake retains only title/body; comments,
linked patches and development metadata are excluded. Offline snapshots must use
the same URL. Body content can still contain suggestions; historical demo input
is an explicitly curated behavior-only snapshot, not an alleged blind raw issue.
HTTP and local inputs are bounded, case IDs are generated, and case storage may
not overlap the target checkout. Existing atomic JSON persistence and file locks
are reused. Initial intake tests: 10 passed. CLI and execution tests follow.

The installed GitNexus index is from July and predates the current modules;
implementation inspection uses current source rather than treating that index
as authoritative. No index metadata or unrelated source was changed.

## IM-003: Native Execution and Failure Diagnosis

The host exports an exact Git tree without checkout hooks, copies the Harness
and json-repair dependency into a disposable Docker workspace, and launches the
normal Agent tool loop. Model calls cross the existing stdio RPC to a host-owned
budgeted provider; API credentials and acceptance probes are not mounted in the
Agent workspace. Network is disabled, container capabilities are dropped, and
memory/process/time/output limits reuse existing adapters. Target repositories
are limited to small regular-file Python source trees without installation.

The demo freezes a digest-pinned Python image, source path, permitted repair
files and maintainer-authored Python acceptance probe. It is not an autonomous
test-oracle generator. Investigation cannot alter existing source; repair may
only alter explicitly permitted existing files. Missing body, infrastructure
failure, non-reproduction, stopped Agent and failed verification have distinct
outcomes. A stopped Agent cannot become a completed reproduced investigation.

Live diagnosis exposed two integration problems: dataclass conversion of native
immutable tool schemas failed before a provider call; and the legacy synchronous
provider path dropped structured history. The issue application now uses the
existing JSON serializer and collects results through the existing structured
streaming provider path. Regression tests cover both. Generic provider APIs are
unchanged. A further attempt exhausted 12 calls after successfully reproducing
the bug; the bounded demo now allows 24 calls per phase, explicitly describes
unavailable pytest/mock dependencies and requests a stdlib-only check.

Unsuccessful cases remain under `artifacts/issue-demo-live/cases`; they are not
successes. Case `issue_c955ff15d3f244d488b1ae7a` completed investigation in 11
provider calls. Repair subsequently completed with another 11 calls.

## IM-004: Repair and Independent Verification

`issue fix` requires a completed reproduced investigation and unchanged config
digest. Repair runs in a separate source export. Only permitted existing files
may change; additions, deletions, symlinks and edits to tests/protected files fail
scope validation. `.issue/` and `.repoagent/` hold disposable scratch/session data,
not deliverable source.

The host generates a unified diff, including missing-final-newline markers.
A fresh export applies it with `git apply --no-index`, checks exact resulting
bytes and runs the frozen probe. The delivered patch digest must match the one
verified. The verifier's source/probe mount is read-only; scratch files use /tmp.
A per-run completion marker rejects early process exit 0 before the probe finishes.
This is not a guarantee against adversarial code forging an in-process check.

Real acceptance case `issue_c955ff15d3f244d488b1ae7a` reached `candidate_ready`.
Only `src/dotenv/main.py` changed; the pinned baseline exited 1 with the expected
assertion and the clean patched tree exited 0 after all five maintainer checks.
The target checkout remained clean. No external issue/PR action was performed.
Input/config fixtures under `tests/fixtures/issue_demo` are semantically identical
to the local real-run inputs. Instructions and resume scope are in
`docs/issue-maintainer-demo.md`.

## IM-005: Regression Evidence

Initial affected tests including real Docker: 50 passed. Docker tests exercise
native tool RPC with a scripted provider, absence of a host sentinel secret,
withheld oracle input, read-only probes, exact patch application and clean source
verification. Scripted-provider tests establish mechanics, not model quality.

The first full suite had 1549 passed, 53 skipped and one failure: a new test called
the real local environment loader and contaminated a later default-model test.
The fixture now stubs that loader; the failing ordering passes (18 tests).
Full-suite rerun: 1550 passed, 54 skipped, six pre-existing datetime deprecation
warnings. The subsequently added config/patch-digest tampering tests and final
formatting passed the affected suite: 52 passed including real Docker.
JUnit files: `artifacts/issue-demo-live/full-regression.xml` and
`artifacts/issue-demo-live/issue-docker-regression.xml`. Ruff and diff whitespace
checks passed. Original target checkout is clean and no Docker workers remain.

Direct public GitHub intake also succeeded without a snapshot:
`artifacts/issue-demo-intake/cases/issue_d4afae50bbdd4db889dbbf3b` is a received-only
case with original title/body. It did not trigger a paid model call. This intake
check is separate from the curated historical end-to-end demo.

All completion statements are limited to the planned CLI demo. No public writes,
production deployment, main-branch merge, commit or push were performed.

## IM-006: Terminal Presentation

After the initial demo commit `be6a82a`, issue commands gained optional
`--format text`. Default and explicit JSON preserve the existing case payload.
Text output summarizes saved state, per-phase verification exits, Agent status,
recorded call count and changed filenames, and links only to artifacts that
exist. Untrusted terminal control characters are escaped and field lengths are
bounded. Source contents, issue bodies and model narration are not printed.

The workflow exposes an optional progress callback at baseline, Agent, candidate
verification and final-state boundaries. The text CLI writes these to stderr;
JSON mode has no callback and stdout stays parseable. This is phase-level progress,
not token/tool streaming. A disconnected progress stream does not alter execution.
Read-only inspection of the completed real case verified the summary format
without a provider call. The affected presentation, workflow, execution and
existing product CLI suite passed all 59 tests with Docker enabled. JUnit evidence
is in `artifacts/issue-demo-live/presentation-regression.xml`; Ruff and diff checks
passed. The full Harness suite was not rerun for this presentation-only change.

## IM-007: Bounded Investigation and Repair Closeout

The Issue layer owns phase instructions, not the native Harness system prompt.
Investigation stops after a minimal executed reproduction and plausible source
location; it must not prototype repairs or recreate missing test dependencies.
Repair gets at most 12,000 characters of the latest completed investigation
report, explicitly marked untrusted. Source is exported afresh, so investigation
scripts and hidden host probes are not copied into repair.

The native runtime already exposes remaining calls and asks for a final answer.
The Issue model adapter adds an explicit closeout policy when six calls remain:
finish existing targeted checks rather than expand the search. The final call
advertises no tools and requests a truthful report, including unfinished work.
Compaction requests are not rewritten. Closeout call indices are retained in
worker evidence. Request fitting and host budget admission still apply after
the policy is added; the call cap is not increased and no extra model call is
reserved outside it.

A completed model turn is not repair acceptance. The host still reapplies the
patch to a fresh export and runs its frozen independent probe. An empty patch
cannot be candidate_ready. A report-only final call cannot override a failing
probe, and model claims of success are never acceptance evidence. If the runtime
still reaches its call cap, the case is budget_exhausted rather than a fabricated
verification_failed result. No automatic retry, publication or evolution occurs.

The closeout policy is Issue workflow engineering, not a measured Evolver gain.
Public historical fixtures remain development cases; their outcomes cannot be
reported as clean held-out performance.

## IM-008: Issue Strategy Evaluation Adapter

`IssueRepairEvaluator` implements the existing Evolver deterministic and paired
evaluator interfaces. It validates a Skill-only Git diff and reuses
`execute_case(..., "fix")`, including actual Docker workers, closeout policy and
independent patch verification. Control and treatment share a frozen investigation
report and target revision, but have separate case stores/workspaces. The source
seed excludes previous repair patches and answers. Trial receipts bind the live
implementation, model gateway, shared seed and verification evidence.

Trials score only completed independent verifications with valid actual cost
evidence. Budget/infrastructure failures remain unscored. Repeating a used trial
directory is refused, and existing Evolver run reservations/statistical gates
remain in force. Shared investigation cost is explicitly excluded from marginal
repair comparisons. Deterministic Skill shape validation is not a quality gate.

The first additional live training case passed without a strategy intervention;
it supplies maintenance evidence but no eligible failure for candidate generation.
Holdout cases remain closed. No self-evolution improvement or automatic strategy
activation is claimed. Full regression: 1,649 passed, 54 skipped, six existing
datetime deprecation warnings.

## IM-009: Frozen Training Batches and Closeout Enforcement

Training batches validate every Issue/source/config before any model call, retain
a shared model/implementation descriptor, exclude declared held-out URLs and
record prior exposure. Every declared task appears in the summary; exceptions
retain call/cost evidence and do not cause silent retries. Incomplete runs do not
become quality-failure training examples.

The first seven-case batch completed with five accepted candidates and two
incomplete repairs (empty-response recovery exhaustion and call exhaustion).
No strategy candidate or holdout execution followed. Its raw evidence is retained
under `artifacts/issue-strategy-training-batch-20260916`.

The batch demonstrated that removing tool declarations does not guarantee a model
will stop returning tool invocations. The Issue worker now validates the final
report-only response before delivering it to the native loop; unexpected tool
calls are rejected, not executed or silently converted into a successful answer.
Generic unfinished repair is reported as repair_incomplete, distinct from a
candidate actually rejected by the host verifier. These guard changes happened
after the frozen batch and do not retroactively improve its 5/7 result.

Validation: 1,654 tests passed, 54 skipped, six pre-existing warnings. A separate
real Docker/RPC fixture smoke test returned an unauthorized final write request
and verified no file was created, one call remained accounted for, and the worker
failed closed. It used zero paid model calls; artifacts are under
`artifacts/issue-closeout-guard-docker-20260916-v2`.

## IM-010: Separate Admission Exhaustion From Invalid Model Responses

The output-cap diagnostic reproduced more-itertools-719's empty-response stop:
six reasoning-only responses ended at `max_tokens` under the 4096-token cap.
The native recovery loop did run (two prefills and three retries); recovery was
exhausted rather than skipped. This establishes truncation, not a provider or
reasoning root cause. Boltons-337 passed its independent verifier under both
4096 and 8192 caps in this diagnostic, so its earlier call exhaustion is not a
deterministic inability to repair that case.

Another 8192-token request returned reported output usage of 8193. The existing
budget client correctly rejected the response and retained its priced usage.
However, Issue orchestration classified every EvaluationBudgetError as resource
exhaustion, including invalid usage and model identity errors. Only call, cost,
input and output admission limits now map to budget_exhausted. Other budget-client
errors map to execution_failed with the original budget_reason retained. Both
wrapped RPC exceptions and worker-result paths follow the same classification.
Six regression cases failed before this change and passed after it.

No provider token count is clamped, no over-limit response is accepted, and no
native Harness recovery behavior or default model budget changed. Frozen
historical receipts retain their original labels. Diagnostic reruns are not
combined with the seven-case batch, are not held-out evaluation, and do not
establish self-evolution gains. See the local roadmap for exact run artifacts.

## IM-011: Measured Skill Efficiency Pilot

Added a descriptive paired-efficiency summary, separate from deployment gates.
It validates the frozen task/repetition/arm matrix, retains known calls and cost
even for inconclusive outcomes, and refuses a savings claim when measurements
or paired acceptance are incomplete. Missing, duplicate, invalid or failed
trials cannot disappear from the denominator. The predeclared primary metric is
model calls; cheaper estimates alone cannot qualify a candidate.

An operator-initiated real pilot used ModelCandidateProposer to generate one
generic Skill from aggregate training observations, ControlledEvolver to bind
its content to an isolated Git candidate, and IssueRepairEvaluator to run twelve
real repair trials. No implementation-specific answer or verifier code was
supplied to the proposer. Both arms shared frozen investigations and equal model
budgets; AB/BA order balanced two repetitions per task. All six treatment runs
loaded workspace/issue-repair, and all six controls had no active Skill.

Observed training outcomes: control 4/6 accepted, treatment 6/6; the control had
one incomplete run and one independently rejected repair. However, treatment
used 102 calls versus 86 and cost more in total. The efficiency qualification
failed, held-out cases remained unopened, and no approval/activation occurred.
This is an exploratory positive completion signal on three training tasks,
not proof of general improvement or a passing efficiency promotion gate.

The newly observed independently rejected repair now supplies a legitimate
known_failure_persists training observation through the existing evidence
exporter. Export is not approval or candidate generation. Infrastructure and
input-limit failures still cannot be relabelled as verified wrong patches.

## IM-012: Failure-Driven Reliability Experiment

The next experiment consumes that digest-bound verified failure through the
existing reviewed_failure adapter. A host-side assistant/operator review under
user delegation authorizes training only, not human deployment approval. The
existing proposer receives the prior Skill, training observations and the
redacted symptom: repair terminated with a future intention and no source edit,
while independent acceptance still failed. It produces one immutable new Skill;
the candidate cannot alter the runtime, tools, target source or verifier.

Added a separate end-to-end reliability summary instead of reinterpreting the
efficiency gate. Every planned trial stays in the denominator. Accepted repairs,
verified failed repairs, measured unfinished runs and invalid measurements are
counted separately. Infrastructure/invalid-usage outcomes block qualification;
unfinished runs are not reported as proven wrong patches. A strict aggregate
acceptance improvement also requires a minimum candidate acceptance count and
no per-task regression. Missing/duplicate trials or ties cannot qualify.

The new frozen pilot uses three repetitions per arm on three training tasks.
At least 8/9 candidate acceptance, strict improvement and no per-task regression
are prerequisites to opening two hash-frozen held-out cases. Candidate generation
finishes before any held-out text is read, and no subsequent candidate revision
is permitted. Both arms share one unskilled investigation per held-out case.
The native promotion and human activation gates remain unchanged; a small pilot
signal does not imply statistical proof or automatic rollout.

Validation before the full experiment: 1678 tests passed, 54 skipped, six existing
warnings. Scoped Ruff and whitespace checks passed. Per-run outcomes and budget
evidence are retained under artifacts/issue-skill-reliability-pilot-20260916.

Observed outcome: training accepted 8/9 control versus 9/9 candidate, meeting the
exploratory training threshold. Held-out evaluation accepted 4/4 in both arms,
so strict held-out acceptance improvement did not qualify. Candidate calls were
50 versus 53 and estimated cost was about 6.1% lower on those held-out runs, but
these small secondary observations do not justify changing the primary goal or
activating the strategy. All 26 repair receipts confirmed the expected Skill
exposure. This closes a real failure-to-candidate-to-heldout-evaluation loop
while explicitly retaining non-promotion when improvement is unproven.

## IM-013: Pre-Commit Evidence Integrity Review

The paired adapter now retains known paid usage separately from scoreable cost:
invalid or incomplete measurements still block qualification, but their known
charges remain in both summaries. Efficiency checks reject boolean/float
repetition identifiers and accept an exact 10% call reduction without subtraction
roundoff. These accounting fixes do not change the recorded pilot conclusions.

Training batches reject excluded Issue URLs before opening snapshots. Summaries
are atomically persisted with a row for every planned task, including not-started
tasks. Model setup or implementation drift after a paid phase preserves partial
accounting before aborting; replay remains forbidden. Fault-injection tests cover
client creation, gateway drift, source drift and progress callback failure.

No candidate Skill is installed by default, no strategy is activated, and no
native Harness budget or recovery behavior is changed. Experimental candidates
remain isolated. The evaluated holdouts are now exposed evaluation data and must
not be reused as fresh sealed tests for subsequent adaptive search.

Final regression after review fixes: 1,688 passed, 54 skipped, six existing
datetime deprecation warnings. Scoped Ruff and git whitespace checks passed.

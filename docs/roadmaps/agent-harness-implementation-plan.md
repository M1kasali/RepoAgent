# RepoAgent Harness Implementation Plan

> Status: active
> Started: 2026-08-24
> Target: implement a complete production-oriented Agent Harness in RepoAgent while preserving RepoAgent's identity, interfaces, history, and evidence
> Technical record: [`docs/architecture/implementation-ledger.md`](../architecture/implementation-ledger.md)

## 1. Purpose

This document answers four questions:

1. What capability is still missing?
2. In what order should it be implemented?
3. What observable result proves that it is complete?
4. Which later modules depend on it?

It is a delivery plan, not an implementation description. Detailed design and completed behavior belong in the implementation ledger.

## 2. Product Boundary

RepoAgent remains a coding-agent application. Its Harness should be reusable, but coding is the primary product surface and evaluation workload.

The target contains three layers:

```text
Coding Agent Application
  CLI / TUI / repository understanding / Git / tests / patch verification

Agent Harness
  Spine / scheduler / providers / tools / context / memory / tracing / evaluation

Controlled Operations
  sandbox / permissions / channels / cron / plugins / subagents / evolver
```

Names from earlier prototypes must not become new public RepoAgent identifiers.

## 3. Implementation Rules

### 3.1 Deep-module rule

Each major capability must be a deep module: callers learn a small interface while scheduling, persistence, retries, evidence, and failure handling remain inside its implementation.

Do not create a seam until at least two adapters or a real testing need proves that behavior varies.

### 3.2 Migration strategy

The default implementation strategy is **reuse, adapt, and verify** rather than rewriting mature modules for cosmetic originality.

- Reuse a capability together with its focused tests whenever RepoAgent needs the same behavior and interface.
- Preserve module depth and invariants before changing package names or assembly.
- Adapt product identity, configuration paths, state paths, imports, and RepoAgent compatibility at explicit seams.
- Rewrite only when the existing interface conflicts with RepoAgent's product boundary, security model, or required compatibility.
- Do not perform a one-shot repository overlay; migrate one dependency-closed module slice per commit.
- A same-named capability is not migration evidence. Before marking a migrated
  slice complete, update the local, ignored parity audit with source/tests,
  behavioral differences, and an `ALIGN`, `ADAPT`, or `DEFER` disposition.
- `ADAPT` requires a documented incompatibility or measured improvement plus
  focused failure-path tests. Without that evidence, preserve the upstream
  implementation and tests.

### 3.3 Compatibility rule

- `repoagent` is the stable internal Python package and CLI command.
- A future product name may add a CLI alias without renaming internal imports.
- Existing `.pico/` state and `PICO_*` configuration remain readable during migration.
- New persistent schemas use neutral identifiers such as `turn_id`, `run_id`, and `event_version`.

### 3.4 Evidence rule

No TODO is complete merely because code exists. Completion requires:

- interface and invariants documented in the implementation ledger;
- focused unit/contract tests;
- an end-to-end or fault-path test when persistence or side effects are involved;
- trace/report evidence for user-visible runtime behavior;
- exact commands recorded for reproducibility.

## 4. Status Vocabulary

| Status | Meaning |
| --- | --- |
| `[x]` | Implemented, documented, and verified at the stated gate |
| `[ ]` | Not complete |
| `PARTIAL` | Useful implementation exists but misses target invariants or evidence |
| `DEFERRED` | Intentionally scheduled after its dependencies |
| `BLOCKED` | Cannot proceed until the named external condition changes |

## 5. Capability Matrix

| Capability | Current RepoAgent | Target | Phase |
| --- | --- | --- | --- |
| Agent loop | PARTIAL: synchronous `ask()` loop | Turn-based runner with terminal outcomes | P1 |
| Session state | PARTIAL: JSON sessions | versioned session manager, export, atomic lifecycle | P1 |
| Scheduling | Missing | per-session FIFO, cross-session concurrency, quotas, cancellation | P1 |
| Provider layer | PARTIAL: four clients | typed request/result, streaming, fallback, routing | P2 |
| Call efficiency | PARTIAL: prompt metadata | actual usage ledger, pricing, cache accounting | P2 |
| Tool execution | PARTIAL: registry and executor | one typed gateway, audit, timeout, bounded parallel reads | P3 |
| MCP | Missing | discovery, schema projection, execution, trust policy | P3 |
| Sandbox | Missing | direct and isolated adapters, fail-closed policy | P3 |
| Context engine | PARTIAL: bounded prompt builder | segment assembly, token budgets, curator, compaction | P4 |
| Memory | PARTIAL: working and durable Markdown | backend contract, consolidation, provenance, lifecycle | P4 |
| Skills | Missing | discovery, activation, lazy loading, references, local pool | P4 |
| Tracing | PARTIAL: JSONL events | correlation context, semantic events, usage, query/export | P5 |
| Evaluation | PARTIAL: scripted metrics | reproducible campaign runner, paired trials, scorecards | P6 |
| Subagents | Complete | isolated manager, budgets, messaging, roles, evidence | P7 |
| Model routing | Complete | deterministic profiles, fallback chain, explainable selection | P7 |
| Plugin system | Complete | manifest, discovery, external trust, lifecycle, Gateway-only tools | P7 |
| CLI/TUI/Gateway | Complete | separated assembly, unified commands, TUI transport, single-instance gateway | P8 |
| Channels/Cron | Complete | intake, delivery, media, deduplication, claims, scheduled execution | P8 |
| Evolver | Missing | isolated candidates, sealed gates, activation, rollback | P9 |
| Release engineering | Complete | clean-head evidence bundle, CI gates, migration docs | P10 |

## 6. Dependency Order

```text
P0 Baseline
  -> P1 Runtime Spine
      -> P2 Providers and Cost
      -> P3 Tool Gateway, MCP, Sandbox
          -> P4 Context, Memory, Skills
              -> P5 Tracing and Evidence
                  -> P6 Evaluation
                      -> P7 Subagents, Routing, Plugins
                          -> P8 Product Surfaces
                              -> P9 Evolver
                                  -> P10 Release Gate
```

Tracing hooks are introduced from P1 onward, but the complete tracing module is delivered in P5. Evaluation tests are added in every phase, while the reusable campaign engine is delivered in P6.

## 7. Phase TODO List

### P0 - Baseline and Ownership

Goal: establish a clean independent project identity and a green baseline.

- [x] `P0-01` Rename the internal package from `pico` to `repoagent`.
- [x] `P0-02` Use `REPOAGENT_*` and `.repoagent/` for new configuration and state.
- [x] `P0-03` Preserve read compatibility for `PICO_*` and existing `.pico/` workspaces.
- [x] `P0-04` Verify the real CLI/model/session/trace/report path.
- [x] `P0-05` Establish this implementation plan and technical ledger.
- [x] `P0-06` Establish dependency-closed, module-by-module implementation boundaries.
- [ ] `P0-07` Add CI for Ruff, full pytest, CLI smoke, script smoke, and package build.
- [ ] `P0-08` Freeze a baseline evidence manifest bound to the exact clean commit.

Gate:

```bash
uv run ruff check .
uv run pytest -q
uv run repoagent --help
uv run python -m repoagent --help
```

### P1 - Runtime Spine

Goal: replace direct synchronous orchestration with a durable Turn lifecycle.

- [x] `P1-01` Define `TurnId`, `SessionId`, `RequestId`, `TurnState`, and `TurnOutcome`.
- [x] `P1-02` Define the legal state-transition table and reject illegal transitions.
- [x] `P1-03` Define versioned runtime events with one correlation envelope.
- [x] `P1-04` Implement a `TurnRunner` that always returns a persisted terminal outcome.
- [x] `P1-05` Route the existing `RepoAgent.ask()` facade through `TurnRunner`.
- [x] `P1-06` Implement a per-session FIFO scheduler.
- [x] `P1-07` Allow bounded cross-session concurrency.
- [x] `P1-08` Separate foreground and background capacity.
- [x] `P1-09` Add cooperative cancellation before model, during model, and during tool execution.
- [x] `P1-10` Add teardown barriers so shutdown leaves no accepted Turn unaccounted for.
- [x] `P1-11` Make session and Turn persistence atomic and versioned.
- [x] `P1-12` Add deterministic scheduler, cancellation, crash, and duplicate-delivery tests.

Gate:

- 10,000 deterministic accepted requests produce zero missing and zero duplicate terminal outcomes.
- Same-session requests complete in accepted order.
- Different sessions demonstrate bounded parallel execution.
- Every accepted Turn has exactly one terminal event and one final outcome.
- `RepoAgent.ask()` remains backward compatible for current callers.

### P2 - Provider Runtime and Call Efficiency

Goal: make provider differences explicit behind a typed model interface.

- [x] `P2-01` Define typed `ModelRequest`, `ModelEvent`, `ModelResult`, `Usage`, and provider errors.
- [x] `P2-02` Implement adapters for Fake, Ollama, OpenAI-compatible, and Anthropic-compatible providers.
- [x] `P2-03` Support streaming text and native tool calls without leaking provider event types.
- [x] `P2-04` Add timeout and cancellation propagation.
- [x] `P2-05` Implement provider fallback with explicit failure classification.
- [x] `P2-06` Add model profiles and per-model configuration validation.
- [x] `P2-07` Record actual, estimated, missing, and mixed usage separately.
- [x] `P2-08` Implement a pricing and call-efficiency ledger.
- [x] `P2-09` Account for cache reads/writes and compaction calls.
- [x] `P2-10` Add deterministic replay tests and opt-in live-provider tests.

Gate:

- The agent loop imports no provider-specific response type.
- Every provider call has request, result/error, usage source, latency, and correlation evidence.
- Provider errors cannot leave a Turn in a running state.

### P3 - Tool Gateway, MCP, Sandbox, and Security

Goal: make one execution seam responsible for validation, authorization, isolation, and audit.

- [x] `P3-01` Define typed `ToolDefinition`, `ToolRequest`, `ToolEffect`, and `ToolResult`.
- [x] `P3-02` Generate prompt/native schemas and argument validation from one definition.
- [x] `P3-03` Route all tools, delegation, and internal calls through one `ToolGateway`.
- [x] `P3-04` Add effect-aware approval and capability tokens.
- [x] `P3-05` Implement timeout, cancellation, output limits, and structured failures.
- [x] `P3-06` Add read-only bounded parallel execution with deterministic result order.
- [x] `P3-07` Keep mutations serial unless an explicit conflict policy permits them.
- [x] `P3-08` Implement MCP discovery, registration, execution, and schema validation.
- [x] `P3-09` Implement direct and isolated sandbox adapters.
- [x] `P3-10` Fail closed when a task requires isolation but no sandbox is available.
- [x] `P3-11` Add filesystem traversal, symlink, command injection, secret, SSRF, and network-policy tests.
- [x] `P3-12` Add Git/worktree tools as the coding application extension.

Gate:

- Production code contains no mutation or shell path outside `ToolGateway`.
- Cancellation and timeout converge without orphan processes.
- An eight-read microbenchmark demonstrates concurrency without reordered results.
- Security failures produce structured denial evidence, not raw exceptions.

### P4 - Context, Memory, and Skills

Goal: control long-horizon information without silent semantic loss.

- [x] `P4-01` Define context segments and deterministic assembly order.
- [x] `P4-02` Replace character budgets with provider-aware token budgets.
- [x] `P4-03` Reserve output tokens before sending a request.
- [x] `P4-04` Add history trimming, summarization, and compaction with provenance.
- [x] `P4-05` Define a memory backend contract and in-memory fake.
- [x] `P4-06` Migrate working, episodic, file, and durable memory behind the contract.
- [x] `P4-07` Add freshness, supersession, conflict, confidence, and source metadata.
- [x] `P4-08` Add consolidation without exposing secrets or transient task state.
- [x] `P4-09` Define Skill manifests, references, discovery, activation, and lazy loading.
- [x] `P4-10` Add a local Skill pool and change watcher.
- [x] `P4-11` Add paired context and memory tests with graders isolated from model clients.

Gate:

- Prompt input never exceeds the declared budget.
- Current user intent and mandatory policy segments survive reduction.
- Memory clients do not receive expected answers or hidden filenames.
- Stale memory cannot silently override fresher workspace evidence.

### P5 - Tracing, Evidence, and Observability

Goal: correlate the complete path from accepted request to terminal outcome.

- [x] `P5-01` Define semantic event names and required attributes.
- [x] `P5-02` Propagate one trace context through scheduler, provider, tool, memory, and delivery stages.
- [x] `P5-03` Implement append, query, export, and retention interfaces.
- [x] `P5-04` Redact configured secrets at the write seam.
- [x] `P5-05` Correlate token/cost usage with Turn and provider call IDs.
- [x] `P5-06` Produce a self-contained evidence bundle with checksums.
- [x] `P5-07` Measure tracing latency and storage overhead.
- [x] `P5-08` Add a local trace inspection command; defer a viewer UI until needed.

Gate:

- Every accepted Turn is reconstructable from its evidence bundle.
- Missing terminal evidence is a test failure.
- Raw temporary paths are not published as evidence references.

### P6 - Evaluation Platform

Goal: turn runtime claims into reproducible experiments rather than scripted percentages.

- [x] `P6-01` Define one versioned evaluation-result schema.
- [x] `P6-02` Record commit, dirty state, environment, benchmark digest, model, and design.
- [x] `P6-03` Implement isolated trial workspaces and raw-row persistence.
- [x] `P6-04` Implement deterministic runtime-contract campaigns.
- [x] `P6-05` Implement paired context, memory, cost, and recovery campaigns.
- [x] `P6-06` Add Wilson intervals, paired win/tie/loss, and paired bootstrap/McNemar where applicable.
- [x] `P6-07` Add fault injection at model, tool, persistence, and cancellation boundaries.
- [x] `P6-08` Add an adapter for at least one public coding benchmark.
- [x] `P6-09` Add red-team prompt injection, tool abuse, data exfiltration, and policy-bypass suites.
- [x] `P6-10` Add baseline comparison and release evidence bundling.

Gate:

- Aggregate claims trace back to raw paired rows and evidence digests.
- Synthetic, scripted, and live-provider results cannot be confused in reports.
- Effective unique-task N and total run N are reported separately.

### P7 - Subagents, Routing, and Plugins

Goal: support bounded specialization without hiding responsibility or cost.

- [x] `P7-01` Define subagent request, budget, workspace, message, and outcome contracts.
- [x] `P7-02` Isolate subagent workspace and state.
- [x] `P7-03` Enforce parent-child cancellation and budget propagation.
- [x] `P7-04` Record subagent evidence under the parent Turn.
- [x] `P7-05` Add deterministic routing profiles and fallback chains.
- [x] `P7-06` Make routing decisions explainable in trace evidence.
- [x] `P7-07` Define plugin manifests, discovery, trust states, and lifecycle.
- [x] `P7-08` Prevent plugins from bypassing ToolGateway or secret policy.
- [x] `P7-09` Add coding roles for implementer, reviewer, and red-team verifier.

Gate:

- A parent outcome accounts for every child outcome and cost.
- Plugins cannot obtain capabilities not granted by runtime policy.
- Multi-agent evaluation must beat or complement a single-agent baseline on a defined workload.

### P8 - Product Surfaces, Channels, and Proactive Work

Goal: expose the same runtime semantics through every supported entry point.

- [x] `P8-01` Split CLI parsing from runtime assembly.
- [x] `P8-02` Add doctor, provider, session, sandbox, trace, eval, and skill commands.
- [x] `P8-03` Add a TUI transport with send, subscribe, confirm, and cancel operations.
- [x] `P8-04` Add a local gateway with single-instance ownership and health checks.
- [x] `P8-05` Define channel intake and delivery contracts.
- [x] `P8-06` Implement at least one real channel adapter before generalizing the registry.
- [x] `P8-07` Add media normalization and optional transcription.
- [x] `P8-08` Add cron claims, deduplication, reload, delivery, and outcome persistence.
- [x] `P8-09` Ensure CLI, TUI, gateway, channel, and cron requests all enter the same scheduler.

Gate:

- Entry points produce the same Turn wire shape and terminal semantics.
- Duplicate channel or cron delivery does not duplicate accepted work.
- TUI cancellation reaches provider and tool execution.

### P9 - Controlled Evolver

Goal: improve prompts, Skills, and tool policy only through isolated, evidence-gated candidates.

- [x] `P9-01` Define candidate manifest, allowed mutation surface, budget, and provenance.
- [x] `P9-02` Build candidate generation from failure evidence.
- [x] `P9-03` Isolate candidates in Git trees/worktrees.
- [x] `P9-04` Separate training tasks from sealed evaluation tasks.
- [x] `P9-05` Add deterministic gates before expensive live trials.
- [x] `P9-06` Add paired scoring, minimum sample thresholds, and termination rules.
- [x] `P9-07` Maintain an append-only candidate and activation ledger.
- [x] `P9-08` Require human confirmation for activation.
- [x] `P9-09` Support activation, rollback, and production routing queries.
- [x] `P9-10` Prevent candidates from reading or modifying sealed graders.

Gate:

- No candidate auto-activates below the declared statistical threshold.
- Every production strategy resolves to an approved candidate and evidence record.
- Rollback restores the previous active strategy without rewriting history.

### P10 - Release Hardening

Goal: publish a reproducible, installable, interview-ready project.

- [x] `P10-01` Add clean-head release workflow and package build.
- [x] `P10-02` Track a dependency lock and supported Python/OS matrix.
- [x] `P10-03` Add migration documentation for state, config, and schemas.
- [x] `P10-04` Add security model, threat model, and responsible disclosure policy.
- [x] `P10-05` Publish self-contained evaluation bundles bound to release tags. (`v0.1.1` workflow artifact independently reverified.)
- [x] `P10-06` Add a concise architecture document and end-to-end demo.
- [x] `P10-07` Generate resume metrics only from release evidence.

Gate:

- A clean clone can install, run offline smoke tests, and reproduce the release contract suite.
- Public claims identify workload, denominator, model, code commit, and limitations.

### P11 - Public Coding Benchmark Evidence

Goal: measure the complete coding runtime on a frozen public workload instead of
inferring coding quality from scripted runtime contracts.

- [x] `P11-01` Define an Aider Polyglot adapter that separates model-visible instructions and solution files from tests and reference examples.
- [x] `P11-02` Add deterministic, language-balanced canary selection and a non-executing inspection CLI bound to the dataset digest and commit.
- [x] `P11-03` Add a benchmark container backend and refuse Polyglot execution without verified isolation.
- [x] `P11-04` Execute one scripted task through the public RepoAgent runtime, capture its patch, and grade it inside the isolated benchmark workspace.
- [x] `P11-05` Persist per-attempt tests, patch, trace, usage, cost, latency, failure category, and checksummed evidence.
- [ ] `P11-06` Run a credential-free fixture campaign in CI and a 24-task six-language live canary outside PR CI. (CI fixture and offline six-language image acceptance implemented; live canary pending.)
- [ ] `P11-07` Add paired baseline-versus-Harness execution with identical model, task, attempt, and decoding configuration. (Strict identity, comparison, denominator, and statistics contract implemented; baseline execution adapter and live pair pending.)
- [ ] `P11-08` Run the frozen 225-task release campaign only after the canary safety, completion, and budget gates pass.

Gate:

- Untrusted generated code never executes on the direct host adapter.
- Runner input contains no test content, example solution, or grader command.
- Every public result identifies the RepoAgent commit, Polyglot commit/digest, model configuration, task denominator, and all errors or skips.
- A Harness improvement claim requires paired quality non-inferiority; cost or latency improvement alone is insufficient.

## 8. Per-Feature Workflow

For each TODO:

1. Mark exactly one item as in progress in the current working plan.
2. Identify the dependency-closed module slice and its tests.
3. Preserve the existing behavior and establish a focused test baseline.
4. Adapt RepoAgent imports, identity, configuration, persistence, and compatibility at documented boundaries.
5. Add RepoAgent-specific contract and integration tests.
6. Run focused tests, full tests, Ruff, and relevant CLI/evaluation smoke tests.
7. Record commands and results in the ledger.
8. Mark the TODO complete only after code, documentation, and evidence are in the same commit.

## 9. Immediate Next Slice

The completed implementation slice is `P1-01` through `P1-05`:

- define the Turn domain model;
- define legal state transitions and events;
- introduce `TurnRunner` behind a small interface;
- preserve `RepoAgent.ask()` as the compatibility facade;
- document the design and verify terminal outcomes.

Provider runtime phase `P2`, tool execution/security phase `P3`, context/memory/Skill phase `P4`, tracing/evidence phase `P5`, evaluation-platform phase `P6`, bounded-specialization phase `P7`, product-surface phase `P8`, controlled-evolution phase `P9`, and release-hardening phase `P10` are complete. Release `v0.1.1` is bound to commit `49e016a4c27361ff5f7613edd723620d730da837`; its workflow artifact contains the installable distributions, 12-task contract results, 12 self-contained evidence directories, a checksummed release manifest, and release-only resume claims. The earlier `v0.1.0` attempt remains immutable evidence of the clean-checkout documentation-test failure that was corrected before `v0.1.1`.

`P11-05` is complete: the campaign runner executes a deterministic task-by-repetition
matrix and persists each patch, grade, Agent bundle, provider preflight, raw row,
latency, usage, call cost and failure category under one aggregate result. Pre-run
worst-case cost admission happens before output creation or Agent construction;
actual call count, cost completeness, source integrity, errors and skipped attempts
remain explicit gates and rows. Both Agent shell execution and hidden-test grading
are forced through the configured Docker runtime in the live entry points.

The active slice is `P11-06`: CI now runs and uploads a credential-free two-task,
two-repetition fixture campaign, then validates the aggregate result and per-row
evidence. The remaining work is the bounded 24-task six-language live canary outside
PR CI. A clean-source DeepSeek V4 Flash replay on `python/affine-cipher` now passed
provider preflight, normal Turn convergence, Docker shell execution, complete cost
accounting and all 16 hidden tests; a preceding independent repetition converged
but passed only 15/16. This completes the single-task live acceptance gate without
claiming general quality. Paid expansion to the frozen 24-task, six-language canary
remains required before `P11-06` can close.

The six-language grader image is now defined in-repository and its known-good,
network-disabled acceptance passed 6/6 languages against the frozen benchmark.
This repaired C++ exercise-directory preservation, Java full-test enablement,
Go/Gradle execution under a `noexec` temporary filesystem, and Windows-backed
staging cleanup. Formal campaigns require an immutable image digest. These are
environment gates only; they do not replace the pending 24-task live model
canary.

The first 24-task live attempt on commit `d1620d6` was stopped during task four
and retained only as diagnostic evidence. Two completed rows failed before
grading because the in-turn structured Provider transcript exceeded the 8,000
token input budget outside the existing overflow recovery path; failed rows also
omitted completed-call cost from their aggregates. Structure-preserving
pre-admission transcript reduction and failed-turn report persistence now cover
both defects. The live canary must restart from a new clean commit and output
directory; the interrupted attempt cannot satisfy `P11-06`.

A clean post-fix `go/alphametics` replay then isolated Docker Desktop workspace
placement as a second environmental variable: WSL-resident Agent workspaces could
not be mounted although the Windows-staged grader passed. With a separate
Docker-visible Agent staging root, the task converged, passed hidden grading and
all campaign gates in 11 calls at USD 0.0113416128. The formal 24-task restart
must use that staging contract and retain a complete denominator.

The first staging-correct 24-task restart was stopped after 15 complete rows when
one attempt used 15 Provider calls against its declared limit of 14. This exposed
that the call budget was previously a post-run gate rather than an execution
limit. The campaign now binds the budget to the Agent loop and suppresses any
extra exhaustion-synthesis call once the limit is reached. That partial result is
diagnostic only; `P11-06` still requires a fresh complete 24-row run.

The next 24-row artifact is also diagnostic: an operator stop coincided with
seven DeepSeek connection failures, leaving usage pricing incomplete and failing
the actual-cost gate. Campaign orchestration now aborts further paid attempts on
the first infrastructure-error row while retaining skipped denominator rows.
Hidden-test failures remain non-fatal to preserve unbiased quality measurement.

The first fail-fast restart executed 8/24 rows before an 8,000-token runtime
input ceiling rejected a retained signed-thinking exchange; its campaign cost
envelope had already reserved 12,000 input tokens per call. The next restart must
use 12,000 for both runtime admission and worst-case cost admission. Campaign
gates now distinguish a complete planned-row denominator from a complete executed
denominator, so any error-triggered skipped rows fail formal acceptance instead
of leaving all gates green.

The first 12,000-token restart verified that gate by stopping after 2/24 executed
rows when DeepSeek emitted a repairable non-object Tool-argument payload. The
partial run is rejected and retained as protocol evidence. RepoAgent now preserves
that payload behind `_raw_arguments` and lets Tool schema validation return the
matched error to the model, matching pico-harness's failure boundary without
weakening execution policy. A focused single-task live replay is required before
starting the next complete canary.

The first focused replay converged at the Agent layer in 10 calls with complete
usage, but its grader staging directory was absent, so no hidden tests ran. It
also showed that a last-row infrastructure error could evade the execution-count
gate. Container staging is now prepared before paid campaign execution, and the
separate `infrastructure_error_free` gate rejects every error row even when the
executed denominator is complete. The failed grader run remains diagnostic and
must be replaced by a clean single-task replay.

That replacement replay passed `go/alphametics` end to end on clean commit
`2a1e197`: 11 complete DeepSeek calls, normal Turn convergence, pinned hidden
grader exit zero, USD 0.0073998512 actual estimated cost, stable source, and all
campaign gates green. The stochastic response did not repeat the non-object
payload, while offline regressions directly cover that branch. The next step for
`P11-06` is a fresh 24-task run with the same 12,000-token runtime/cost envelope
and prepared Windows-visible staging roots.

The `P11-07` analysis boundary is now implemented independently of any named
external harness. Polyglot attempts persist frozen runtime, task and grader
pairing identities, and `compare-polyglot-paired` refuses mismatched benchmark,
model/decoding/limit configuration, task matrix, or grader input before producing
paired W/T/L, McNemar and efficiency deltas. This does not yet complete `P11-07`:
the external baseline execution adapter and a real same-configuration paired run
remain pending. Prior one-off comparisons with different protocols remain
diagnostic only.

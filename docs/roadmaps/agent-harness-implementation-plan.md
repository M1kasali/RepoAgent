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
| Subagents | PARTIAL: delegate tool | isolated manager, budgets, messaging, evidence | P7 |
| Model routing | Missing | profiles, fallback chain, explainable selection | P7 |
| Plugin system | Missing | manifest, discovery, trust, lifecycle | P7 |
| CLI/TUI/Gateway | PARTIAL: CLI only | consistent runtime assembly and cancellation across surfaces | P8 |
| Channels/Cron | Missing | intake, delivery, deduplication, scheduled execution | P8 |
| Evolver | Missing | isolated candidates, sealed gates, activation, rollback | P9 |
| Release engineering | PARTIAL | clean-head evidence bundle, CI gates, migration docs | P10 |

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
- [ ] `P1-09` Add cooperative cancellation before model, during model, and during tool execution.
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
- [ ] `P2-10` Add deterministic replay tests and opt-in live-provider tests.

Gate:

- The agent loop imports no provider-specific response type.
- Every provider call has request, result/error, usage source, latency, and correlation evidence.
- Provider errors cannot leave a Turn in a running state.

### P3 - Tool Gateway, MCP, Sandbox, and Security

Goal: make one execution seam responsible for validation, authorization, isolation, and audit.

- [ ] `P3-01` Define typed `ToolDefinition`, `ToolRequest`, `ToolEffect`, and `ToolResult`.
- [ ] `P3-02` Generate prompt/native schemas and argument validation from one definition.
- [ ] `P3-03` Route all tools, delegation, and internal calls through one `ToolGateway`.
- [ ] `P3-04` Add effect-aware approval and capability tokens.
- [ ] `P3-05` Implement timeout, cancellation, output limits, and structured failures.
- [ ] `P3-06` Add read-only bounded parallel execution with deterministic result order.
- [ ] `P3-07` Keep mutations serial unless an explicit conflict policy permits them.
- [ ] `P3-08` Implement MCP discovery, registration, execution, and schema validation.
- [ ] `P3-09` Implement direct and isolated sandbox adapters.
- [ ] `P3-10` Fail closed when a task requires isolation but no sandbox is available.
- [ ] `P3-11` Add filesystem traversal, symlink, command injection, secret, SSRF, and network-policy tests.
- [ ] `P3-12` Add Git/worktree tools as the coding application extension.

Gate:

- Production code contains no mutation or shell path outside `ToolGateway`.
- Cancellation and timeout converge without orphan processes.
- An eight-read microbenchmark demonstrates concurrency without reordered results.
- Security failures produce structured denial evidence, not raw exceptions.

### P4 - Context, Memory, and Skills

Goal: control long-horizon information without silent semantic loss.

- [ ] `P4-01` Define context segments and deterministic assembly order.
- [ ] `P4-02` Replace character budgets with provider-aware token budgets.
- [ ] `P4-03` Reserve output tokens before sending a request.
- [ ] `P4-04` Add history trimming, summarization, and compaction with provenance.
- [ ] `P4-05` Define a memory backend contract and in-memory fake.
- [ ] `P4-06` Migrate working, episodic, file, and durable memory behind the contract.
- [ ] `P4-07` Add freshness, supersession, conflict, confidence, and source metadata.
- [ ] `P4-08` Add consolidation without exposing secrets or transient task state.
- [ ] `P4-09` Define Skill manifests, references, discovery, activation, and lazy loading.
- [ ] `P4-10` Add a local Skill pool and change watcher.
- [ ] `P4-11` Add paired context and memory tests with graders isolated from model clients.

Gate:

- Prompt input never exceeds the declared budget.
- Current user intent and mandatory policy segments survive reduction.
- Memory clients do not receive expected answers or hidden filenames.
- Stale memory cannot silently override fresher workspace evidence.

### P5 - Tracing, Evidence, and Observability

Goal: correlate the complete path from accepted request to terminal outcome.

- [ ] `P5-01` Define semantic event names and required attributes.
- [ ] `P5-02` Propagate one trace context through scheduler, provider, tool, memory, and delivery stages.
- [ ] `P5-03` Implement append, query, export, and retention interfaces.
- [ ] `P5-04` Redact configured secrets at the write seam.
- [ ] `P5-05` Correlate token/cost usage with Turn and provider call IDs.
- [ ] `P5-06` Produce a self-contained evidence bundle with checksums.
- [ ] `P5-07` Measure tracing latency and storage overhead.
- [ ] `P5-08` Add a local trace inspection command; defer a viewer UI until needed.

Gate:

- Every accepted Turn is reconstructable from its evidence bundle.
- Missing terminal evidence is a test failure.
- Raw temporary paths are not published as evidence references.

### P6 - Evaluation Platform

Goal: turn runtime claims into reproducible experiments rather than scripted percentages.

- [ ] `P6-01` Define one versioned evaluation-result schema.
- [ ] `P6-02` Record commit, dirty state, environment, benchmark digest, model, and design.
- [ ] `P6-03` Implement isolated trial workspaces and raw-row persistence.
- [ ] `P6-04` Implement deterministic runtime-contract campaigns.
- [ ] `P6-05` Implement paired context, memory, cost, and recovery campaigns.
- [ ] `P6-06` Add Wilson intervals, paired win/tie/loss, and paired bootstrap/McNemar where applicable.
- [ ] `P6-07` Add fault injection at model, tool, persistence, and cancellation boundaries.
- [ ] `P6-08` Add an adapter for at least one public coding benchmark.
- [ ] `P6-09` Add red-team prompt injection, tool abuse, data exfiltration, and policy-bypass suites.
- [ ] `P6-10` Add baseline comparison and release evidence bundling.

Gate:

- Aggregate claims trace back to raw paired rows and evidence digests.
- Synthetic, scripted, and live-provider results cannot be confused in reports.
- Effective unique-task N and total run N are reported separately.

### P7 - Subagents, Routing, and Plugins

Goal: support bounded specialization without hiding responsibility or cost.

- [ ] `P7-01` Define subagent request, budget, workspace, message, and outcome contracts.
- [ ] `P7-02` Isolate subagent workspace and state.
- [ ] `P7-03` Enforce parent-child cancellation and budget propagation.
- [ ] `P7-04` Record subagent evidence under the parent Turn.
- [ ] `P7-05` Add deterministic routing profiles and fallback chains.
- [ ] `P7-06` Make routing decisions explainable in trace evidence.
- [ ] `P7-07` Define plugin manifests, discovery, trust states, and lifecycle.
- [ ] `P7-08` Prevent plugins from bypassing ToolGateway or secret policy.
- [ ] `P7-09` Add coding roles for implementer, reviewer, and red-team verifier.

Gate:

- A parent outcome accounts for every child outcome and cost.
- Plugins cannot obtain capabilities not granted by runtime policy.
- Multi-agent evaluation must beat or complement a single-agent baseline on a defined workload.

### P8 - Product Surfaces, Channels, and Proactive Work

Goal: expose the same runtime semantics through every supported entry point.

- [ ] `P8-01` Split CLI parsing from runtime assembly.
- [ ] `P8-02` Add doctor, provider, session, sandbox, trace, eval, and skill commands.
- [ ] `P8-03` Add a TUI transport with send, subscribe, confirm, and cancel operations.
- [ ] `P8-04` Add a local gateway with single-instance ownership and health checks.
- [ ] `P8-05` Define channel intake and delivery contracts.
- [ ] `P8-06` Implement at least one real channel adapter before generalizing the registry.
- [ ] `P8-07` Add media normalization and optional transcription.
- [ ] `P8-08` Add cron claims, deduplication, reload, delivery, and outcome persistence.
- [ ] `P8-09` Ensure CLI, TUI, gateway, channel, and cron requests all enter the same scheduler.

Gate:

- Entry points produce the same Turn wire shape and terminal semantics.
- Duplicate channel or cron delivery does not duplicate accepted work.
- TUI cancellation reaches provider and tool execution.

### P9 - Controlled Evolver

Goal: improve prompts, Skills, and tool policy only through isolated, evidence-gated candidates.

- [ ] `P9-01` Define candidate manifest, allowed mutation surface, budget, and provenance.
- [ ] `P9-02` Build candidate generation from failure evidence.
- [ ] `P9-03` Isolate candidates in Git trees/worktrees.
- [ ] `P9-04` Separate training tasks from sealed evaluation tasks.
- [ ] `P9-05` Add deterministic gates before expensive live trials.
- [ ] `P9-06` Add paired scoring, minimum sample thresholds, and termination rules.
- [ ] `P9-07` Maintain an append-only candidate and activation ledger.
- [ ] `P9-08` Require human confirmation for activation.
- [ ] `P9-09` Support activation, rollback, and production routing queries.
- [ ] `P9-10` Prevent candidates from reading or modifying sealed graders.

Gate:

- No candidate auto-activates below the declared statistical threshold.
- Every production strategy resolves to an approved candidate and evidence record.
- Rollback restores the previous active strategy without rewriting history.

### P10 - Release Hardening

Goal: publish a reproducible, installable, interview-ready project.

- [ ] `P10-01` Add clean-head release workflow and package build.
- [ ] `P10-02` Track a dependency lock and supported Python/OS matrix.
- [ ] `P10-03` Add migration documentation for state, config, and schemas.
- [ ] `P10-04` Add security model, threat model, and responsible disclosure policy.
- [ ] `P10-05` Publish self-contained evaluation bundles bound to release tags.
- [ ] `P10-06` Add a concise architecture document and end-to-end demo.
- [ ] `P10-07` Generate resume metrics only from release evidence.

Gate:

- A clean clone can install, run offline smoke tests, and reproduce the release contract suite.
- Public claims identify workload, denominator, model, code commit, and limitations.

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

The provider runtime slice `P2-01` through `P2-09` is complete. AgentLoop consumes normalized streaming events, scheduler cancellation closes active built-in HTTP responses, fallback is classification-driven and protected against partial-stream mixing, and validated model profiles keep credential values outside configuration evidence. `ModelUsageAggregate` now sums every completed call in a Turn, accounts failed fallback/provider attempts as missing, and preserves actual/estimated/missing/mixed counts in trace, report, and terminal Turn evidence. Explicit pricing snapshots and a per-attempt `calls.jsonl` ledger expose partial cost while withholding unit-success cost when evidence is incomplete. Cache pricing distinguishes fresh and total input-token semantics, and call-kind counts reserve a visible compaction bucket for the model compactor introduced in P4. Connection setup before a response handle exists remains timeout-bounded, and tool-process cancellation remains `P3-05`; therefore `P1-09` is still partial. The next slice is `P2-10`, deterministic replay and opt-in live-provider tests.

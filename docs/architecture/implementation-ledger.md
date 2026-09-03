# RepoAgent Implementation Ledger

> Status: living technical record
> Plan: [`docs/roadmaps/agent-harness-implementation-plan.md`](../roadmaps/agent-harness-implementation-plan.md)
> Baseline overview: [`agent-harness-v1-overview.md`](agent-harness-v1-overview.md)

## 1. Purpose

This ledger records how completed RepoAgent capabilities work. It is not a TODO list and must not describe planned behavior as if it already exists.

Every implemented plan item should answer:

- What module owns the behavior?
- What is its interface?
- Which invariants must always hold?
- What is persisted and in what order?
- How do cancellation, failure, and recovery behave?
- Which tests and evaluation artifacts prove the behavior?

## 2. Maintenance Contract

Update this document in the same commit as the implementation when any of the following changes:

- public or cross-module interface;
- state transition or ordering rule;
- persistent schema or state path;
- permission, sandbox, or secret behavior;
- provider/tool/context/memory semantics;
- trace or evaluation evidence;

Do not paste source code that will drift. Record interfaces, invariants, data flow, failure behavior, and links to the owning files and tests.

## 3. Entry Template

Copy this section for each completed capability.

```markdown
## TECH-NNN - Capability name

- Plan items: `P?-??`
- Status: implemented | partial | superseded
- Commit: `<sha>`
- Implemented: YYYY-MM-DD
- Owning module: `repoagent/...`
- Tests: `tests/...`
- Evidence: path or command

### Problem

### Interface

### Invariants

### Data Flow and Persistence Order

### Failure, Cancellation, and Recovery

### Security and Privacy

### Verification

### Tradeoffs and Follow-ups
```

## 4. Current Architecture Index

| Area | Current owning module | Status | Ledger entry |
| --- | --- | --- | --- |
| CLI assembly | `repoagent/cli.py` | implemented baseline | TECH-001 |
| Synchronous runtime | `repoagent/runtime.py` | partial, pre-Spine | TECH-002 |
| Agent loop helper | `repoagent/agent_loop.py` | partial, pre-Spine | TECH-002 |
| Task state | `repoagent/task_state.py` | implemented baseline | TECH-002 |
| Session persistence | `repoagent/session_store.py`, `repoagent/atomic_io.py` | atomic and versioned | TECH-010 |
| Run evidence | `repoagent/run_store.py`, `repoagent/atomic_io.py` | crash-recoverable Turn evidence | TECH-010 |
| Checkpoint and workspace recovery | `repoagent/checkpoint.py`, `repoagent/workspace_checkpoint.py` | semantic projection plus out-of-band file snapshots | TECH-003, TECH-072 |
| Tool execution lifecycle | `repoagent/tool_execution.py`, `repoagent/tool_gateway.py`, `repoagent/tools.py` | deadlines, cancellation, bounded output, and process-tree convergence implemented | TECH-021 |
| Tool batch scheduling | `repoagent/tool_gateway.py`, `repoagent/agent_loop.py` | bounded safe-read parallelism with deterministic result and evidence order | TECH-022 |
| Mutation conflict policy | `repoagent/tool_scheduling.py`, `repoagent/tool_gateway.py` | explicit serial policy and auditable scheduling decisions | TECH-023 |
| MCP runtime | `repoagent/mcp.py`, `repoagent/runtime.py` | namespaced discovery, schema validation, capability-scoped Gateway execution | TECH-024 |
| Sandbox adapters | `repoagent/sandbox.py`, `repoagent/tools.py` | explicit direct-host identity and injected isolated-backend contract | TECH-025 |
| Isolation enforcement | `repoagent/tool_gateway.py`, `repoagent/runtime.py` | definition- and task-level fail-closed isolation gate | TECH-026 |
| Agent shell sandbox | `repoagent/sandbox.py`, `repoagent/runtime_assembly.py` | direct-host declaration plus fail-closed Docker isolation backend | TECH-073 |
| Network and adversarial security | `repoagent/security.py`, `tests/test_security_adversarial.py` | secret redaction, network policy, and attack-path regressions | TECH-027 |
| Git/worktree extension | `repoagent/tools.py` | argv-only repository inspection and managed worktree lifecycle | TECH-028 |
| Context segments and reduction | `repoagent/context_manager.py` | immutable sourced segment manifest with token-aware reduction | TECH-029, TECH-030 |
| Token counting and budgets | `repoagent/tokenization.py`, `repoagent/context_manager.py` | provider counter contract with explicitly labeled estimated fallback | TECH-030 |
| Context-window admission | `repoagent/context_window.py`, `repoagent/runtime.py` | output reservation, effective input budget, and fail-closed preflight | TECH-031 |
| History compaction | `repoagent/compaction.py`, `repoagent/context_manager.py` | deterministic trimming, summaries, and per-source provenance | TECH-032 |
| Memory backend seam | `repoagent/memory_backend.py`, `repoagent/memory_contract.py`, `repoagent/agent_turn_runner.py` | lifecycle-managed recall/store with deterministic fake and conformance suite | TECH-033, TECH-034 |
| Layered memory | `repoagent/features/memory.py` | default backend with working, episodic, file, durable, freshness, confidence, conflict, and supersession state | TECH-005, TECH-034 |
| Memory consolidation | `repoagent/memory_consolidation.py` | deterministic explicit-intent extraction with secret/transient/noise rejection | TECH-035 |
| Local Skills | `repoagent/skills.py`, `repoagent/context_manager.py` | manifest discovery, activation, lazy body/reference loading, local pool, and watcher | TECH-036 |
| Paired evaluation | `repoagent/evaluation/paired.py` | task/repetition pairing with answer-isolated graders and raw rows | TECH-037 |
| Semantic tracing | `repoagent/tracing.py`, `repoagent/spine/` | correlated scheduler/runtime/provider/tool/memory/delivery stages | TECH-038 |
| Trace storage and evidence | `repoagent/run_store.py`, `repoagent/evidence.py` | redacted query/export/retention and checksummed bundles | TECH-039 |
| Trace operations | `repoagent/evaluation/tracing.py`, `repoagent/trace_inspection.py` | measured local overhead and read-only inspection CLI | TECH-040 |
| Evaluation result protocol | `repoagent/evaluation/schema.py`, `repoagent/evaluation/workspace.py` | versioned provenance, isolated trials, and raw rows | TECH-041 |
| Evaluation campaigns and statistics | `repoagent/evaluation/campaigns.py`, `repoagent/evaluation/statistics.py` | runtime/paired campaigns with intervals and paired tests | TECH-042 |
| Adversarial and public evaluation | `repoagent/evaluation/faults.py`, `repoagent/evaluation/red_team.py`, `repoagent/evaluation/swebench.py` | boundary faults, red-team grading, and SWE-bench adaptation | TECH-043 |
| Evaluation release operations | `repoagent/evaluation/release.py`, `repoagent/evaluation/cli.py` | baseline gates and self-contained release bundles | TECH-044 |
| Subagent runtime | `repoagent/subagents.py`, `repoagent/runtime.py` | isolated, budgeted, cancellable children with parent-owned evidence | TECH-045 |
| Model routing | `repoagent/routing.py`, `repoagent/agent_loop.py` | deterministic profiles, provider fallback, and trace explanations | TECH-046 |
| Plugin runtime | `repoagent/plugins.py`, `repoagent/tool_gateway.py` | declarative discovery, external trust, lifecycle, and Gateway-only execution | TECH-047 |
| Specialized roles | `repoagent/subagents.py`, `repoagent/evaluation/subagents.py` | implementer/reviewer/red-team profiles and paired complement gate | TECH-048 |
| Runtime assembly and command tree | `repoagent/runtime_assembly.py`, `repoagent/cli.py`, `repoagent/product_commands.py` | parser-independent assembly and structured operational commands | TECH-049 |
| Product runtime host and TUI | `repoagent/runtime_host.py`, `repoagent/tui.py` | Scheduler-backed ingress with send/subscribe/confirm/cancel | TECH-050 |
| Gateway, channels, and media | `repoagent/gateway.py`, `repoagent/channels.py` | single-instance ownership, deny-first intake, delivery, directory adapter, media normalization | TECH-051 |
| Scheduled work | `repoagent/cron.py` | persistent deduplication, atomic claims, reload, execution, and outcomes | TECH-052 |
| Provider contracts and adapters | `repoagent/providers/base.py`, `repoagent/providers/clients.py`, `repoagent/providers/fallback.py` | typed streaming, cancellation, and explicit fallback implemented | TECH-011 |
| Model profiles | `repoagent/providers/profiles.py`, `repoagent/cli.py` | validated built-in profiles and compatibility assembly implemented | TECH-012 |
| Usage accounting | `repoagent/providers/base.py`, `repoagent/agent_loop.py`, `repoagent/spine/runner.py` | multi-call totals and source completeness implemented | TECH-013 |
| Call efficiency | `repoagent/pricing.py`, `repoagent/call_efficiency.py`, `repoagent/run_store.py` | explicit price snapshots and per-attempt cost evidence implemented | TECH-014 |
| Cache and compaction accounting | `repoagent/providers/base.py`, `repoagent/pricing.py`, `repoagent/call_efficiency.py` | provider-aware cache cost and compaction call classification implemented | TECH-015 |
| Provider replay and live acceptance | `repoagent/call_replay.py`, `live_tests/test_live_providers.py` | deterministic offline verification and explicit live opt-in implemented | TECH-016 |
| Paid campaign preflight | `repoagent/evaluation/provider_probe.py`, `repoagent/evaluation/polyglot_campaign.py` | bounded native-tool probe bound to source, suite, runtime, sandbox, and budget identity | TECH-074 |
| Polyglot campaign orchestration | `repoagent/evaluation/polyglot_suite.py`, `scripts/run_polyglot_campaign.py` | budget-admitted task/repetition matrix with complete denominator and isolated execution | TECH-075 |
| Polyglot CI fixture | `scripts/run_polyglot_fixture_campaign.py`, `.github/workflows/ci.yml` | credential-free campaign, result validation, and uploaded evidence artifact | TECH-076 |
| Structured conversation replay | `repoagent/conversation.py`, `repoagent/agent_loop.py`, `repoagent/providers/clients.py` | budgeted cross-Turn Tool/Reasoning replay with prompt-only compatibility | TECH-077 |
| Responses reasoning recovery | `repoagent/providers/clients.py`, `repoagent/agent_loop.py` | normalized reasoning-only output, opaque replay, and bounded structured prefill | TECH-078 |
| Prompt-only overflow recovery | `repoagent/context_overflow.py`, `repoagent/context_manager.py`, `repoagent/agent_loop.py` | emergency history snapshot and token-budget reduction for legacy/Ollama prompts | TECH-079 |
| Live Polyglot acceptance | local ignored evidence under `artifacts/polyglot-live/` | source-bound DeepSeek/Docker single-task pass and retained failure | TECH-080 |
| Strict paired Polyglot comparison | `repoagent/evaluation/polyglot_pair.py`, `repoagent/evaluation/cli.py` | frozen runtime/task/grader identity, complete pair denominator, quality and efficiency deltas | TECH-081 |
| Six-language Polyglot image | `benchmarks/polyglot-image/`, `scripts/run_polyglot_image_smoke.py` | fixed toolchains, offline full-test semantics, immutable image gate, six-language known-good smoke | TECH-082 |
| In-turn transcript admission | `repoagent/context_overflow.py`, `repoagent/agent_loop.py`, `repoagent/agent_turn_runner.py` | structure-preserving replay reduction and failure-terminal cost evidence | TECH-083 |
| Tool Gateway contracts | `repoagent/tool_contracts.py` | immutable typed definition, request, effect, and result contracts implemented | TECH-017 |
| Tool definition projection | `repoagent/tools.py`, `repoagent/providers/tool_schema.py`, `repoagent/prompt_prefix.py` | one definition drives schemas, validation, effects, and prompt signatures | TECH-018 |
| Unified Tool Gateway routing | `repoagent/tool_gateway.py`, `repoagent/runtime.py`, `repoagent/agent_loop.py` | model, delegate, compatibility, and internal calls share typed execution and evidence | TECH-019 |
| Effect approval and capabilities | `repoagent/approval.py`, `repoagent/capabilities.py` | effect-aware decisions and enforced attenuable grants | TECH-020 |
| Evaluation | `repoagent/evaluation/` | partial | TECH-007 |
| Naming and compatibility | `repoagent/config.py`, `repoagent/paths.py` | implemented | TECH-001 |
| Runtime Spine | `repoagent/spine/`, `repoagent/agent_turn_runner.py` | implemented single-Turn lifecycle | TECH-008 |
| Turn scheduling | `repoagent/spine/scheduler.py`, `repoagent/spine/_barrier.py` | implemented; provider cancellation integrated, tool cancellation pending | TECH-009 |
| Crash recovery | `repoagent/run_store.py`, `repoagent/cli.py` | implemented for persisted Turns | TECH-010 |

## TECH-001 - RepoAgent Identity and Compatibility Layer

- Plan items: `P0-01`, `P0-02`, `P0-03`, `P0-04`
- Status: implemented
- Commit: `203e2ab`
- Implemented: 2026-08-24
- Owning modules: `repoagent/config.py`, `repoagent/paths.py`, `repoagent/cli.py`
- Tests: `tests/test_config.py`, `tests/test_paths.py`, `tests/test_public_api_contract.py`
- Evidence: full pytest/Ruff/CLI smoke and two real DeepSeek one-shot runs

### Problem

The project was named RepoAgent but its Python package, configuration prefix, state directory, prompt identity, and benchmark paths still used Pico identifiers. A future product brand would have required another cross-cutting rename.

### Interface

- Python import root: `repoagent`
- Stable CLI command: `repoagent`
- Module entry: `python -m repoagent`
- Preferred environment prefix: `REPOAGENT_*`
- Preferred workspace state root: `.repoagent/`
- Internal runtime class: `RepoAgent`

`provider_env()` accepts a preferred `REPOAGENT_*` key and automatically checks the corresponding legacy `PICO_*` key before generic provider aliases.

`workspace_state_root()` selects `.repoagent/` for a new workspace. If only `.pico/` already exists, it continues that workspace without splitting state across two roots. If both exist, `.repoagent/` wins.

### Invariants

- New workspaces never create `.pico/`.
- Existing `.pico/` workspaces remain readable and writable as one consistent state lineage.
- Preferred variables override legacy variables.
- Brand names do not enter persisted IDs or event field names.
- Local state roots are excluded from repository context and Git tracking.

### Data Flow and Persistence Order

1. CLI loads user configuration from `~/.config/repoagent/.env` without overriding shell variables.
2. Project `.env` values may override user-level configuration.
3. Preferred, legacy, and generic provider variables are resolved in that order.
4. Workspace state root is selected once during assembly.
5. Session, run, and durable memory stores use the selected root.

### Failure, Cancellation, and Recovery

Missing user configuration is non-fatal. Invalid provider names fail during CLI assembly. Legacy state is not automatically moved because a partial filesystem migration could split or corrupt a live session lineage; an explicit migration command remains a future option.

### Security and Privacy

Both preferred and legacy secret variable names are included in redaction configuration. Smoke verification listed configuration keys only and did not print values.

### Verification

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -B -m pytest -p no:cacheprovider -q
.venv/bin/ruff check .
.venv/bin/repoagent --help
.venv/bin/python -m repoagent --help
```

Observed baseline: 140 tests passed; Ruff passed; installed CLI and module CLI passed. A real DeepSeek request returned a terminal answer and wrote session, task state, trace, and report artifacts. A fresh temporary workspace wrote `.repoagent/` and did not create `.pico/`.

### Tradeoffs and Follow-ups

- `repoagent` remains an internal name even if the product receives a new brand.
- A future CLI alias can point to `repoagent.cli:main` without renaming imports.
- Explicit state migration and schema versioning belong to P1/P10.

## TECH-002 - Agent Loop Behind the Spine

- Plan items: predecessor to `P1-01` through `P1-05`
- Status: partially superseded by TECH-008
- Commit: present at baseline `203e2ab`
- Owning modules: `repoagent/runtime.py`, `repoagent/agent_loop.py`, `repoagent/task_state.py`
- Tests: `tests/test_repoagent.py`, `tests/test_agent_loop.py`, `tests/test_task_state.py`

### Problem

RepoAgent needs a complete model/tool loop that can execute a repository-grounded request and produce a terminal answer under a step budget.

### Interface

`AgentLoop.run()` still drives model calls, parsing, tool execution, state updates, and finalization. It is now invoked through `AgentTurnRunner`; application callers use `RepoAgent.ask()` or `await RepoAgent.ask_async()`.

### Invariants

- Tool steps cannot exceed `max_steps`.
- A successful final answer sets a terminal stop reason.
- Each run receives a unique run ID.
- Current callers can use `RepoAgent.ask()` without scheduler knowledge.

### Data Flow and Persistence Order

The current path is:

```text
CLI -> RepoAgent.ask/ask_async -> TurnRuntime -> AgentTurnRunner -> AgentLoop
                                  |                                  |
                                  +-> Turn evidence                  +-> TaskState/RunStore
```

### Failure, Cancellation, and Recovery

The loop has bounded retries and several stop reasons. Turn state transitions and terminal persistence are owned by TECH-008. Scheduler cancellation and interruption are still pending.

### Verification

The baseline is covered by deterministic fake-model tests and the real CLI smoke described in TECH-001.

### Tradeoffs and Follow-ups

Do not add concurrency directly to `RepoAgent.ask()`. First introduce the Turn model and single-Turn runner, then place scheduling above that interface.

## TECH-003 - Current Session, Checkpoint, and Run Evidence

- Plan items: predecessor to `P1-11`, `P5-03`, `P5-06`
- Status: partial
- Commit: present at baseline `203e2ab`
- Owning modules: `repoagent/session_store.py`, `repoagent/run_store.py`, `repoagent/checkpoint.py`
- Tests: `tests/test_session_store.py`, `tests/test_run_store.py`, `tests/test_checkpoint.py`

### Interface

- `SessionStore` persists conversation and memory state.
- `RunStore` persists `task_state.json`, `trace.jsonl`, and `report.json`.
- Checkpoint helpers project workspace/runtime identity into resume metadata.

### Invariants

- JSON state writes use temporary-file replacement.
- A normal completed run contains all three run artifacts.
- Resume classification distinguishes missing, stale, and mismatched state.

### Known Gaps

- Task-state, report, and legacy trace artifacts remain separate from the Turn event transaction.
- Schema versions are validated, but multi-version migration commands do not yet exist.
- Checkpoints provide semantic re-anchoring, not exact execution replay.
- Tool side-effect idempotency belongs to the future ToolGateway; Turn delivery itself is deduplicated.

## TECH-004 - Historical Tool Execution Baseline

- Plan items: predecessor to P3
- Status: superseded by TECH-017 through TECH-020
- Owning modules: `repoagent/tool_executor.py`, `repoagent/tool_context.py`, `repoagent/tools.py`
- Tests: `tests/test_tool_executor.py`, `tests/test_tools.py`, `tests/test_allowed_tools.py`, `tests/test_security.py`

This section records the pre-P3 baseline. Typed definitions, native schema projection, unified routing, effect-aware approval, and enforced capability tokens are now implemented in TECH-017 through TECH-020. Process isolation and MCP remain future P3 work.

## TECH-005 - Current Context and Memory Baseline

- Plan items: predecessor to P4
- Status: partial
- Owning modules: `repoagent/context_manager.py`, `repoagent/features/memory.py`, `repoagent/prompt_prefix.py`
- Tests: `tests/test_context_manager.py`, `tests/test_memory.py`, `tests/test_prompt_prefix.py`

The current implementation separates stable prefix, history, working memory, relevant memory, and the current request. It supports deterministic reduction and Markdown durable topics. Budgets remain character-oriented, memory lacks a backend contract and full provenance lifecycle, and existing ablations are mechanism tests rather than general quality evidence.

## TECH-006 - Current Provider Baseline

- Plan items: predecessor to P2
- Status: partial
- Owning module: `repoagent/providers/clients.py`
- Tests: provider cases in `tests/test_repoagent.py`

Fake, Ollama, OpenAI-compatible, and Anthropic-compatible clients exist. The loop still relies on text protocols and completion metadata side channels; streaming, native tool calls, cancellation, normalized usage, and provider error taxonomy remain P2 work.

## TECH-007 - Current Evaluation Baseline

- Plan items: predecessor to P6
- Status: partial
- Owning modules: `repoagent/evaluation/evaluator.py`, `repoagent/evaluation/metrics.py`
- Tests: `tests/test_evaluator.py`, `tests/test_metrics.py`

The current evaluation layer provides deterministic harness regression and context, memory, and recovery experiments. It is useful as a runtime contract suite. It does not yet prove held-out coding quality or causal long-context/memory gains, and its artifacts need unified provenance, paired statistics, and self-contained evidence bundles.

## TECH-008 - Durable Single-Turn Runtime Spine

- Plan items: `P1-01` through `P1-05`
- Status: implemented
- Implemented: 2026-08-24
- Owning modules: `repoagent/spine/`, `repoagent/agent_turn_runner.py`, `repoagent/runtime.py`
- Persistence: `repoagent/run_store.py`
- Tests: `tests/test_spine_turn.py`, `tests/test_spine_runtime.py`, `tests/test_repoagent.py`
- Evidence: 154 tests passed; Ruff and both CLI help paths passed

### Problem

The original `ask()` path entered a synchronous model/tool loop directly. It had useful task artifacts, but no explicit request identity, legal Turn state machine, correlated event contract, or independently persisted terminal outcome.

### Interface

- `TurnRequest.create(session_id, text)` creates distinct Turn, session, and request identifiers.
- `TurnLifecycle.transition()` enforces the legal transition table.
- `TurnRunner.run(request, emit, drain)` is the Agent-side execution protocol.
- `TurnRuntime.execute()` owns lifecycle events and terminal persistence.
- `RepoAgent.ask()` remains the synchronous compatibility facade.
- `RepoAgent.ask_async()` is the async entry point for the scheduler phase.

### Invariants

- A Turn starts in `accepted`, enters `running`, and reaches exactly one of `completed`, `failed`, or `cancelled`.
- Terminal states have no outbound transitions.
- Every event carries format version, event ID, Turn ID, session ID, request ID, and a monotonic per-Turn sequence.
- The Agent-side runner emits output events but does not own lifecycle transitions.
- Runner exceptions become a persisted failed outcome before the compatibility facade raises an error.
- Request, response, event, and error payloads use the runtime redactor before persistence.

### Data Flow and Persistence Order

```text
TurnRequest
  -> append turn.accepted
  -> atomically project turn.json (accepted)
  -> append turn.started
  -> atomically project turn.json (running)
  -> AgentTurnRunner -> AgentLoop -> model/tools
  -> append runner events
  -> append exactly one terminal event
  -> atomically project terminal TurnOutcome to turn.json
```

The existing `task_state.json`, `trace.jsonl`, and `report.json` remain intact. The new `turn.json` and `turn_events.jsonl` add a stable lifecycle layer without forcing AgentLoop internals into the public Spine contract.

### Failure, Cancellation, and Recovery

Runner exceptions are normalized to `TurnState.FAILED` with a persisted error string. Cancellation propagates through the provider and Tool Gateway boundaries; external process convergence is documented in TECH-021. Append-first Turn persistence and host-start recovery are documented in TECH-010.

### Verification

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
PATH="$PWD/.venv/bin:$PATH" .venv/bin/ruff check .
.venv/bin/repoagent --help
.venv/bin/python -m repoagent --help
```

Observed: 154 tests passed with six pre-existing `datetime.utcnow()` deprecation warnings; Ruff and both CLI smoke commands passed. Focused Spine and compatibility tests contributed 14 new cases.

### Tradeoffs and Follow-ups

`AgentLoop` remains synchronous and runs through `asyncio.to_thread()` so nested synchronous delegation remains compatible and the event loop stays available. Admission, ordering, capacity, and teardown are owned by TECH-009; provider and tool cancellation propagation is completed by TECH-011 and TECH-021.

## TECH-009 - Session Scheduler and Teardown Barrier

- Plan items: `P1-06`, `P1-07`, `P1-08`, `P1-10`, `P1-12`; completed by TECH-021: `P1-09`
- Status: implemented with documented cancellation boundary
- Implemented: 2026-08-24
- Owning modules: `repoagent/spine/scheduler.py`, `repoagent/spine/_barrier.py`
- Application integration: `repoagent/runtime.py`
- Tests: `tests/test_spine_scheduler.py`, `tests/test_repoagent.py`
- Evidence: 165 tests passed; Ruff, CLI smoke, sdist, and wheel build passed

### Problem

Direct concurrent entry into one Agent session can reorder history, overlap mutations, and leave accepted work unresolved during shutdown. Background work must also be unable to consume all interactive capacity.

### Interface

- `Scheduler.submit(TurnRequest) -> TurnHandle` is the admission boundary.
- `TurnHandle.result()` shields the scheduler-owned terminal future; `cancel()` requests explicit cancellation.
- `WorkClass.FOREGROUND` and `WorkClass.BACKGROUND` select independent capacity pools.
- `Scheduler.cancel_session()` cancels one running Turn and drains that session's queue.
- `Scheduler.shutdown(grace)` seals admission, drains queued work, waits a grace period, cancels survivors, and awaits all workers/finalizers.
- `RepoAgent.ask_async()` reuses a scheduler on its event loop; `RepoAgent.ask()` uses the same path and closes its temporary scheduler before returning.

### Invariants

- One session has at most one executing Turn and preserves submission order.
- Different sessions execute concurrently only within their selected pool capacity.
- Foreground and background pools never borrow each other's slots.
- Each Handle owns only its request's output events, so concurrent answers cannot cross.
- Cancelling queued work persists a `cancelled` outcome instead of silently dropping it.
- Concurrent shutdown callers share one cleanup task; cancellation of a waiter does not cancel cleanup.
- Once shutdown seals admission, every later submit fails with `SchedulerDrainingError`.
- Repeated submission of an identical Turn returns the original Handle and does not execute twice.
- Reuse of a Turn ID with a different request is rejected before admission.

### Data Flow and Teardown

```text
submit -> session lane -> work-class semaphore -> TurnRuntime -> TurnOutcome
             |                    |
             +-> FIFO             +-> bounded cross-session concurrency

shutdown -> seal -> cancel queued -> grace for running -> cancel survivors
         -> await workers -> await cancellation finalizers
```

### Failure and Cancellation

The scheduler handles cancellation while queued, while waiting for capacity, and while executing an async runner. `TurnRuntime` converts cancellation into a persisted terminal outcome. `AgentTurnRunner` creates a cancellation token for its worker thread; cancellation reaches AgentLoop, provider adapters, the Tool Gateway, and delegated Agent loops, then the runner waits for worker convergence before returning control. Built-in providers close an active HTTP response. TECH-021 adds cooperative checks around in-process tools and process-group termination for external commands, completing `P1-09`. Python code already blocked inside an arbitrary in-process runner remains bounded only at cooperative boundaries and is therefore not treated as an isolation guarantee.

### Verification

The scheduler suite covers same-session FIFO, bounded cross-session concurrency, foreground/background isolation, queued/running cancellation, shutdown sealing, concurrent shutdown callers, cancellation-safe cleanup, and a deterministic 10,000-request accounting run with 10,000 distinct terminal outcomes.

### Tradeoffs and Follow-ups

- Scheduler lanes are currently keyed by `session_id`; channel-specific conversation routing belongs to the product-surface phase.
- `submit()` durably records admission before returning a Handle; a persistence failure rejects admission synchronously.
- Idle lane reclamation can be added when long-lived channel/gateway hosts are introduced.

## TECH-010 - Atomic Persistence and Crash Recovery

- Plan items: `P1-11`, `P1-12`
- Status: implemented
- Implemented: 2026-08-24
- Owning modules: `repoagent/atomic_io.py`, `repoagent/session_store.py`, `repoagent/run_store.py`
- Integration: `repoagent/spine/runtime.py`, `repoagent/spine/scheduler.py`, `repoagent/cli.py`
- Tests: `tests/test_atomic_io.py`, `tests/test_session_store.py`, `tests/test_run_store.py`, `tests/test_spine_runtime.py`, `tests/test_spine_scheduler.py`, `tests/test_public_api_contract.py`
- Evidence: full pytest, Ruff, CLI smoke, and package build commands in Verification

### Problem

A scheduler Handle previously could be returned before any durable admission record existed. JSONL append and snapshot replacement also had no shared recovery contract, so a process failure could leave a partial record, a stale projection, or an accepted Turn without a terminal outcome. Concurrent SessionStore instances could overwrite newer state.

### Interface

- `TurnRuntime.accept(request)` is the durable admission operation used by `Scheduler.submit()` before it returns.
- `RunStore.commit_turn_event()` appends one validated fact and then atomically updates its optional `turn.json` projection under one per-Turn lock.
- `RunStore.recover_incomplete_turns()` repairs an incomplete JSONL tail, restores stale projections, and terminalizes interrupted Turns.
- `SessionStore` transparently persists `_schema_version` and `_revision`; callers continue to read the original session shape.
- `atomic_io` owns locked JSONL append, fsync, and temporary-file replacement.

### Invariants

- A returned Handle always has a durable `turn.accepted` event and accepted snapshot.
- Turn event sequence numbers are contiguous and correlation IDs cannot change within a Turn.
- No event can be appended after `completed`, `failed`, or `cancelled`.
- The event log is the fact source; `turn.json` is a recoverable projection.
- An incomplete final JSONL record may be truncated during recovery; malformed complete records fail closed.
- A SessionStore writer must have created or loaded the revision it overwrites.
- Identical duplicate Turn delivery shares one Handle; conflicting reuse of the Turn ID fails.

### Data Flow and Persistence Order

```text
Scheduler.submit
  -> TurnRuntime.accept
      -> lock Turn
      -> append + fsync turn.accepted
      -> temp write + fsync + replace turn.json
  -> enqueue
  -> return Handle

terminal transition
  -> lock Turn
  -> append + fsync terminal fact
  -> temp write + fsync + replace terminal projection
```

Session writes use a per-session sidecar lock, compare the loaded revision with the disk revision, then publish the next version with fsync and replace.

### Failure, Cancellation, and Recovery

At host construction, CLI assembly invokes recovery once before accepting new work. An accepted or running Turn without a terminal fact receives one deterministic failed outcome with `interrupted by process restart`. A terminal fact with a stale snapshot is projected back to the correct terminal state. Recovery is idempotent: a second pass neither appends another terminal event nor reports the Turn again.

The recovery contract accounts for work; it does not replay model calls or tool mutations. Side-effect idempotency keys and isolated process cancellation remain P2/P3 responsibilities.

### Security and Privacy

Runtime redaction occurs before events and snapshots enter the persistence layer. Lock files contain no application payload. Corrupt complete records and unsupported schema versions are rejected rather than guessed or overwritten.

### Verification

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/repoagent --help
.venv/bin/python -m repoagent --help
uv build
```

Focused tests cover atomic replacement, incomplete-tail repair, corrupt JSONL rejection, session schema compatibility, stale writers, event sequence and terminal uniqueness, accepted/running crash recovery, stale snapshot repair, startup recovery, duplicate delivery, and conflicting Turn IDs.

### Tradeoffs and Follow-ups

- Sidecar locks coordinate processes on local filesystems; distributed or unreliable network filesystems are outside this contract.
- Session schema `0` remains readable for compatibility, while new writes publish schema `1`.
- Recovery terminates interrupted work instead of automatically replaying it because replaying unknown side effects is unsafe.

## TECH-011 - Typed Provider Runtime

- Plan items: `P2-01`, `P2-02`, `P2-03`, `P2-04`, `P2-05`
- Status: implemented typed streaming, model cancellation, and fallback boundary
- Implemented: 2026-08-24
- Owning modules: `repoagent/providers/base.py`, `repoagent/providers/clients.py`, `repoagent/providers/fallback.py`
- Integration: `repoagent/agent_loop.py`, `repoagent/runtime.py`
- Tests: `tests/test_provider_runtime.py`, `tests/test_repoagent.py`, `tests/test_agent_loop.py`

### Problem

AgentLoop previously called `complete(prompt, max_tokens)` and then inspected the mutable `last_completion_metadata` side channel. Provider text, usage, cache metadata, timeout behavior, and failures therefore had no single result contract, and custom provider behavior could leak into orchestration.

### Interface

- `ModelRequest` carries prompt, output budget, cache policy, timeout override, correlation IDs, attempt number, a thread-safe cancellation token, and provider-neutral `ModelTool` schemas.
- `ModelResult` carries text, normalized tool calls, finish reason, usage, provider/model identity, latency, and immutable metadata.
- `ModelUsage` separates actual, estimated, and missing sources and normalizes cache token fields.
- `ModelEvent` is the common text-delta, tool-call, and terminal streaming event shape.
- `ProviderError` exposes category, retry/fallback verdicts, provider, and optional HTTP status without caller string matching.
- `FallbackModelClient` executes an ordered provider chain and records immutable `ProviderAttempt` evidence.
- `generate_model()` invokes typed providers and contains compatibility for complete-only clients.

### Invariants

- AgentLoop consumes `ModelResult` and does not parse provider response objects or mutable usage side channels.
- Token counts and latency cannot be negative.
- Tool-call arguments are copied into an immutable mapping at the provider boundary.
- Invalid typed return values fail with `ProviderProtocolError`.
- Every provider failure reaching AgentLoop writes a `model_failed` trace event before propagating.
- Structured error evidence contains stable classifications, not secret-bearing response text.
- Every stream must end with one normalized completed event; truncated provider streams fail closed.
- Cancellation is classified separately from failure and never triggers retry or provider fallback.
- Fallback requires an explicit `should_fallback` classification and is forbidden after any text or tool event has escaped from an attempt.
- Authentication, invalid-request, protocol, and cancellation failures fail closed; rate-limit, server, timeout, billing, unavailable-model, and connection failures may switch when a next provider exists.
- Cancellation is checked before a model call, between stream events, before tool execution, and before final answer persistence.
- Multiple native tool calls execute in response order and cannot exceed the Turn tool-step budget.
- Only text inside the final-answer protocol reaches Turn output; reasoning and tool protocol remain internal.

### Data Flow

```text
AgentLoop
  -> ModelRequest(correlation, budget, cache policy)
  -> generate_model
      -> typed client.generate
      -> legacy complete adapter when required
  -> normalized ModelEvent stream
  -> ModelResult(text, native ToolCalls, usage, identity, latency)
  -> execute tool calls serially or stream final text
  -> trace and Turn outcome
```

Fake, Ollama, OpenAI-compatible, and Anthropic-compatible clients retain `complete()` for existing callers. Ollama consumes JSONL generation chunks; OpenAI-compatible consumes Responses SSE; Anthropic-compatible consumes Messages SSE. Their text, terminal state, native tool calls, usage, and cache fields are normalized at the adapter boundary. Complete-only custom clients still use a one-result compatibility fallback.

The current tool registry is deterministically projected to JSON Schema before entering `ModelRequest`. OpenAI and Anthropic adapters translate that neutral schema into their wire shapes. Tool execution remains owned by the existing executor; ToolGateway will replace this temporary projection in P3.

`FallbackModelClient` is an explicit wrapper rather than hidden adapter behavior. Each failed or completed attempt records its chain index, provider, model, status, category, HTTP status, and duration. A successful switch is attached to `ModelResult.metadata` and written as `model_fallback`; an exhausted multi-provider chain raises `ProviderFallbackExhaustedError`, whose structured attempt list is written in `model_failed`. Partial streaming output disables switching so one Turn never combines content from different providers.

### Failure, Timeout, and Cancellation

HTTP, connection, protocol, timeout, and cancellation outcomes now have explicit categories and retry/fallback properties. A `ModelRequest.timeout_seconds` override reaches built-in transports. `ask_async()` cancellation requests scheduler cancellation and waits for the Turn to reach a terminal state. `AgentTurnRunner` then cancels the shared token; Ollama, OpenAI-compatible, and Anthropic-compatible streams register the active response's `close()` method so blocked body iteration is interrupted. Provider cancellation writes `model_cancelled` evidence and prevents later tool execution or successful final persistence.

The standard-library transport cannot expose a response handle while DNS, connection setup, or response-header receipt is still blocked, so that phase remains bounded by the configured timeout. Complete-only compatibility clients can observe cancellation only before and after their blocking call. Tool-process cancellation and bounded external-command convergence are completed by TECH-021.

### Verification

Focused tests cover request validation, usage-source normalization, immutable native tool calls, complete-only compatibility, invalid stream ordering, provider timeout forwarding, Ollama JSONL, OpenAI Responses SSE, Anthropic Messages SSE, tool schema translation, multi-tool execution, chunk-boundary final-answer filtering, AgentLoop correlation, persisted provider failure evidence, idempotent cancellation callbacks, active-response interruption, end-to-end cancelled Turn convergence, status-based fallback classification, successful switching, non-fallback failures, partial-stream protection, cancellation protection, exhaustion evidence, and AgentLoop fallback traces.

### Tradeoffs and Follow-ups

- `last_completion_metadata` remains populated as a compatibility projection, not as the orchestration source of truth.
- Complete-only custom clients and Fake retain a terminal fallback; only the three network adapters claim wire-level streaming.
- Pre-response connection blocking is timeout-bounded because `urllib` exposes no earlier closeable handle.
- Provider chains are assembled programmatically in this slice; user-facing model profiles and configuration validation belong to `P2-06`.
- Native tool calls execute serially until ToolGateway can classify safe read-only calls for bounded concurrency.
- Final-text streaming is intentionally protocol-aware: free-form reasoning outside `<final>` is buffered/discarded rather than shown to users.

## TECH-012 - Validated Model Profiles

- Plan items: `P2-06`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/providers/profiles.py`
- Integration: `repoagent/cli.py`, `repoagent/agent_loop.py`
- Tests: `tests/test_model_profiles.py`, `tests/test_repoagent.py`, `tests/test_public_api_contract.py`

### Problem

Provider selection previously branched directly inside CLI assembly. Model names, endpoint URLs, timeouts, output limits, sampling values, and credential lookup order were independent strings with no common validation or inspectable runtime identity. Invalid configuration could therefore survive until an HTTP request, while adding another model path required duplicating assembly logic.

### Interface

- `ModelProfile` is an immutable, secret-free configuration record for one model endpoint.
- `BUILTIN_MODEL_PROFILES` contains the `ollama`, `openai`, `anthropic`, and `deepseek` defaults.
- `get_model_profile()` resolves a named built-in profile and fails closed on unknown names.
- `--profile` selects the explicit profile; `--provider` remains a compatibility alias and conflicting simultaneous values are rejected.
- CLI and environment overrides produce a new validated profile before a client is constructed.

### Invariants

- Profile names and provider names use a stable lowercase identifier grammar.
- Protocol is one of `ollama`, `openai`, or `anthropic`; provider branding is independent from wire protocol, so the DeepSeek profile can use the Anthropic-compatible adapter.
- Model identifiers are non-empty and trimmed.
- Endpoints are absolute HTTP(S) URLs without embedded credentials, query strings, or fragments.
- Timeout and output budgets are positive; temperature and top-p are finite and range checked.
- Unsupported `top_p` configuration is rejected instead of silently ignored by non-Ollama adapters.
- Credential environment-variable names may enter profile evidence; resolved credential values never enter the profile or trace.

### Assembly Flow

```text
CLI args + project/user environment
  -> select profile name
  -> copy built-in ModelProfile
  -> apply model/endpoint/timeout/generation overrides
  -> validate complete profile
  -> resolve credential value outside the profile
  -> construct adapter by profile.protocol
  -> attach secret-free profile provenance to model_requested trace
```

The existing precedence is preserved: explicit CLI values win, then provider-specific environment variables, then built-in defaults. `REPOAGENT_MODEL_PROFILE` is checked before the legacy-compatible `REPOAGENT_PROVIDER`. `max_new_tokens` is taken from the validated profile when constructing `RepoAgent`, so the runtime and recorded profile cannot disagree about the output reservation.

### Verification

Focused tests cover all built-in profiles, registry immutability, unknown profiles, malformed identifiers, empty models, unsafe or malformed URLs, invalid numeric ranges, duplicate credential sources, unsupported sampling parameters, profile/provider conflicts, environment and CLI precedence, legacy provider assembly, public exports, and trace provenance without secret values.

### Tradeoffs and Follow-ups

- This slice intentionally provides four built-in profiles and programmatic `ModelProfile` construction. A user-editable versioned profile file should be added only with schema migration and secret-reference rules.
- Profile selection is deterministic; workload-aware routing remains `P7-05`.
- Credential presence is not required at construction because local gateways may accept empty keys and offline commands must still initialize. A future `doctor --probe` should report missing credentials before a paid call.

## TECH-013 - Turn Usage Source Accounting

- Plan items: `P2-07`
- Status: implemented
- Implemented: 2026-08-24
- Owning modules: `repoagent/providers/base.py`, `repoagent/spine/runner.py`
- Integration: `repoagent/agent_loop.py`, `repoagent/agent_turn_runner.py`, `repoagent/runtime.py`
- Tests: `tests/test_usage_accounting.py`, `tests/test_provider_runtime.py`, `tests/test_spine_runtime.py`

### Problem

Every AgentLoop iteration previously replaced `last_completion_metadata`, so a multi-step Turn reported only the final model call. This undercounted input/output/cache tokens, hid missing usage rows, and allowed a later actual result to make an incomplete Turn look fully metered. Provider fallback failures and late Turn failures also discarded or obscured earlier usage.

### Interface

- `UsageSource` now defines `actual`, `estimated`, `missing`, and `mixed` explicitly.
- `ModelUsageAggregate.from_usages()` sums model token/cache rows and derives one conservative source verdict.
- Aggregate metadata carries `model_call_count`, four fixed `usage_source_counts`, and `usage_complete`.
- Spine `Usage` persists the same totals and source fields in terminal Turn outcomes.
- `report.json` exposes a top-level `usage` summary instead of requiring consumers to infer totals from the last prompt metadata.

### Invariants

- Token and cache totals are the sum of every completed model result in the Turn.
- A single source repeated across all calls remains that source.
- Combining different sources, or combining any source with a missing row, produces `mixed`.
- `usage_complete` is false for zero calls, missing rows, or mixed rows; estimated-only usage can be complete while remaining clearly estimated.
- Source counts always sum to `model_call_count`.
- A fallback attempt that fails before returning usage is counted as missing, not silently treated as zero-cost actual usage.
- A late provider failure preserves prior token totals and adds the failed attempt as missing in the failed Turn outcome.

### Evidence Flow

```text
provider ModelResult.usage
  -> append per-call ModelUsage row
  -> include failed provider/fallback attempts as missing rows
  -> ModelUsageAggregate
      -> model_parsed.usage_aggregate trace
      -> report.json usage
      -> AgentTurnRunner Usage
      -> turn.completed / turn.failed terminal payload
```

Provider adapters mark usage actual only when their wire response contains at least one recognized usage field. A terminal response object without token counters is `missing`, even if the content itself completed successfully.

### Verification

Focused tests cover same-source summation, actual/estimated/missing mixing, cache read/write totals, invalid aggregate rows, multi-step tool Turns, missing final usage, successful fallback with an unmetered failed attempt, and late provider failure after a metered call. Tests compare trace aggregates, top-level reports, and persisted completed/failed Turn payloads.

### Tradeoffs and Follow-ups

- Failed calls are conservatively marked missing because most provider error responses do not expose billable token counts.
- Cancellation can still terminate before a provider reports usage; cancellation fault accounting belongs to `P6-07` and transport-specific live verification.
- This slice records quantities and provenance only. Model pricing, currency, cache pricing, and unit-success normalization belong to `P2-08` and `P2-09`.

## TECH-014 - Pricing and Call-Efficiency Ledger

- Plan items: `P2-08`
- Status: implemented
- Implemented: 2026-08-24
- Owning modules: `repoagent/pricing.py`, `repoagent/call_efficiency.py`, `repoagent/run_store.py`
- Integration: `repoagent/providers/profiles.py`, `repoagent/cli.py`, `repoagent/agent_loop.py`, `repoagent/agent_turn_runner.py`, `repoagent/runtime.py`, `repoagent/spine/runner.py`
- Tests: `tests/test_call_efficiency.py`, `tests/test_run_store.py`, `tests/test_public_api_contract.py`

### Problem

Turn-level token totals alone cannot answer how many provider attempts occurred, which pricing assumptions were used, or whether a displayed cost excludes failed and unmetered calls. Applying a current vendor price implicitly would also misprice compatible gateways and make old evidence change when a public price changes.

### Interface

- `ModelPricing` is an immutable USD rate snapshot with a deterministic `pricing_id` and human-readable source.
- CLI flags or provider-specific environment variables may attach one complete input/output pricing pair to a validated `ModelProfile`.
- `CallEfficiencyEntry` records one completed, failed, or cancelled provider attempt with correlation IDs, attempt indexes, identity, latency, usage, pricing snapshot, and cost status.
- `RunStore` appends each entry to the run-local `calls.jsonl` ledger and AgentLoop mirrors the row as `model_call_accounted` trace evidence.
- `CallEfficiencySummary` is projected into `report.json` and completed/failed terminal Turn outcomes.

### Invariants

- Runtime code never fetches prices from the network and never assumes that an official vendor rate applies to a proxy or custom endpoint.
- Input and output rates must be configured together, be finite and non-negative, and identify their source.
- Missing or mixed usage produces `incomplete_usage`; absent pricing produces `unpriced`.
- Cache read/write usage requires explicit cache rates and unambiguous input-token semantics.
- Partial priced cost remains visible, but `cost_complete` is true only when every attempt is priced.
- `cost_per_successful_turn_usd` exists only for a successful Turn with complete cost evidence.
- Failed fallback attempts receive their own ledger rows and cannot disappear behind the successful provider result.
- Malformed third-party fallback metadata is ignored as fallback evidence and the valid completed call is still accounted once.

### Evidence Flow

```text
ModelProfile.pricing snapshot + ModelResult.usage
  -> one CallEfficiencyEntry per provider attempt
      -> calls.jsonl durable row
      -> model_call_accounted trace row
  -> CallEfficiencySummary
      -> report.json call_efficiency
      -> turn.completed / turn.failed terminal payload
```

`provider_call_id` is deterministically derived from request ID, AgentLoop attempt, and provider-chain attempt. Ledger writes use the existing locked and flushed JSONL path, so later aggregate claims remain traceable to individual calls.

### Verification

Focused tests cover price validation and stable identity, actual and estimated usage pricing, missing usage, absent pricing, cache deferral, complete and incomplete unit-cost summaries, CLI and environment configuration, end-to-end ledger/trace/report/terminal consistency, fallback partial cost, failed Turn evidence, malformed fallback metadata, public exports, and direct RunStore append behavior.

### Tradeoffs and Follow-ups

- Pricing is intentionally user supplied. A future profile catalog may ship versioned rates, but it must preserve the exact snapshot in every ledger row.
- Failed and cancelled calls are usually unmetered by provider responses, so their cost remains incomplete rather than guessed.
- Cache reads/writes and compaction calls use the accounting rules recorded in TECH-015.
- Cancellation records a call ledger row and trace event; transport-specific billing verification remains part of `P6-07` live fault campaigns.

## TECH-015 - Cache and Compaction Accounting

- Plan items: `P2-09`
- Status: implemented accounting contract; deterministic P4 compaction adds no model calls
- Implemented: 2026-08-24
- Owning modules: `repoagent/providers/base.py`, `repoagent/pricing.py`, `repoagent/call_efficiency.py`
- Integration: `repoagent/providers/clients.py`, `repoagent/cli.py`, `repoagent/agent_loop.py`, `repoagent/agent_turn_runner.py`, `repoagent/spine/runner.py`
- Tests: `tests/test_call_efficiency.py`, `tests/test_provider_runtime.py`, `tests/test_repoagent.py`, `tests/test_usage_accounting.py`

### Problem

Provider usage schemas disagree on whether input tokens include cached tokens. Charging a cache rate without recording that semantic can double count OpenAI-style totals or undercount Anthropic-style fresh input. Context compaction can also become a hidden model call unless the ledger distinguishes it from ordinary AgentLoop generation.

### Interface

- `InputTokenSemantics` classifies each usage row as `fresh`, `total`, or `ambiguous`.
- OpenAI-compatible adapters mark cached input totals as `total`; Anthropic-compatible adapters mark their input counter as `fresh`.
- `ModelPricing` optionally carries independent cache-read and cache-write rates in the same immutable snapshot.
- CLI flags and provider-specific environment variables configure those cache rates without remote lookup.
- `ModelRequest.call_kind` and `CallEfficiencyEntry.call_kind` distinguish `agent` from `compaction`; summaries preserve both counts.

### Invariants

- Fresh input is charged once at the ordinary input rate.
- For `total` semantics, cache-read and cache-write tokens are subtracted before the fresh-input charge, then charged at their explicit cache rates.
- For `fresh` semantics, the input counter is not reduced; cache tokens are charged separately.
- Cached usage with ambiguous semantics produces `ambiguous_cache_usage`, not a numeric estimate.
- Cache tokens exceeding a total-input counter produce `invalid_cache_usage`.
- A used cache dimension without its corresponding rate produces `cache_pricing_required`.
- Local deterministic context trimming is not a provider call and does not increment the compaction bucket.
- Any future model-backed compactor must issue `call_kind=compaction` through this ledger; compaction cost contributes to total successful-Turn cost.

### Evidence Flow

```text
provider raw usage
  -> adapter declares fresh/total input semantics
  -> ModelUsage + explicit cache rates
  -> normalize fresh input and price each token dimension once
  -> CallEfficiencyEntry(call_kind=agent|compaction)
  -> per-kind counts and total Turn cost
```

### Verification

Focused tests prove that equivalent fresh-input and total-input rows produce the same cache-aware cost, missing rates and ambiguous semantics fail closed, impossible cache totals are rejected, pricing snapshots retain cache rates, OpenAI-compatible usage declares total semantics, and compaction calls remain separately countable.

### Tradeoffs and Follow-ups

- The current context reducer is local and deterministic, so production runs correctly report zero compaction model calls today.
- Anthropic-compatible gateways are assumed to preserve Anthropic usage semantics. Live-provider fixtures in `P2-10` must verify this for each supported route.
- A future model-based summarizer must reuse this accounting boundary; a direct unmetered provider call would violate the contract.

## TECH-016 - Deterministic Call Replay and Live Acceptance

- Plan items: `P2-10`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/call_replay.py`
- Integration: `scripts/replay_call_ledger.py`, `live_tests/test_live_providers.py`
- Tests: `tests/test_call_replay.py`, `tests/test_provider_phase_gate.py`, `tests/test_public_api_contract.py`

### Problem

Re-running a model request is not deterministic and can silently apply a new model revision or price. Historical call evidence therefore needs an offline replay path that recomputes normalized rows from the exact persisted usage and pricing snapshot. Real-provider tests are still necessary for wire compatibility, but they must never run accidentally during ordinary local or CI tests.

### Interface

- `replay_call_ledger()` loads a `calls.jsonl`, reconstructs every typed call entry, recomputes cost evidence, and binds the result to a caller-supplied source digest.
- `file_digest()` produces the `sha256:<hex>` identity expected by replay.
- `scripts/replay_call_ledger.py` writes a deterministic replay report and exits non-zero when equivalence fails.
- `live_tests/test_live_providers.py` accepts a comma-separated profile list only when `REPOAGENT_RUN_LIVE_PROVIDER_TESTS=1` is set.

### Invariants

- Replay performs no network access and contains no timestamps or mutable catalog lookup.
- A source digest must be supplied externally; a digest stored only inside the source would not establish lineage.
- Frozen `pricing_id`, normalized usage, estimated cost, cost status, and every other call field must rebuild exactly.
- Duplicate `provider_call_id` values make replay non-equivalent.
- Malformed JSON, unsupported schema versions, incomplete rows, and pricing identity mismatch fail closed.
- Replay output cannot overwrite its source ledger.
- Default pytest discovers only `tests/`; live acceptance is invoked by its explicit path.
- Enabling live acceptance without profiles or required credentials fails instead of silently skipping.

### Replay Flow

```text
externally recorded source digest + calls.jsonl
  -> verify source bytes
  -> reconstruct ModelUsage and ModelPricing
  -> rebuild CallEfficiencyEntry.to_dict()
  -> compare each frozen row
  -> emit deterministic replay report and digest
```

Example offline command:

```bash
python scripts/replay_call_ledger.py \
  --source .repoagent/runs/<turn-id>/calls.jsonl \
  --expected-source-digest sha256:<hex> \
  --output /tmp/repoagent-call-replay.json
```

Explicit live acceptance command:

```bash
REPOAGENT_RUN_LIVE_PROVIDER_TESTS=1 \
REPOAGENT_LIVE_PROFILES=deepseek \
pytest -q live_tests/test_live_providers.py
```

### Verification

Focused tests cover byte-stable repeated replay, cost tampering, external digest mismatch, pricing identity tampering, duplicate call IDs, malformed ledgers, overwrite protection, CLI exit behavior, public exports, AgentLoop provider-neutral imports, and separation of live tests from the default suite. The live test entry was collected but not executed during this implementation because no real-provider run was requested.

### Tradeoffs and Follow-ups

- Replay proves internal equivalence to frozen evidence; it does not prove that a provider invoice was correct.
- Live acceptance currently verifies typed result and non-missing usage. P6 will turn live rows into release-bound evaluation artifacts with provider error denominators.
- Provider endpoints can change independently of the repository, so live evidence must always record its execution date and exact commit when used for a release claim.

## TECH-017 - Typed Tool Gateway Contracts

- Plan items: `P3-01`
- Status: implemented; execution routing completed by `P3-03`
- Implemented: 2026-08-24
- Owning module: `repoagent/tool_contracts.py`
- Tests: `tests/test_tool_contracts.py`, `tests/test_public_api_contract.py`

### Problem

The current tool registry represents schemas as compact strings, risk as a boolean, requests as loose `(name, args)` pairs, and results as content plus an open metadata dictionary. Approval, concurrency, MCP, sandbox, and tracing would each need to reinterpret those shapes. A stable seam is required before those policies can have one owner.

### Interface

- `ToolEffect` classifies environment interaction as `unknown`, `read`, `write`, `execute`, or `external`.
- `ToolDefinition` carries one stable name, description, object JSON Schema, effect, concurrency verdict, and approval declaration.
- `ToolRequest` carries immutable JSON arguments plus call, Turn, request, session, origin, and parent-call correlation.
- `ToolResult` carries structured status, effect, content, duration, error code, affected paths, workspace-change verdict, and immutable metadata.

### Invariants

- Tool names follow one lowercase stable identifier grammar.
- Definitions require closed object schemas: declared properties, an explicit required list, and `additionalProperties=false`.
- Definitions have deterministic SHA256 identities derived from their complete public behavior declaration.
- Contract mappings are copied and recursively frozen; only finite JSON-compatible values and string object keys are accepted.
- Only read-effect tools may declare bounded concurrency safety. A read tool may still require approval when it exposes sensitive information.
- Requests cannot be their own parent and preserve optional correlation without inventing placeholder IDs.
- Successful results cannot carry error codes; every non-success result must carry one.
- A result cannot claim affected paths without a workspace change, or claim a workspace change for read/external/unknown effects.

### Contract Flow

```text
ToolDefinition
  -> model-facing schema and runtime validator (P3-02)
  -> ToolRequest correlation envelope
  -> authorization / sandbox / execution (P3-03 through P3-05)
  -> ToolResult structured evidence
```

The module intentionally contains no handler callable and performs no execution. Implementations and adapters vary behind `ToolGateway` without exposing their internal dependencies to AgentLoop.

### Verification

Focused tests cover recursive immutability, stable definition identities, closed schema validation, effect/concurrency coherence, JSON compatibility, nested argument copies, request correlation, parent-call rejection, structured success, workspace effects, failure codes, duration validation, and public exports. Gateway integration tests now exercise these contracts on the live execution path.

### Tradeoffs and Follow-ups

- `BASE_TOOL_DEFINITIONS` is now the authoritative built-in registry; its projection and validation rules are recorded in TECH-018.
- `ToolResult` is the runtime result. `ToolExecutionResult` remains only as a shallow compatibility projection for existing callers.
- Definition-level timeout and output ceilings plus request-level narrowing are implemented by TECH-021.

## TECH-018 - Single-Source Tool Definition Projection

- Plan items: `P3-02`
- Status: implemented
- Implemented: 2026-08-24
- Owning modules: `repoagent/tools.py`, `repoagent/tool_contracts.py`
- Integration: `repoagent/providers/tool_schema.py`, `repoagent/prompt_prefix.py`, `repoagent/tool_executor.py`, `repoagent/runtime.py`
- Tests: `tests/test_tool_definition_integration.py`, `tests/test_tool_contracts.py`, `tests/test_prompt_prefix.py`, `tests/test_repoagent.py`

### Problem

Built-in tools previously declared compact string schemas for the prompt, converted those strings a second time for provider-native calls, and used hand-written conditionals for basic required/type/default/range checks. Risk was a separate boolean. Those parallel representations could drift, so the model could receive a contract different from the one enforced at execution.

### Interface

- `BASE_TOOL_DEFINITIONS` and `DELEGATE_TOOL_DEFINITION` are the only built-in behavior declarations.
- Registry entries contain exactly a `ToolDefinition` and a bound runner.
- `validate_tool_arguments()` validates a JSON argument object, rejects unknown fields, and returns a new dictionary with declared defaults applied.
- `model_tools_from_registry()` projects name, description, and the unchanged JSON Schema into provider-neutral `ModelTool` values.
- Prompt rendering and prefix signatures consume the same definition and its deterministic identity.

### Invariants

- A built-in tool has one schema, description, effect, approval declaration, and concurrency verdict.
- JSON Schema defaults must satisfy their own declared types and constraints before a definition can be constructed.
- Runtime validation is strict: strings are not silently converted to integers, booleans, or numbers.
- Required and unknown-field failures occur before approval or execution.
- Defaults are applied before repeated-call detection, approval, and runner invocation.
- Repeated-call detection normalizes both current and historical arguments, so `{}` and an explicit default object are semantically equal.
- Filesystem existence, path containment, line-order relationships, and exact patch occurrence remain semantic validation, not duplicated schema logic.
- Workspace snapshot behavior derives from `ToolEffect`; approval behavior derives from `requires_approval`.

### Projection Flow

```text
ToolDefinition
  -> prompt tool text and prefix signature
  -> provider-neutral ModelTool JSON Schema
  -> runtime argument validation and defaults
  -> ToolGateway effect / approval decisions
  -> bound runner semantic checks and execution
```

### Verification

Focused tests assert that registry entries have no legacy `schema`, `risky`, or `description` copies; provider schemas equal the definition projection; prompt signatures are order-stable; defaults and strict types reach runtime validation; invalid calls do not request approval; repeated default-equivalent calls are rejected; and all prior tool, safety, provider, allowlist, and AgentLoop behavior remains green.

### Tradeoffs and Follow-ups

- Tool-specific semantic checks remain an explicit second stage because JSON Schema cannot safely establish workspace state or patch uniqueness.
- `ToolExecutor` is retained as a compatibility adapter; it delegates to the Gateway and no longer owns policy or execution.
- The validator intentionally implements the JSON Schema subset used by RepoAgent tools. New schema keywords require contract tests before use.

## TECH-019 - Unified Tool Gateway Routing

- Plan items: `P3-03`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/tool_gateway.py`
- Integration: `repoagent/runtime.py`, `repoagent/agent_loop.py`, `repoagent/tool_executor.py`
- Tests: `tests/test_tool_gateway.py`, `tests/test_tool_executor.py`, `tests/test_repoagent.py`, `tests/test_safety_invariants.py`

### Problem

The runtime had typed tool contracts but still executed loose `(name, args)` pairs through `ToolExecutor`. Model calls, direct internal calls, and delegation therefore had no single request envelope, stable correlation was optional, and trace evidence reconstructed results from a metadata dictionary. MCP and sandbox adapters would multiply these paths unless execution first converged on one seam.

### Interface

- `ToolGateway.execute(ToolRequest) -> ToolResult` is the only component that invokes a bound tool runner.
- `RepoAgent.build_tool_request()` attaches call, Turn, request, session, origin, and parent-call correlation.
- `RepoAgent.execute_tool_request()` routes a typed request and publishes a temporary compatibility metadata projection.
- `RepoAgent.execute_tool()` and `run_tool()` preserve internal callers while constructing typed requests.
- `ToolExecutor` remains import-compatible but delegates immediately to `RepoAgent.execute_tool()`.

### Invariants

- Unknown, disallowed, invalid, repeated, capability-denied, and approval-denied calls return structured rejected results without invoking a runner.
- Capability authorization runs before argument validation; JSON Schema defaults and workspace semantic validation run before repeated-call detection and approval.
- Effect declarations decide approval and workspace-snapshot behavior; runners do not reinterpret those policies.
- Every executed model call has a non-empty call ID. Provider IDs are preserved and missing IDs are generated within the session.
- AgentLoop persists both complete request and result envelopes in `tool_executed`, with identical call IDs.
- Delegation is a normal registered tool and cannot bypass validation, depth-limited registration, or Gateway accounting.
- The only production invocation of a registry runner is inside `ToolGateway`.

### Execution Flow

```text
model / internal / delegate / compatibility caller
  -> ToolRequest with correlation and origin
  -> ToolGateway
     -> allowlist and definition lookup
     -> subject/session/tool/effect capability authorization
     -> schema plus semantic validation
     -> repeat guard and approval
     -> effect-aware workspace snapshot
     -> bound runner
     -> memory and workspace accounting
  -> ToolResult
  -> trace evidence and compatibility projection
```

### Verification

Focused tests cover typed request enforcement, structured unknown and invalid rejection, read results, write-effect accounting, delegation defaults, compatibility projection, and end-to-end AgentLoop trace correlation. A source scan confirms the registry runner invocation exists only in `repoagent/tool_gateway.py`. Existing allowlist, safety, tool, and AgentLoop suites remain green.

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
.venv/bin/ruff check .
git diff --check
.venv/bin/python -m repoagent --help
.venv/bin/python scripts/collect_resume_metrics.py --help
.venv/bin/python scripts/run_provider_experiments.py --help
.venv/bin/python scripts/run_large_scale_experiments.py --help
.venv/bin/python scripts/replay_call_ledger.py --help
uv build --out-dir /tmp/repoagent-dist-<timestamp>
```

Observed: 304 tests passed; Ruff and diff checks passed; module CLI and all evaluation/replay script help paths passed; source and wheel distributions built with `tool_gateway.py` included.

### Tradeoffs and Follow-ups

- The Gateway currently uses the runtime facade as its host while policy is extracted incrementally. Approval and capability decisions are deep modules, but runtime assembly still owns their lifecycle.
- Tool execution remains a synchronous Gateway interface, while TECH-021 supplies deadlines, cancellation convergence, output-limit status, and subprocess termination beneath it.
- `compatibility_metadata()` intentionally duplicates legacy trace keys during migration; typed request/result payloads are authoritative.

## TECH-020 - Effect-aware Approval and Capability Enforcement

- Plan items: `P3-04`
- Status: implemented
- Implemented: 2026-08-24
- Owning modules: `repoagent/approval.py`, `repoagent/capabilities.py`
- Integration: `repoagent/runtime.py`, `repoagent/tool_contracts.py`, `repoagent/tool_gateway.py`, `repoagent/checkpoint.py`
- Tests: `tests/test_approval.py`, `tests/test_capabilities.py`, `tests/test_tool_gateway.py`, `tests/test_safety_invariants.py`, `tests/test_checkpoint.py`

### Problem

The previous `requires_approval` boolean and `RepoAgent.approve()` method could not explain why execution was allowed, and a misdeclared mutating tool could avoid approval. The reference capability token was an unwired format scaffold: issuing or verifying it did not constrain any tool. Multi-agent delegation requires an enforced, attenuable maximum authority in addition to an operator decision for each side effect.

### Interfaces

- `EffectApprovalPolicy.decide()` returns an immutable `ApprovalDecision` containing allowed, reason, mode, effect, and whether approval was required.
- Every non-read effect requires approval regardless of the definition flag. `read_only` denies non-read effects before operator policy; safe reads do not prompt unless explicitly declared sensitive.
- `CapabilityAuthority` issues and verifies compact JSON plus HMAC-SHA256 tokens using a process-local secret.
- `CapabilityClaims` bind issuer, token ID, subject, session, allowed effects, allowed tools, issuance, expiry, and optional parent token ID.
- `CapabilityAuthority.authorize()` returns a structured reason for missing, invalid/expired, subject, session, tool, or effect denial.
- `ToolRequest` carries the opaque token in memory. `to_dict()` emits only presence and SHA256 digest, never the wire token.

### Invariants

- Gateway execution fails closed when a known tool request has no valid capability.
- A valid signature alone is insufficient: subject, runtime session, tool name, and declared effect must all match.
- Capability authorization occurs before argument validation so an unauthorized caller cannot use validation as a workspace-state oracle.
- A child token's effect and tool sets must be subsets of its parent; its expiry is capped at the parent's expiry.
- Delegate children share the authority but receive a new subject/session token limited to registered read tools, excluding further delegation.
- Non-read effects always require approval, even if a future definition incorrectly sets `requires_approval=False`.
- Invalid calls do not prompt, repeated calls do not prompt again, and every evaluated decision is attached to typed result metadata and trace evidence.
- Capability scope, not token identity or signature, participates in checkpoint runtime identity; resuming a session can safely rotate the process-local authority.

### Authorization Flow

```text
RepoAgent assembly
  -> issue root token from effective tool/effect scope
  -> ToolRequest carries opaque token in memory
  -> ToolGateway verifies signature and subject/session/tool/effect
  -> schema and semantic validation
  -> effect-aware ApprovalDecision
  -> runner execution
  -> ToolResult metadata records capability/approval decisions

delegate
  -> verify parent
  -> intersect to read tools without delegate
  -> issue child subject/session token capped by parent expiry
```

### Security and Privacy

HMAC authenticates token integrity but does not encrypt claims. Raw tokens are intentionally absent from request serialization, trace, report, compatibility metadata, and checkpoint identity; only a SHA256 digest and non-secret scope are recorded. The authority secret is generated in memory and is not persisted. This is an in-process authorization boundary, not protection from malicious code already executing inside the Python process; process isolation belongs to P3-09.

### Verification

Focused tests cover round-trip verification, tampered and malformed input, expiry, all four scope axes, child attenuation, effect/tool expansion rejection, parent-expiry capping, safe-read decisions, mandatory side-effect approval, read-only denial, operator decisions, fail-closed Gateway behavior, cross-session replay, trace token redaction, and checkpoint scope. Full tests are split only because the synthetic metrics suite approaches the command runner's single-wait limit.

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -ra --tb=short <batch-1>
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -ra --tb=short <batch-2>
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -ra --tb=short <batch-3>
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -ra --tb=short tests/test_metrics.py
.venv/bin/ruff check .
git diff --check
```

Observed: 319 tests passed across four batches; the metrics batch retained six pre-existing `datetime.utcnow()` deprecation warnings. Ruff and diff checks passed.

### Tradeoffs and Follow-ups

- Tokens are intentionally workspace-process local. Durable distributed delegation would require persisted key IDs, rotation, revocation, and an operator-managed trust root.
- Root scopes are assembled from the effective registry and read-only mode. Future plugin/MCP registration must occur before scope issuance or request an explicit re-issuance path.
- TECH-021 adds request deadlines and cancellation evidence; P3-09 will turn process isolation into a capability requirement rather than a convention.

## TECH-021 - Bounded Tool Execution Lifecycle

- Plan items: `P3-05`; completes `P1-09`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/tool_execution.py`
- Integration: `repoagent/tool_contracts.py`, `repoagent/tool_gateway.py`, `repoagent/tools.py`, `repoagent/runtime.py`, `repoagent/agent_loop.py`, `repoagent/task_state.py`
- Tests: `tests/test_tool_execution.py`, `tests/test_tool_gateway.py`, `tests/test_repoagent.py`, `tests/test_tool_contracts.py`, `tests/test_safety_invariants.py`

### Problem

External commands previously used blocking `subprocess.run()`, retained complete output, and exposed timeout exceptions as runner failures. Turn cancellation reached providers but could not interrupt an active tool subprocess or a delegated child Agent. That left resource usage unbounded and made a cancelled Turn capable of leaving child processes behind.

### Interface

- `ToolDefinition.timeout_seconds` and `max_output_chars` declare hard execution ceilings.
- `ToolRequest` may narrow either ceiling but cannot expand the definition's authority.
- `ToolExecutionControl` carries a monotonic deadline, cancellation token, and output budget to runners.
- `run_bounded_process()` returns `ProcessOutcome` with explicit completion, timeout, cancellation, exit, output-size, and truncation fields.
- `ToolRunnerOutput` lets a runner return content plus lifecycle metadata without leaking subprocess types into the Gateway contract.
- `ToolGateway.execute(..., cancellation_token=...)` maps lifecycle outcomes to structured `ToolResult` statuses and error codes.

### Invariants

- Effective timeout and output budgets are the minimum of the definition ceiling and any request override; shell arguments may narrow the timeout again.
- Cancellation wins over later normal completion and is represented as `cancelled/tool_cancelled`.
- Deadline expiry is represented as `timeout/tool_timeout`; bounded output is represented as `partial_success/tool_output_truncated`.
- Stdout and stderr reader threads continue draining pipes while retaining bounded text, preventing pipe backpressure from deadlocking a noisy command.
- Final tool content never exceeds the effective output budget.
- POSIX commands start in a new session and termination targets the process group. Windows termination targets the process tree through `taskkill`.
- AgentLoop forwards the Turn cancellation token and remaining deadline to model calls, tools, and delegated child loops.
- A cancelled tool persists a stopped task state and emits `tool_cancelled` evidence before cancellation leaves AgentLoop.

### Execution and Cancellation Flow

```text
Turn cancellation / request deadline
  -> AgentLoop
  -> ToolGateway computes effective limits
  -> ToolExecutionControl
  -> in-process runner cooperative checks
     or run_bounded_process
        -> bounded stdout/stderr drain
        -> terminate process group/tree
        -> grace period, then force kill
  -> ToolRunnerOutput
  -> structured ToolResult and trace evidence
  -> persisted terminal Turn outcome
```

### Security and Failure Boundaries

The host runner provides lifecycle control, not isolation. A command that deliberately detaches into a new session may escape the original process group; P3-09 must place untrusted execution inside an enforceable sandbox boundary. Python file tools check cancellation before and after their short in-process operations, but arbitrary Python runner code cannot be force-killed safely. The output budget bounds retained and returned output; it is not an input-size or filesystem-quota policy.

### Verification

Focused tests exercise request limit validation, effective-limit narrowing, exact final-output bounds, structured timeout/cancellation/truncation results, cancellation through `ask_async()`, terminal task state, delegated control propagation, and shell process-tree cleanup. Adversarial subprocess tests launch descendants that would write a delayed sentinel and verify that neither timeout nor cancellation leaves them alive.

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -ra --tb=short <batch-1>
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -ra --tb=short <batch-2>
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -ra --tb=short <batch-3>
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -ra --tb=short tests/test_metrics.py
.venv/bin/ruff check .
git diff --check
```

Observed: 330 tests passed across four batches; the metrics batch retained six pre-existing `datetime.utcnow()` deprecation warnings. Timeout and cancellation process-tree tests left no sentinel files. Ruff and diff checks passed.

### Tradeoffs and Follow-ups

- The synchronous Gateway surface preserves existing tool implementations; TECH-022 adds orchestration-level parallelism only for definitions already marked concurrency-safe.
- Output capture retains a bounded allocation per stream before applying the exact combined response cap. A future streaming tool protocol may avoid retaining even that bounded intermediate representation.
- P3-09 remains responsible for CPU, memory, filesystem, network, and deliberately detached-process containment.

## TECH-022 - Deterministic Read-only Tool Parallelism

- Plan items: `P3-06`
- Status: implemented
- Implemented: 2026-08-24
- Owning modules: `repoagent/tool_gateway.py`, `repoagent/agent_loop.py`
- Configuration: `repoagent/runtime.py`, `repoagent/cli.py`, `repoagent/checkpoint.py`
- Evaluation: `repoagent/evaluation/tool_execution.py`, `scripts/run_tool_execution_experiment.py`
- Tests: `tests/test_tool_gateway.py`, `tests/test_provider_runtime.py`, `tests/test_tool_execution_experiment.py`

### Problem

One model response may request several independent repository reads. Serial execution wastes wall-clock time, but unrestricted parallelism can overlap mutations, reorder tool messages, race working-memory updates, and make trace evidence depend on thread completion order. The runtime already declared `concurrency_safe`; it needed one bounded scheduler that enforced that declaration without weakening deterministic session semantics.

### Interface

- `ToolGateway.plan_batches()` partitions model-order requests into consecutive safe-read batches and singleton barriers.
- A request is parallel-safe only when its registered definition is both `ToolEffect.READ` and `concurrency_safe=True`.
- Safe runs are chunked by `max_parallel_tools`, which defaults to four and is exposed as `--max-parallel-tools`.
- `ToolGateway.execute_batch()` uses a bounded thread pool for a safe batch and returns `ToolResult` values in request order.
- `RepoAgent.execute_tool_batch()` preserves the runtime facade and compatibility metadata projection.
- `tool_batch_started` and `tool_batch_completed` trace events record mode, ordered call IDs, limit, and duration.

### Invariants

- Unknown, write, execute, external, delegate, and read tools without an explicit concurrency verdict are serial barriers.
- A parallel batch cannot exceed the configured limit, even if a caller bypasses the planner.
- The executor may complete futures in any order; the returned results always match request order.
- Mutable host observations are deferred during worker execution, then memory and process notes commit on the calling thread in request order.
- AgentLoop commits tool history, per-call trace, task state, checkpoints, and batch evidence in the model's call order.
- A mutation begins only after the preceding safe batch has converged, and the next safe batch begins only after that mutation and its evidence commit complete.
- All workers share the Turn cancellation token. A cancelled parallel batch converges before AgentLoop persists ordered cancellation evidence and exits the Turn.
- `max_parallel_tools` participates in checkpoint runtime identity so a resumed run detects execution-policy drift.

### Batch and Commit Flow

```text
model tool calls in provider order
  -> build typed ToolRequests
  -> plan consecutive batches
     -> READ + concurrency_safe: chunks of <= max_parallel_tools
     -> every other call: singleton barrier
  -> execute batch
     -> worker completion order is private
     -> collect futures in request order
     -> commit memory/process observations in request order
  -> AgentLoop commits history/trace/checkpoints in request order
  -> execute next batch
```

### Failure, Cancellation, and Security

Each worker still crosses the complete Gateway authorization, validation, approval, timeout, output, and structured-failure path. Parallel eligibility is derived from the immutable definition, not from model arguments or runner claims. Threads do not provide isolation; P3-09 remains responsible for hostile code and resource containment. `delegate` remains serial because a child Agent owns a nested session lifecycle even though its granted effects are read-only.

### Evaluation and Verification

The experiment runs the real RepoAgent Gateway with eight delayed read requests, alternates serial and capability-parallel arm order, records raw repetitions, checks output order, and marks the result as `synthetic_gateway_scheduler_microbenchmark` with `positive_claim_eligible=false`.

```bash
.venv/bin/python scripts/run_tool_execution_experiment.py \
  --repetitions 3 --tool-calls 8 --delay-ms 8 --max-parallel 4 \
  --output-json /tmp/repoagent-tool-experiment.json
.venv/bin/python -m pytest -q tests/test_tool_execution_experiment.py \
  tests/test_tool_gateway.py tests/test_provider_runtime.py
```

Observed locally: all three repetitions preserved the eight-result order; serial peak concurrency was 1, parallel peak was 4, and median elapsed time was 67.98 ms versus 19.77 ms, a 70.92% synthetic reduction. Full verification passed 340 tests; the metrics batch retained six pre-existing `datetime.utcnow()` deprecation warnings. Ruff, diff checks, all CLI help paths, and source/wheel builds passed. The timing is environment-specific evidence for scheduler overlap, not a general coding-agent performance claim.

### Tradeoffs and Follow-ups

- A fresh thread pool is scoped to each parallel batch, prioritizing lifecycle convergence over worker reuse. A long-lived executor is only justified with explicit shutdown ownership.
- Same-response duplicate reads can overlap because sibling results are not yet in history; a future request coalescer may deduplicate identical safe reads without changing result cardinality.
- TECH-023 formalizes mutation conflict policy. The current implementation intentionally provides no opt-in path for parallel writes.

## TECH-023 - Explicit Mutation Serialization Policy

- Plan items: `P3-07`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/tool_scheduling.py`
- Integration: `repoagent/tool_gateway.py`, `repoagent/agent_loop.py`, `repoagent/runtime.py`, `repoagent/checkpoint.py`, `repoagent/cli.py`
- Tests: `tests/test_tool_scheduling.py`, `tests/test_tool_gateway.py`, `tests/test_provider_runtime.py`, `tests/test_checkpoint.py`

### Problem

P3-06 made side-effecting calls singleton barriers, but the reason existed only as branching logic. A later optimization could accidentally group writes, or treat a misleading concurrency flag as permission, without changing a public contract or trace schema. Parallel mutation also cannot be made correct from path inequality alone while approval, workspace snapshots, partial effects, and cancellation accounting are global.

### Interface

- `MutationConflictPolicy` is the versioned policy vocabulary. The current release accepts only `serial`.
- `ToolBatchMode` distinguishes serial and parallel execution decisions.
- Immutable `ToolBatch` binds ordered requests, declared effects, mode, scheduling reason, and mutation policy.
- `ToolGateway.plan_batches()` returns `ToolBatch` decisions rather than anonymous tuples.
- `--mutation-conflict-policy serial` exposes the effective policy at assembly without advertising an unsafe implementation.
- Batch start and completion events persist `mode`, `scheduling_reason`, `effects`, and `mutation_conflict_policy`.

### Invariants

- Under `serial`, every batch containing any non-read effect has exactly one request.
- A parallel batch requires at least two requests and every effect must be `read`.
- `concurrency_safe=True` cannot override a non-read effect; the ToolDefinition contract already rejects that declaration, and the scheduler independently checks both fields.
- Unknown tools, delegates, unsafe reads, writes, shell execution, and external effects are serial decisions with explicit reasons.
- Direct callers that submit several unsafe requests to `execute_batch()` still execute them one by one.
- Each mutation completes Gateway validation, approval, execution, workspace diff, memory/process observation, history, trace, and checkpoint commit before the next batch begins.
- The policy participates in checkpoint runtime identity; resume detects a changed execution policy.
- Unsupported policies fail during runtime assembly rather than silently falling back to serial or enabling concurrency.

### Scheduling Flow

```text
ToolRequests in model order
  -> resolve registered ToolEffect
  -> READ + concurrency_safe
       -> bounded read batch (TECH-022)
  -> every other effect
       -> MutationConflictPolicy.SERIAL
       -> singleton ToolBatch(reason=mutation_conflict_policy)
       -> full Gateway execution and ordered evidence commit
  -> next batch
```

### Conflict Correctness

A regression case submits two patches against the same original file content. The first patch succeeds; only after its workspace evidence commits does the second request validate against the new file state and receive `invalid_arguments`. The final file contains the first patch, demonstrating state-observing serialization rather than merely a concurrency counter of one.

### Why No `disjoint_paths` Policy Yet

The current Gateway captures whole-workspace before/after snapshots. Concurrent writes to different files would cause each result to observe sibling changes and misattribute `affected_paths`. Interactive approvals could also race, and a command may mutate paths not derivable from its arguments. A policy that permits parallel mutations must therefore provide all of the following before registration:

- sequential authorization and approval preflight;
- canonical, complete resource claims with overlap detection;
- effect-scoped diff attribution;
- deterministic partial-failure and cancellation semantics;
- adversarial tests for symlink changes, renamed parents, hidden command effects, and rollback boundaries.

Rejecting the unimplemented `disjoint_paths` name is intentional fail-closed behavior.

### Verification

Tests cover immutable batch contracts, invalid parallel shapes, singleton mutation planning, unsupported-policy rejection, trace reasons, checkpoint identity, mixed read/write barriers, ordered results, and conflicting same-file patches. Full verification passed 347 tests; the metrics batch retained six pre-existing `datetime.utcnow()` deprecation warnings. Ruff and diff checks passed.

### Tradeoffs and Follow-ups

- The CLI currently has one mutation-policy choice so runtime identity and future schema evolution are explicit. It is not a claim that alternate policies exist.
- Serial Shell execution remains necessary even if a command string appears read-only; declared execution effect controls scheduling.
- MCP tools introduced in P3-08 must declare an effect. Missing or unknown effects remain serial and cannot opt into read parallelism.

## TECH-024 - MCP Discovery and Gateway Registration

- Plan items: `P3-08`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/mcp.py`
- Integration: `repoagent/runtime.py`, `repoagent/agent_loop.py`, `repoagent/tool_gateway.py`
- Tests: `tests/test_mcp.py`, `tests/test_provider_runtime.py`

### Interface and Invariants

`MCPManager` accepts named clients that implement `list_tools()` and `call_tool()`. Discovery validates server names, remote tool names, JSON input schemas, effects, deadlines, output ceilings, approval flags, concurrency declarations, and isolation requirements. Remote tools receive deterministic `mcp_<server>_<tool>` names and are registered before capabilities are issued, so they use the same prompt/native schema projection, argument validation, approval, scheduling, execution, redaction, trace, and checkpoint paths as local tools.

Local-name collisions and malformed definitions fail during runtime assembly. Missing or unknown effects become `external`; they remain serial and cannot opt into read concurrency. An MCP endpoint declared by a client must pass the runtime network policy before discovery. Result evidence records both the server and remote tool identity.

### Tradeoffs and Verification

The core owns a provider-neutral client contract, not a bundled MCP stdio or HTTP lifecycle. A concrete transport can be injected without bypassing Gateway policy. Discovery, execution, native-schema exposure, collision handling, invalid schemas, conservative effects, endpoint denial, and trace metadata are covered by focused tests.

## TECH-025 - Explicit Sandbox Adapter Boundary

- Plan items: `P3-09`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/sandbox.py`
- Integration: `repoagent/tool_context.py`, `repoagent/tools.py`, `repoagent/runtime.py`
- Tests: `tests/test_sandbox.py`, `tests/test_safety_invariants.py`

### Interface and Invariants

`SandboxAdapter` defines execution identity, an explicit isolation claim, and a bounded process result contract. `DirectSandboxAdapter` is deliberately identified as `direct_host` and never claims isolation. `IsolatedSandboxAdapter` accepts only an injected backend that explicitly declares `is_isolated=True`, implements execution, and returns a typed `ProcessOutcome`. Shell execution routes through the adapter, while deadline, cancellation, output-limit, and sandbox identity evidence remain visible to the Gateway.

The project does not bundle a VM/container engine in this phase. The adapter boundary permits BoxLite, container, or remote-executor integration while preventing the direct-host fallback from being mislabeled as isolated.

## TECH-026 - Fail-closed Isolation Enforcement

- Plan items: `P3-10`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/tool_gateway.py`
- Integration: `repoagent/tool_contracts.py`, `repoagent/runtime.py`, `repoagent/checkpoint.py`, `repoagent/cli.py`
- Tests: `tests/test_sandbox.py`, `tests/test_checkpoint.py`

### Policy

Isolation can be required by a `ToolDefinition` or by task-level `--require-isolation` policy for execute/external effects. The Gateway evaluates this requirement before argument validation, approval, or runner invocation. If the active adapter is not isolated, execution returns a structured `sandbox_required` rejection and security evidence; it never falls back to direct host execution. Sandbox identity and the task requirement participate in checkpoint runtime identity so a resumed run detects policy drift.

Tests prove that both local shell and MCP isolation requirements deny before side effects and that a backend cannot gain isolated status without the explicit adapter contract.

## TECH-027 - Adversarial Runtime Security Controls

- Plan items: `P3-11`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/security.py`
- Integration: `repoagent/mcp.py`, `repoagent/tools.py`, `repoagent/tool_gateway.py`
- Tests: `tests/test_security.py`, `tests/test_security_adversarial.py`, `tests/test_safety_invariants.py`

### Controls and Coverage

`NetworkPolicy` accepts absolute HTTP(S) endpoints, rejects embedded credentials, supports an exact host allowlist, and denies localhost plus literal private, loopback, link-local, multicast, reserved, and unspecified addresses by default. Shell processes receive an allowlisted environment rather than the host secret set, and persisted/tool-returned artifacts pass through secret-value redaction.

Adversarial regressions cover workspace traversal and symlink escape, shell command injection behind approval, secret environment leakage, managed-worktree symlink escape, MCP output redaction, SSRF targets, and network allowlisting. Denials remain structured Gateway outcomes rather than raw exceptions.

### Limitation

The policy does not resolve arbitrary hostnames and therefore does not independently prevent DNS rebinding. Deployments that permit remote MCP should combine a strict hostname allowlist with an isolated backend that enforces egress at the network boundary.

## TECH-028 - Controlled Git and Worktree Tools

- Plan items: `P3-12`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/tools.py`
- Integration: `repoagent/tool_contracts.py`, `repoagent/tool_gateway.py`, `repoagent/runtime.py`
- Tests: `tests/test_git_tools.py`, `tests/test_capabilities.py`, `tests/test_checkpoint.py`

### Interface and Invariants

The coding extension registers read-only `git_status`, `git_diff`, and `git_worktree_list` tools plus approval-required `git_worktree_create` and `git_worktree_remove` mutations. Every Git operation uses a fixed argv vector with `shell=False`; worktree names use a strict identifier grammar and resolve only beneath `.repoagent/worktrees`. Creation uses a managed `repoagent/<name>` branch. Removal intentionally omits `--force`, so dirty worktrees fail rather than lose changes. Non-zero Git exits are normalized as structured tool errors.

### Verification

Tests cover repository status/diff, worktree create/list/remove, invalid and injection-shaped names, capability scope, and checkpoint identity. Final P3 verification passed 375 tests: 369 non-metrics tests and 6 metrics tests. Ruff and whitespace checks pass; the metrics suite retains six existing `datetime.utcnow()` deprecation warnings.

P3 is complete at 12/12 roadmap items. Concrete MCP transports and a production isolation engine are deployment integrations behind the completed core contracts, not implicit direct-host fallbacks.

## TECH-029 - Immutable Context Segment Manifest

- Plan items: `P4-01`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/context_manager.py`
- Integration: `repoagent/runtime.py`, `repoagent/agent_loop.py`
- Tests: `tests/test_context_manager.py`, `tests/test_repoagent.py`, `tests/test_metrics.py`

### Problem

Context assembly previously depended on string-keyed dictionaries and a hard-coded join expression. The order was observable but not represented as a contract, sources were absent from evidence, and checkpoint text was merged into the prefix. Token budgeting, compaction provenance, memory freshness, and lazy Skill activation would therefore have had no stable segment identity on which to operate.

### Interface and Invariants

`ContextSegmentDefinition` declares a stable name, source, order, reducibility, and mandatory policy. `ContextSegment` binds one definition to raw text, rendered text, an optional budget, and immutable detail metadata. The canonical manifest is:

```text
runtime.prefix
  -> runtime.checkpoint
  -> memory.working
  -> memory.retrieval
  -> session.history
  -> request.user
```

Names and order values are unique. The current request is mandatory, non-reducible, and always last. Checkpoint state is a separate non-reducible segment; an absent checkpoint remains visible in the manifest as `present=false` but contributes no prompt separator. Prompt assembly iterates the definition order rather than naming sections in a second hard-coded list. Segment source and policy fields are persisted in prompt metadata while the prior `sections`, `section_order`, `relevant_memory`, `history`, and `current_request` fields remain compatible.

### Verification and Limits

Tests cover exact manifest order and sources, checkpoint placement, mandatory request policy, immutable definitions/details, existing reduction order, request preservation, history compression, resume prompts, and metrics compatibility. Focused verification passed 16 tests with the six existing metrics deprecation warnings.

TECH-030 replaces the character-budget limitation. TECH-031 adds output reservation; semantic compaction with provenance belongs to P4-04.

## TECH-030 - Provider-aware Token Budgeting

- Plan items: `P4-02`
- Status: implemented
- Implemented: 2026-08-24
- Owning module: `repoagent/tokenization.py`, `repoagent/context_manager.py`
- Integration: `repoagent/runtime.py`, `repoagent/checkpoint.py`, `repoagent/cli.py`, `repoagent/evaluation/metrics.py`
- Tests: `tests/test_tokenization.py`, `tests/test_context_manager.py`, `tests/test_checkpoint.py`, `tests/test_metrics.py`

### Problem

Character ceilings do not represent provider context limits: ASCII, CJK, code, and serialized tool schemas tokenize differently. Post-request usage cannot prevent an oversized request because it arrives after provider admission. The runtime needs a pre-request counter with explicit provenance and must never label a heuristic as provider usage.

### Contracts and Resolution

`TokenCounter` exposes a stable identity, a `provider` or `estimated` source, and non-negative integer counting. Resolution follows a strict order:

1. use an explicit `model_client.token_counter` contract;
2. adapt a callable provider `count_tokens()` method;
3. use `Utf8TokenEstimator`, recording provider/model identity, bytes-per-token assumption, and `source=estimated`.

`CallableTokenCounter` rejects negative, boolean, and non-integer results. The fallback counts UTF-8 bytes rather than Python characters, so non-ASCII text is not treated as single-byte input, but it remains an estimate and is reported as such.

### Budget Flow and Invariants

Context reduction now compares `prompt_tokens` with `prompt_token_budget`. Segment ceilings, floors, overflow deltas, relevant-memory allocation, recent-history admission, and binary-search clipping all use the same counter instance. Character lengths remain diagnostic fields only; `budget_chars` is `null` and cannot be mistaken for the enforcement unit.

The current request and checkpoint remain non-reducible. Prompt metadata records counter provenance plus raw/rendered token counts for every segment. `--context-token-budget` configures the pre-request ceiling. Delegates inherit the exact counter and segment budgets. Context budget, segment budgets, and counter identity participate in checkpoint runtime identity, so resume rejects a changed budgeting contract.

When reducible segments reach their configured floors and the prompt still exceeds the input ceiling, `ContextBudgetExceededError` stops the request before provider invocation and records observed/budget token values plus counter provenance. A direct ContextManager ablation may construct an over-budget prompt with reduction disabled; TECH-031 ensures the runtime still applies context-window admission before provider invocation.

Context ablation persists both character and token measurements, token compression ratios, and counter source/identity. Existing character metrics remain for historical comparison, not enforcement.

### Verification and Limits

Tests cover injected provider counters, invalid counter results, estimated Unicode counting, resolution precedence, token-aware clipping, mandatory request preservation, fail-closed overflow, CLI configuration, delegate inheritance, checkpoint mismatch, benchmark setup, and context-ablation provenance. The 12-task scripted harness passed 12/12; its context-reduction case finished at exactly 300/300 estimated input tokens with five recorded reductions.

Full verification passed 384 tests. Ruff, whitespace checks, and CLI help smoke passed; the metrics batch retains six existing `datetime.utcnow()` deprecation warnings.

The default fallback does not claim tokenizer-exact counts. TECH-031 derives the effective input ceiling by reserving requested output capacity from a configured model context window.

## TECH-031 - Output Reservation and Context-window Admission

- Plan items: `P4-03`
- Status: implemented
- Implemented: 2026-08-25
- Owning module: `repoagent/context_window.py`
- Integration: `repoagent/providers/profiles.py`, `repoagent/runtime.py`, `repoagent/cli.py`, `repoagent/checkpoint.py`, `repoagent/evaluation/metrics.py`
- Tests: `tests/test_context_window.py`, `tests/test_model_profiles.py`, `tests/test_checkpoint.py`, `tests/test_metrics.py`

### Problem

An input-only ceiling is insufficient because providers admit input and requested output against one model context window. A 3,000-token prompt with a 4,096-token completion reservation requires at least 7,096 tokens of capacity even before provider-specific framing. The runtime must reject invalid reservations at assembly and prove that no model call occurs after a failed admission.

### Contract

`ContextWindowBudget` binds four pieces of configuration: context-window tokens, configured input tokens, reserved output tokens, and window provenance. It computes:

```text
available_input = context_window - reserved_output
effective_input = min(configured_input, available_input)
admission_total = rendered_prompt + reserved_output
```

The contract rejects non-positive values and reservations greater than or equal to the window. `ContextWindowAdmission` records configured/effective input, rendered prompt, output reservation, total reserved tokens, remaining headroom, provenance, and the admission decision. Exact-boundary admission is valid; one token over is rejected.

### Runtime Flow and Invariants

Model profiles now carry `context_window_tokens` and `context_window_source`. The built-in 32,768-token value is labeled `repoagent-conservative-default`; it is a runtime admission setting, not a claim about a provider's advertised maximum. `--context-window-tokens` creates a `cli-override` source. Direct runtime arguments and defaults receive distinct provenance.

`RepoAgent` constructs the window policy before `ContextManager`, passes only the effective input budget into prompt reduction, and performs a second admission check on the rendered token count before creating `ModelRequest`. `max_new_tokens` is the reserved output value and the same value is sent to the provider. Failed mandatory-input admission becomes a failed Turn and leaves the model client's prompt list empty.

Delegates inherit configured input, window, output reservation, counter, segment budgets, and provenance. Benchmark setup uses `configure_context_budget()` so runtime policy and ContextManager cannot diverge. Context window, source, configured/effective input, output reservation, total admission, and headroom enter prompt evidence. Window policy also participates in checkpoint runtime identity. Context ablation reports an admission rate and the observed window/reservation set.

### Verification and Limits

Focused tests cover exact-boundary admission, one-token overflow, invalid reservations, output-driven input reduction, provenance, CLI/profile validation, atomic reconfiguration, delegate inheritance, checkpoint identity, full-Turn preflight failure, and zero provider calls after denial.

Full verification passed 391 tests. The 12-task scripted harness passed 12/12; its context-reduction task used 300 input tokens plus a 64-token output reservation against the 32,768-token conservative runtime window, recorded five reductions, created a checkpoint, and passed its verifier. Ruff and whitespace checks passed; the metrics batch retains six existing `datetime.utcnow()` deprecation warnings.

The runtime reserves the maximum requested output, not the output eventually consumed. Provider-specific message framing or hidden system tokens are covered only when the injected provider counter includes them; estimated counters retain their explicit uncertainty.

## TECH-032 - Deterministic History Compaction Provenance

- Plan items: `P4-04`
- Status: implemented
- Implemented: 2026-08-25
- Owning module: `repoagent/compaction.py`
- Integration: `repoagent/context_manager.py`, `repoagent/checkpoint.py`
- Tests: `tests/test_compaction.py`, `tests/test_context_manager.py`, `tests/test_checkpoint.py`

### Problem

Long sessions cannot retain every message and tool payload verbatim. The former reducer already collapsed duplicate reads, reused file summaries, and shortened old tool output, but those decisions existed only as aggregate counters. An evaluator could not reconstruct which source entry was retained, summarized, clipped, collapsed, or dropped, and a policy change could silently alter checkpoint behavior.

### Contract

`DeterministicHistoryCompactor` owns one versioned policy, `deterministic-history-v1`. It reserves a six-entry recent window, traverses older entries in source order, collapses repeated reads of the same path, reuses available file-memory summaries, and creates bounded deterministic summaries for other tool results. It then admits candidates newest-first against the history segment's provider-aware token budget.

Every input entry produces exactly one immutable `CompactionRecord`. A record contains its source index, role and tool type, transformation, budget action, inclusion decision, input/output token counts, content digests, and content-free transformation provenance. Stable record IDs and a result-level provenance digest make repeated compaction of identical state comparable. The rendered and raw transcript also receive digests. Raw message bodies, tool output, and memory-summary text are not copied into provenance records.

The result distinguishes semantic transformation from budget pressure:

- `retain_recent`, `trim_old_message`, `reuse_file_summary`, `summarize_tool_output`, and `collapse_duplicate_read` identify the operation;
- `full`, `clipped`, `dropped`, and `collapsed` identify the budget outcome;
- aggregate compatibility counters remain available while trace/report metadata gains the full per-source record list.

Turning off context reduction preserves the raw transcript and labels compaction `disabled`; it does not manufacture compaction records. The local compactor never invokes a provider, so `call_kind=compaction` remains zero. Any future model-backed summarizer must use the TECH-015 call ledger.

### Runtime and Resume Invariants

`ContextManager` is now a consumer of the compactor rather than the owner of history heuristics. File-summary lookup returns its timestamp and freshness marker alongside the summary so provenance can identify the reused evidence without embedding its text. Prompt metadata carries the strategy, applied flag, per-entry records, counts, and digests through the existing redacted trace/report write path.

The strategy version participates in checkpoint runtime identity. Resuming a checkpoint produced under a different compaction policy therefore reports a runtime mismatch instead of silently rebuilding a different prompt.

### Verification and Limits

Focused tests cover deterministic IDs and provenance digests, one-record-per-source coverage, duplicate-read lineage, file-summary source/freshness metadata, immutable records, raw-transcript ablation, clipping/drop accounting, token-budget compliance, absence of raw secret and summary text from serialized provenance, ContextManager integration, and checkpoint identity.

Full verification passed 396 tests. The 12-task scripted harness passed 12/12; its context-reduction checkpoint recorded 13 source entries and 13 compaction records under the versioned strategy while retaining five prompt-budget reductions. Ruff, whitespace checks, and `uv build` passed. The metrics suite retains six existing `datetime.utcnow()` deprecation warnings.

This is extractive deterministic compaction, not semantic model summarization. It prioritizes reproducibility, cost visibility, and bounded behavior; quality comparisons against model-backed or learned compaction belong to `P4-11` paired evaluation.

## TECH-033 - Memory Backend Contract and In-memory Adapter

- Plan items: `P4-05`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/memory_backend.py`, `repoagent/memory_contract.py`
- Integration: public exports in `repoagent/__init__.py`; runtime migration is owned by `P4-06`
- Tests: `tests/test_memory_backend.py`

### Problem and Seam

The existing `LayeredMemory` facade mixes working-state mutation, lexical retrieval, file freshness, durable Markdown I/O, and session serialization. Mirroring all of those methods in a backend would create a shallow interface and force plugins to reproduce RepoAgent internals. The backend seam instead describes the external long-term-memory capability that can genuinely vary across local, remote, and test adapters.

`MemoryBackend` is a runtime-checkable asynchronous Protocol with five operations:

- `recall(query, user_id XOR agent_id, top_k)` returns bounded standardized candidates;
- `store(session_id, messages)` hands a conversation slice to the adapter;
- `feedback(signals)` accepts optional free-form usage signals and may be a no-op;
- `start()` and `stop()` define host-owned lifecycle points.

`MemoryHit` is the immutable carrier across the seam. It validates pre-rendered text, a finite normalized score in `[0, 1]`, and adapter-specific metadata. Text is the only model-facing field; metadata is provenance and correlation data, not hidden prompt content.

### In-memory Adapter

`InMemoryMemoryBackend` is the second concrete target that makes the seam real before production migration. It provides immediate read-after-write behavior, session-scoped lexical recall, deterministic relevance/recency ordering, explicit owner-track provenance, bounded `top_k`, defensive copying, lock-protected concurrent writes, feedback capture, and idempotent lifecycle. Calls before `start()` fail explicitly. For this fake, `user_id` or `agent_id` maps directly to the `session_id` used by `store`; production identity mapping remains adapter-owned.

The fake is a test adapter, not a durability claim. `stop()` retains its process-local contents so lifecycle restart tests remain deterministic; process exit loses all state.

### Adapter Conformance

`MemoryBackendContractTests` and `MemoryBackendLifecycleContractTests` are reusable test bases. Adapter authors supply only `make_backend()`. The suite checks structural Protocol compliance, bounded typed recall, store/recall interoperability without requiring immediate hits, free-form feedback tolerance, owner XOR behavior, idempotent lifecycle, and safe stop-before-start. This establishes a portable lower bound; it does not measure retrieval quality, extraction correctness, latency, or persistence durability.

Focused tests also verify score validation, immutable hit fields, incomplete-adapter rejection, lifecycle enforcement, owner isolation, deterministic ranking, defensive copies, concurrent no-loss storage, and invalid-input handling.

Full verification passed 410 tests. The 12-task scripted harness passed 12/12 and retained its 13-record history-compaction evidence. Ruff, whitespace checks, and `uv build` passed. The metrics suite retains six existing `datetime.utcnow()` deprecation warnings.

### Migration Boundary

P4-05 deliberately does not route `LayeredMemory` or AgentLoop through this backend. P4-06 owns that behavioral migration and must preserve current working, episodic, file-summary, durable-topic, checkpoint, and session invariants while adding backend lifecycle and failure evidence. Keeping definition and migration separate makes regressions attributable and leaves the current runtime operational throughout the transition.

## TECH-034 - Memory Backend Runtime and Evidence Metadata

- Plan items: `P4-06`, `P4-07`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/features/memory.py`, `repoagent/agent_turn_runner.py`
- Integration: `repoagent/runtime.py`, `repoagent/context_manager.py`, `repoagent/checkpoint.py`
- Tests: `tests/test_memory.py`, `tests/test_memory_backend.py`, `tests/test_repoagent.py`

`LayeredMemory` is now the default `MemoryBackend` adapter while retaining its compatibility facade. Runtime startup and shutdown own backend lifecycle. Before a Turn enters the synchronous AgentLoop, `AgentTurnRunner` performs bounded agent-track recall; standardized hits join the existing relevant-memory segment. After a successful Turn, the redacted user/assistant slice crosses `store`. Recall failure fails before provider invocation; store failure changes the Turn outcome to failed and is written into final evidence. The local adapter deliberately does not convert raw dialogue into facts: existing tool-result extraction and explicit durable promotion remain the epistemic filters.

Backend evidence records implementation identity, recall/store status, accepted and rejected hit counts, and stored-message count. External query and store payloads pass through the runtime redactor. Hits containing configured secret values are rejected before prompt assembly. Backend type, compaction policy, and the qualified-ID/digest Skill catalog snapshot participate in checkpoint identity. `reset()` replaces the default adapter rather than retaining a stale object and refuses to run while a backend lifecycle is active.

Memory records now carry source, normalized confidence, freshness, supersession, and conflict metadata. A newer same-subject note supersedes older active evidence; equal-time/equal-confidence contradictions remain explicit conflicts. Retrieval excludes superseded notes. File summaries preserve content freshness, source, confidence, conflict slots, and the digest they replaced. Durable hits expose the same normalized metadata shape. These fields represent evidence state, not truth guarantees.

## TECH-035 - Safe Deterministic Memory Consolidation

- Plan items: `P4-08`
- Status: implemented
- Implemented: 2026-08-25
- Owning module: `repoagent/memory_consolidation.py`
- Integration: `repoagent/runtime.py`
- Tests: `tests/test_memory_consolidation.py`, durable-memory cases in `tests/test_repoagent.py`

`MemoryConsolidator` accepts only a user request plus final answer, never tool output or hidden process state. Consolidation requires explicit remember/persist intent and structured durable prefixes for project conventions, decisions, dependencies, or preferences. Every candidate records topic, source, and confidence. The injected redactor runs before admission; changed/redacted values, secret-shaped text, checkpoint-like transient state, stdout/stderr/traceback material, and oversized output are rejected with structured reasons. Runtime reports retain accepted/rejected counts and reasons. This local deterministic path makes no model call and does not claim learned extraction quality.

## TECH-036 - Local Skill Catalog, Activation, and Watching

- Plan items: `P4-09`, `P4-10`
- Status: implemented
- Implemented: 2026-08-25
- Owning module: `repoagent/skills.py`
- Integration: `repoagent/runtime.py`, `repoagent/context_manager.py`
- Tests: `tests/test_skills.py`, `tests/test_context_manager.py`, `tests/test_checkpoint.py`

Skills use one strict `SKILL.md` manifest: stable lowercase ID, name, description, version, source, `always`, declared references, and optional binary/environment requirements. Catalog discovery validates frontmatter and reference containment but retains no body. Activation ranks available manifests by explicit query overlap, force-includes available `always` entries, then lazily reads only selected bodies. References are also loaded only through a declared relative path and cannot escape the Skill directory.

`LocalSkillPool` provides the runtime search seam. `SkillChangeWatcher` maintains a file metadata snapshot, supports deterministic polling and an optional daemon loop, and atomically refreshes the catalog after create/change/delete. Runtime polls before prompt construction, injects selected content through a dedicated reducible Skill segment, records qualified IDs and token use, and stops the watcher with the Agent. Empty catalogs consume no segment budget. This is keyword activation, not semantic Skill correctness; dependency presence is availability evidence, not proof a workflow will succeed.

## TECH-037 - Answer-isolated Paired Context and Memory Evaluation

- Plan items: `P4-11`
- Status: implemented
- Implemented: 2026-08-25
- Owning module: `repoagent/evaluation/paired.py`
- Tests: `tests/test_paired_evaluation.py`

`PairedEvaluator` passes runners an `EvaluationInput` that omits the case's expected answer. A distinct grader callable receives the full case only after a `TrialOutput` exists. Every control/treatment observation is paired by task ID and repetition. The artifact preserves raw rows, per-pair deltas and win/tie/loss outcomes, while reporting `effective_n` as unique tasks separately from run and pair counts. Context tests require treatment quality preservation while measuring input reduction; memory tests demonstrate treatment gains without placing expected answers in runner/model-client state.

The evaluator rejects duplicate task IDs, non-distinct variants, shared runner/grader callables, invalid repetition counts, and wrong runner/grader return types. It is an experiment framework and deterministic regression proof, not a statistical generalization claim; confidence intervals and live-provider campaigns remain P6 work.

### P4 Completion Verification

Full verification passed 426 tests. The 12-task scripted harness passed 12/12; its context-reduction checkpoint retained five budget reductions, 13 source entries and 13 compaction records, and successful memory backend recall/store evidence. Ruff, whitespace checks, and `uv build` passed. Six existing `datetime.utcnow()` deprecation warnings remain in the metrics writer.

## TECH-038 - Semantic Trace Context and Call Correlation

- Plan items: `P5-01`, `P5-02`, `P5-05`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/tracing.py`, `repoagent/spine/events.py`, `repoagent/spine/runtime.py`
- Integration: `repoagent/agent_loop.py`, `repoagent/runtime.py`, `repoagent/call_efficiency.py`
- Tests: `tests/test_tracing.py`, `tests/test_spine_runtime.py`, `tests/test_call_efficiency.py`

`EventDefinition` is the versioned registry for accepted Turn, memory recall/store, provider call, tool call, delivery, and terminal events. Definitions own a stage and required attributes; unknown or incomplete semantic records fail validation. Old `RuntimeEvent` construction remains readable without a TraceContext, while every newly accepted `TurnRequest` receives an immutable context containing `trace_id`, `span_id`, optional parent span, stage, and format version.

The same trace ID now crosses scheduler admission, runtime execution, memory recall, provider accounting, tool execution, streamed delivery, and terminal Turn persistence. The async context is copied into the AgentLoop worker thread. Provider calls create child spans and retain Turn, request, session, and provider-call IDs. Tool and memory runtime rows retain the root correlation and explicit stages. Delivery chunks and terminal events remain on the same lineage. Context is reset after execution so unrelated requests cannot inherit it.

Every call-ledger row contains its trace/span lineage, provider-call ID, Turn/request/session IDs, usage provenance, pricing snapshot, and estimated cost status. Final reports list provider-call IDs beside aggregate usage and `call_efficiency`, so a Turn-level cost can be traced back to the exact calls rather than inferred from a global counter.

## TECH-039 - Redacted Trace Storage and Self-contained Evidence

- Plan items: `P5-03`, `P5-04`, `P5-06`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/run_store.py`, `repoagent/evidence.py`
- Tests: `tests/test_tracing.py`, `tests/test_run_store.py`

`RunStore` is now the final redaction seam for task state, runtime trace, model-call ledger, report, Turn snapshot, and Turn events. Runtime callers may still redact defensively, but persistence applies the configured redactor again, preventing a new caller from accidentally publishing configured secret values.

Read APIs support filtered runtime-trace queries by event, stage, provider-call ID, tool-call ID, and tail limit; Turn-event queries filter by semantic kind and stage. Structured export returns Turn events, runtime rows, and model calls in memory without embedding host paths. Retention removes only terminal run directories, protects explicit IDs, preserves the newest configured count, and never deletes an accepted/running Turn.

`EvidenceBundleBuilder` requires a terminal Turn event before publication. It copies the available Turn snapshot/events, task state, runtime trace, call ledger, and report into a new directory. Its manifest uses bundle-relative safe paths and records SHA-256 plus byte size for every artifact; it contains no temporary source path. Verification rejects missing, path-traversing, size-mismatched, or checksum-mismatched artifacts. The manifest itself identifies the run, trace, terminal kind, and Turn-event count.

## TECH-040 - Trace Overhead Experiment and Local Inspector

- Plan items: `P5-07`, `P5-08`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/evaluation/tracing.py`, `repoagent/trace_inspection.py`
- Entry points: `scripts/run_tracing_experiment.py`, `repoagent-trace`
- Tests: `tests/test_tracing.py`

The tracing microbenchmark separately times deterministic JSON serialization and the actual RunStore append path, reporting median/P95 milliseconds, total bytes, and bytes per event under an explicit event count and payload size. A 200-event local run with 128 content characters observed a `1.743 ms` append median, `2.403 ms` P95, and `188.45 bytes/event`. These are local filesystem measurements, not cross-machine latency claims.

`repoagent-trace` is a read-only local command that loads one RunStore root and run ID, prints a compact stage/event timeline or structured JSON, filters by event/stage/tail limit, and can emit a verified evidence bundle. A viewer UI is intentionally deferred; P8 will integrate trace inspection into the unified product command tree without changing these storage interfaces.

### P5 Completion Verification

Full verification passed 433 tests. The 12-task scripted harness passed 12/12 with all workspace verifiers and step budgets passing. Ruff, whitespace checks, `uv build`, trace CLI help, the 200-event overhead experiment, and evidence tamper tests passed. Six existing `datetime.utcnow()` deprecation warnings remain in the legacy metrics writer.

## TECH-041 - Unified Evaluation Result and Isolated Trials

- Plan items: `P6-01`, `P6-02`, `P6-03`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/evaluation/schema.py`, `repoagent/evaluation/workspace.py`
- Tests: `tests/test_evaluation_platform.py`

`repoagent.evaluation-result/v1` is the single result envelope for all new campaigns. It requires non-empty experiment, source, environment, benchmark, model, and design sections; raw rows have a unique `(task_id, variant, repetition)` identity and explicit `pass`, `fail`, `error`, or `skipped` status. `model.run_kind` must be `scripted`, `synthetic`, or `live`, preventing generated and real-provider evidence from sharing an ambiguous schema. Validation independently recomputes unique-task `effective_n` and total `run_n` from raw rows.

Source provenance records exact commit, branch, dirty state, and a SHA-256 over tracked index state, working diff, and untracked content. Environment provenance records Python implementation/version, OS/release/architecture, locale, executable identity, and an available lock digest. Benchmark identity includes definition digest, version, and unique task count; model and experimental design remain explicit rather than inferred from filenames.

`TrialWorkspace` creates one fresh fixture copy per task, variant, and repetition and refuses identity reuse. `RawRowWriter` appends validated rows immediately, so a later campaign crash does not erase completed observations. Result publication uses atomic replacement.

## TECH-042 - Runtime, Paired, and Statistical Campaigns

- Plan items: `P6-04`, `P6-05`, `P6-06`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/evaluation/campaigns.py`, `repoagent/evaluation/statistics.py`
- Tests: `tests/test_evaluation_platform.py`, `tests/test_paired_evaluation.py`

`RuntimeContractCampaign` runs every deterministic fixture in a retained isolated workspace, persists a raw row, builds one checksummed Turn evidence bundle per task, and emits the unified result. Missing terminal evidence fails bundle creation. The campaign reports pass numerator/denominator and a Wilson 95% interval; it labels itself scripted and explicitly limits the claim to runtime contracts.

The paired campaign adapter converts answer-isolated grader output into the same schema. `paired_campaign_matrix` requires all four declared dimensions: context, memory, cost, and recovery. It reports unique tasks separately from runs and pairs, paired win/tie/loss and mean/median deltas, deterministic paired-bootstrap intervals, and exact two-sided McNemar results for pass/fail discordance. Repetitions remain visible raw rows and do not inflate effective N.

## TECH-043 - Fault Injection, SWE-bench, and Red-team Evaluation

- Plan items: `P6-07`, `P6-08`, `P6-09`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/evaluation/faults.py`, `repoagent/evaluation/swebench.py`, `repoagent/evaluation/red_team.py`
- Tests: `tests/test_evaluation_platform.py`, `tests/test_security_adversarial.py`

Fault plans target an exact occurrence at model, tool, persistence, or cancellation boundaries. Provider, ToolGateway, and persistence proxies expose injection without placing test branches in production implementations. The matrix records every boundary, including probe errors and faults that failed to trigger, then converts those rows into the unified schema with a strict all-detected gate.

`SWEBenchAdapter` follows the official SWE-bench dataset fields: instance ID, repo, base commit, problem statement, patch, test patch, FAIL_TO_PASS, and PASS_TO_PASS. Runner input includes only repository coordinates and the problem statement. Gold patch, test patch, and test oracle remain in a separate grader payload; predictions use the official instance/model/model-patch shape. The adapter consumes local JSON/JSONL and does not silently download data or execute Docker. Dataset reference: [SWE-bench dataset guide](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/datasets.md).

Red-team cases cover prompt injection, forbidden-tool execution, secret canary exfiltration, and workspace/policy bypass. The runner receives no grader canary configuration beyond the public task input. Tool records and changed paths are graded structurally. Runner exceptions remain separate `error` rows and fail the release gate rather than disappearing from the denominator or being mislabeled as successful attacks.

## TECH-044 - Evaluation Comparison and Release Evidence

- Plan items: `P6-10`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/evaluation/release.py`, `repoagent/evaluation/cli.py`
- Entry point: `repoagent-eval`
- Tests: `tests/test_evaluation_platform.py`

Baseline comparison requires the same benchmark definition, run kind, and raw row identities. It reports pass-rate delta and exact per-row regressions/improvements; the default gate permits no pass-rate drop and no passing-row regression. This prevents aggregate improvements from hiding a broken task.

Release bundling requires an exact commit and, by default, a clean source tree. Every row must reference a safe relative evidence directory. Results and evidence are copied into a self-contained directory with SHA-256 records; host and temporary paths are not published. Dirty bundles are possible only through an explicit local-development override and are not release eligible.

`repoagent-eval` exposes `contract`, `validate`, `compare`, and `release` commands. P8 may route them through the unified product CLI without changing the evaluation interfaces.

### P6 Completion Verification

Full verification passed 444 tests. A fresh unified runtime-contract campaign passed 12/12 tasks and produced 12 raw rows plus 12 checksummed evidence bundles. Its observed pass rate was 100% with a Wilson 95% interval of approximately `[75.75%, 100%]`; this remains a scripted contract result. `repoagent-eval validate --require-evidence`, same-result baseline comparison, explicit dirty-local release smoke, Ruff, whitespace checks, and `uv build` passed. Six existing `datetime.utcnow()` deprecation warnings remain in the legacy metrics writer.

## TECH-045 - Bounded and Isolated Subagent Runtime

- Plan items: `P7-01`, `P7-02`, `P7-03`, `P7-04`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/subagents.py`, `repoagent/runtime.py`
- Tests: `tests/test_subagents_routing_plugins.py`

Subagent requests bind a child ID to the parent Turn, request, and session; declare a role, unique tool allowlist, message sequence, and positive step/token/time budget; and produce a typed completed, failed, or cancelled outcome. Requested budgets are attenuated against the parent's remaining step, context-window, output, and deadline limits. The active parent cancellation token and deadline are passed into the child Agent loop, and a cancellation regression verifies convergence while a child provider call is blocked.

Each child receives a temporary repository snapshot with `.git` and `.repoagent` excluded, its own session and Run stores, an attenuated capability token, and only role-approved tools. Child writes therefore do not mutate the parent workspace. The parent Turn retains the request/outcome record, usage and call-efficiency totals, semantic start/completion events, and checksummed child task-state/trace/call/report evidence under `subagents/<child-id>`.

## TECH-046 - Deterministic and Explainable Model Routing

- Plan items: `P7-05`, `P7-06`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/routing.py`, `repoagent/agent_loop.py`
- Tests: `tests/test_subagents_routing_plugins.py`

Routing profiles declare ordered provider fallback chains plus optional role, category, keyword, and capability selectors. Selection uses a deterministic score and stable tie-break; unmatched specialized profiles are ineligible and a named default profile handles ordinary work. Every terminal model result carries the selected profile, candidate scores, reasons, primary provider, and fallback providers, which the Agent loop persists as a `model_routed` semantic event. Provider failure behavior remains owned by the typed fallback adapter rather than duplicated in routing policy.

## TECH-047 - Trust-gated Declarative Plugins

- Plan items: `P7-07`, `P7-08`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/plugins.py`, `repoagent/runtime.py`, `repoagent/tool_gateway.py`
- Tests: `tests/test_subagents_routing_plugins.py`

Plugins are discovered from versioned JSON manifests and move through discovered, blocked/disabled, registered, active, and stopped lifecycle states. Trust comes from an external host trust store; a manifest cannot approve itself. Manifests cannot import Python or name arbitrary entry points: each tool must reference a host-registered runner ID, and duplicate plugin IDs, tool collisions, missing runners, or invalid schemas fail closed.

Approved tools join the same registry before capability issuance and execute only through ToolGateway. This preserves argument validation, effect-aware approval, capability checks, deadlines, output limits, isolation requirements, evidence, and post-execution secret redaction. Plugin tools can be selected by the runtime allowlist, but cannot mint capabilities or access the Agent object and process environment through the plugin contract.

## TECH-048 - Coding Roles and Paired Complement Gate

- Plan items: `P7-09`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/subagents.py`, `repoagent/evaluation/subagents.py`
- Tests: `tests/test_subagents_routing_plugins.py`

The runtime exposes three bounded role profiles: an isolated write-capable implementer, a read-only reviewer, and a read-only red-team verifier. Tool sets and approval policy follow the role contract rather than free-form model requests. `SubagentRoleEvaluator` compares separate single-agent and role-team runners on the same task/repetition pairs with an answer-isolated grader. Its gate requires at least one role-team paired win without reducing total passes, so merely adding more calls cannot satisfy the specialization claim.

### P7 Completion Verification

Full verification passed 451 tests. Focused contract, isolation, cancellation, routing, plugin-security, and paired-role tests passed. A fresh unified runtime-contract campaign passed 12/12 tasks and its evidence-aware validator accepted all retained bundles. Ruff, `git diff --check`, and `uv build` passed. Six existing `datetime.utcnow()` deprecation warnings remain in the legacy metrics writer.

## TECH-049 - Parser-independent Assembly and Product Commands

- Plan items: `P8-01`, `P8-02`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/runtime_assembly.py`, `repoagent/cli.py`, `repoagent/product_commands.py`
- Tests: `tests/test_product_cli.py`, `tests/test_public_api_contract.py`

`RuntimeAssembly` owns workspace/config loading, model and secret resolution inputs, Session/Run stores, incomplete-Turn recovery, resume selection, and the final RepoAgent object graph. The CLI keeps a compatibility `build_agent` facade but no longer constructs runtime storage and lifecycle objects itself. Existing arbitrary one-shot prompts and the REPL remain valid; reserved operational names enter a separate product parser.

The unified command tree exposes structured doctor, provider, session, sandbox, trace, eval, skill, gateway, directory-channel, and cron inspection. Provider output reports whether a credential is configured without returning its value. Session and Skill commands return metadata rather than history/body content. The existing trace and evaluation CLIs are routed through this tree instead of reimplemented.

## TECH-050 - Scheduler-backed RuntimeHost and TUI Transport

- Plan items: `P8-03`, `P8-09`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/runtime_host.py`, `repoagent/tui.py`
- Tests: `tests/test_product_transports.py`

`RuntimeHost` is the shared ingress for interactive and background product surfaces. It converts normalized messages to the existing `TurnRequest`, submits only through `Scheduler` and `TurnRuntime`, retains handles for cancellation/waiting, publishes accepted and terminal events to per-session subscribers, and optionally delivers the final answer through a channel outlet. Stable channel/message IDs form the accepted-work dedup key.

`TUITransport` provides send, subscribe/unsubscribe, cancel, and confirmation-response operations without owning Agent semantics. `ConfirmationBroker` bounds pending confirmations by timeout. The runnable terminal loop uses this transport and RuntimeHost rather than calling the model loop directly. The original CLI also continues to enter the same Turn/Scheduler runtime through `RepoAgent.ask_async`.

## TECH-051 - Local Gateway, Channel Boundary, and Media

- Plan items: `P8-04`, `P8-05`, `P8-06`, `P8-07`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/gateway.py`, `repoagent/channels.py`
- Tests: `tests/test_product_transports.py`

The local gateway owns RuntimeHost and channel lifecycle. A filesystem lease directory is acquired atomically, records PID/owner/start time, rejects a live second instance, recovers a stale owner, and exposes a zero-network health snapshot. Shutdown seals intake before stopping channels and draining the runtime.

Channel intake is deny-by-default, validates channel identity, and submits immutable `ChannelMessage` objects. Delivery is an explicit asynchronous outlet and records success or failure in the terminal subscription event. `DirectoryChannel` is a usable SDK-free adapter: JSON inbox messages enter the runtime, processed input is retained, and replies are atomically written to an outbox. Duplicate platform message IDs resolve to the existing Turn and do not execute or deliver twice.

Media persistence strips server-supplied path components, content-addresses filenames, applies a size ceiling, and returns digest/MIME/kind metadata. Audio normalization supports an injected asynchronous transcriber; absence is represented as `not_configured` rather than silently dropping audio.

## TECH-052 - Persistent Cron Claims and Delivery Outcomes

- Plan items: `P8-08`, `P8-09`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/cron.py`
- Tests: `tests/test_cron.py`

Cron supports one-shot timestamps and fixed intervals. The versioned JSON store uses the shared cross-platform file lock and atomic replacement. Add deduplication hashes schedule, prompt, channel, and destination; multiple processes therefore converge on one enabled job. Services detect store mtime changes and reload without restart.

Due jobs are atomically claimed with an owner and expiry. A second worker cannot claim a live lease, while an expired claim is recoverable. Execution enters RuntimeHost as background work, final output uses the configured channel delivery surface, and completion persists Turn ID, terminal status/error, run count, and the next schedule. One-shot jobs disable after completion; recurring jobs advance beyond the current clock to avoid replay storms.

### P8 Completion Verification

Full verification passed 466 tests. The TUI-to-Scheduler-to-ToolGateway cancellation path terminated a real slow shell and persisted the `tool_cancelled` state; the underlying cancellation regression also passed five consecutive reruns. A fresh unified runtime-contract campaign and evidence validation passed 12/12 tasks. Doctor, provider, session, sandbox, trace, eval, skill, gateway, channel, cron, and TUI command smokes passed, as did Ruff, `git diff --check`, and `uv build`. Six existing `datetime.utcnow()` deprecation warnings remain in the legacy metrics writer.

## TECH-053 - Bounded Evolution Candidate Contract

- Plan items: `P9-01`, `P9-02`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/evolver/contracts.py`, `repoagent/evolver/generator.py`
- Tests: `tests/test_evolver_contracts.py`

Evolution candidates are immutable manifests bound to an exact base commit, typed mutation label, explicit failure-evidence digests, before/after content digests, a patch digest, and file/byte/trial/cost budgets. Prompt, Skill, tool-policy, and routing mutations each have a narrow allowlist; runtime state, Git metadata, and sealed paths are denied independently of the model proposal. Generation accepts only registered host strategies and explicit failure evidence. The conservative changed-byte charge uses the larger before/after content size, so the budget cannot be understated by replacing a large file with a small one.

## TECH-054 - Detached Candidate and Sealed Evaluation Isolation

- Plan items: `P9-03`, `P9-04`, `P9-10`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/evolver/workspace.py`, `repoagent/evolver/sealed.py`
- Tests: `tests/test_evolver_contracts.py`

Every candidate is applied to a temporary detached Git worktree created from the manifest's full commit SHA. Baseline and output digests are recomputed, symlinked mutation targets are rejected, and finalization returns immutable commit/tree identity while leaving the parent checkout unchanged. Training and sealed task IDs must be disjoint. Sealed scoring accepts only an explicitly isolated backend, rejects storage/workspace overlap and unsafe candidate IDs, stores raw grader output inside the vault, and exposes only a blind receipt until evolution is explicitly finished. The candidate contract contains neither grader path nor grader implementation; this is a local isolation boundary, not a claim of hostile multi-tenant containment.

## TECH-055 - Deterministic and Paired Promotion Gates

- Plan items: `P9-05`, `P9-06`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/evolver/gates.py`
- Tests: `tests/test_evolver_gates.py`

Cheap gates first recompute the base identity and mutation content, then run injected deterministic checks with exceptions converted to failed evidence rather than terminating the controller. Expensive promotion uses unique task/repetition pairs, minimum effective task and repetition thresholds, quality non-inferiority, positive mean lift with more wins than losses, optional paired-bootstrap lower bounds, and trial/cost ceilings. Reports retain paired win/tie/loss, bootstrap interval, and exact McNemar statistics. A termination tracker stops on round budget, consecutive evaluator errors, or promotion patience. These mechanisms define a promotion protocol; they do not establish model improvement until populated with representative held-out tasks.

## TECH-056 - Tamper-evident Human-approved Activation

- Plan items: `P9-07`, `P9-08`, `P9-09`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/evolver/ledger.py`, `repoagent/evolver/activation.py`, `repoagent/evolver/orchestrator.py`
- Tests: `tests/test_evolver_activation.py`

Candidate creation, materialization, gate decisions, approval requests/confirmations, activation, and rollback are append-only JSONL events protected by a sequence-numbered SHA-256 hash chain and cross-platform file lock. Approval uses a random one-time token whose digest, candidate, and paired-evidence digest are recorded; the raw token is never persisted. Activation accepts no caller-provided `approved` flag: it replays the ledger and requires matching manifest/materialization identity, latest passing deterministic and paired decisions, and a human confirmation bound to the latest paired evidence. Routing is reconstructed from activation events, while rollback appends a restoration event referencing the previous activation and never rewrites history.

## TECH-057 - Evolver Operational Inspection

- Plan items: `P9-09`
- Status: implemented
- Implemented: 2026-08-25
- Owning modules: `repoagent/product_commands.py`, `repoagent/cli.py`
- Tests: `tests/test_product_cli.py`

`repoagent evolver status` verifies the workspace evolution ledger and reports event count plus active prompt, Skill, tool-policy, and routing candidate identities. It is deliberately read-only: candidate generation, human token handling, and activation remain explicit library operations so an unattended product command cannot silently promote its own policy.

### P9 Completion Verification

Full verification passed 481 tests. Focused candidate provenance, detached-worktree isolation, sealed-boundary, deterministic/paired gate, ledger-tamper, human-approval, activation, rollback, and product-status tests passed 25/25. Ruff passed for all P9 modules and touched integration files. A fresh scripted runtime-contract campaign passed 12/12 tasks and its retained evidence bundles validated successfully. Six existing `datetime.utcnow()` deprecation warnings remain in the legacy metrics writer.

## TECH-058 - Reproducible Package and CI Matrix

- Plan items: `P10-01`, `P10-02`
- Status: implemented
- Implemented: 2026-08-25
- Owning files: `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `repoagent/release_check.py`
- Tests: `tests/test_release_hardening.py`

`uv.lock` is tracked and frozen installation is the CI contract. Pull requests run Ruff and the full suite on Python 3.10, 3.11, and 3.12 across Ubuntu and Windows. A separate package job builds the sdist/wheel, installs the wheel into a fresh environment, and smokes the Agent, evaluation, release-check, and offline-demo entry points. The release preflight requires a clean exact HEAD, tracked lock, and a `vX.Y.Z` tag matching the package version and resolving to HEAD. Python 3.10 receives `tomli` through an environment marker while newer interpreters use the standard library parser.

## TECH-059 - Migration and Security Contract

- Plan items: `P10-03`, `P10-04`
- Status: implemented
- Implemented: 2026-08-25
- Owning files: `docs/migration.md`, `docs/security/threat-model.md`, `SECURITY.md`

The migration guide documents legacy `.pico` state selection, explicit stop/backup/rename/verify steps, `PICO_*` fallback configuration, current schema versions, and fail-closed behavior. It forbids merging two live state roots because their revisions, event sequences, leases, and hash chains are concurrency-sensitive. The threat model identifies repository data, credentials, execution authority, evidence, sealed graders, and activation state as assets; separates untrusted model/repository/plugin/channel inputs from host policy; maps threats to controls; and explicitly states that direct-host execution, hostile same-account tenants, provider retention, and semantic prompt injection remain outside guaranteed containment. `SECURITY.md` defines private reporting and safe reproduction expectations.

## TECH-060 - Tagged Evidence and Release-only Claims

- Plan items: `P10-05`, `P10-07`
- Status: published and independently verified
- Implemented: 2026-08-25
- Owning modules: `repoagent/evaluation/release.py`, `repoagent/evaluation/resume.py`, `scripts/collect_resume_metrics.py`
- Tests: `tests/test_release_hardening.py`

A tagged bundle must bind a syntactically valid release tag to the evaluation commit and current clean repository HEAD. It includes results, the exact benchmark definition, every per-task evidence file, and a manifest containing tag, commit, benchmark digest, run kind, denominators, file sizes, and SHA-256 values. Verification recomputes every checksum, validates the unified result, verifies nested Turn evidence manifests, and rejects dirty or mismatched provenance. Tagged publication cannot use the development-only dirty override.

The former resume collector no longer accepts arbitrary benchmark/run paths. It consumes only a verified tagged bundle and emits workload ID/digest, unique-task and run denominators, model/run kind, commit/tag, metrics, and limitations together. This prevents a synthetic local run from silently becoming a public claim.

Annotated tag `v0.1.1` resolves to commit `49e016a4c27361ff5f7613edd723620d730da837`. GitHub Actions run `32827010946` completed successfully and published artifact `repoagent-v0.1.1`. The downloaded bundle was independently passed through `verify_release_bundle(require_tag=True)`: it reported schema `repoagent.evaluation-release/v1`, 12 unique tasks, 12 runs, 86 checksummed payload files, and benchmark digest `sha256:18cc5ae299ddb56e08d3d426b320e5ba20bfe6efd3f54f7ac107ff252bb306e7`.

## TECH-061 - Current Architecture and Offline Demo

- Plan items: `P10-06`
- Status: implemented
- Implemented: 2026-08-25
- Owning files: `docs/architecture/current-architecture.md`, `repoagent/offline_demo.py`, `README.md`
- Tests: `tests/test_release_hardening.py`

The current architecture document presents the unified ingress, Scheduler/Turn runtime, Agent loop, provider/context/tool boundaries, evidence flow, and controlled Evolver without requiring readers to reconstruct nine implementation phases. `repoagent-demo` runs the 12-task deterministic contract suite without credentials and verifies every generated evidence bundle before reporting success. Its output carries an explicit limitation that this is runtime-contract evidence rather than competitive coding quality.

### P10 Release Readiness Verification

Full local verification passed 488 tests. The final main-branch CI run `32825640141` passed Ruff and the full suite on Python 3.10, 3.11, and 3.12 across Ubuntu and Windows, plus the independent wheel-install smoke job. Release run `32827010946` repeated the clean-tag preflight, Ruff, full tests, build, 12-task offline contract campaign, bundle construction, and release-only claim generation. The downloaded artifact was reverified against all manifest checksums. Six existing `datetime.utcnow()` deprecation warnings remain in the legacy metrics writer.

## TECH-062 - Aider Polyglot Dataset and Canary Contract

- Plan items: `P11-01`, `P11-02`
- Status: implemented adapter and inspection plan; no benchmark execution claim
- Implemented: 2026-08-25
- Owning modules: `repoagent/evaluation/polyglot.py`, `repoagent/evaluation/cli.py`
- Tests: `tests/test_polyglot_evaluation.py`

`PolyglotAdapter` consumes a caller-supplied checkout of the official
`Aider-AI/polyglot-benchmark` layout. It validates every declared relative path,
loads introduction/instruction/append text, exposes only editable solution files
to `PolyglotRunnerInput`, and retains test files, example/reference files, test
commands, and the exercise root behind `PolyglotInstance.grader_payload()`.
This is an information-flow boundary for the runner API, not protection against
a model that can bypass RepoAgent and inspect the host filesystem.

Selection is sorted and round-robin across the canonical C++, Go, Java,
JavaScript, Python, and Rust order. A 24-task canary therefore selects four per
language when the official corpus is complete, without randomness or
cherry-picking after results are known. The plan records the complete dataset
digest, source commit and dirty state when available, complete-corpus and
selected per-language denominators, selected task IDs, solution paths,
instruction digests, and fixture digests. It contains no
test path, test content, example path, reference content, or grader command.

`repoagent-eval polyglot-plan` only writes this frozen inspection artifact and
marks execution `not_run` plus `requires_isolation=true`. The official Aider
harness warns that generated solutions are untrusted and runs them in Docker;
RepoAgent follows that threat boundary. P11-03 must supply a concrete isolated
container backend before P11-04 is allowed to execute or grade a task.

Local verification is also retained by `scripts/record_verification.py`. Its
non-publishable bundle stores pytest JUnit, per-command stdout/stderr, duration,
exit status, source/environment provenance, and checksums, plus the frozen
Polyglot plan when a dataset checkout is supplied. Failed commands remain in the
bundle instead of disappearing from the denominator. These ignored local
artifacts are debugging and claim-preparation inputs; only clean-tag release
evidence may generate resume claims.

## TECH-063 - Isolated Polyglot Grading Boundary

- Plan item: `P11-03`
- Status: implemented and locally container-probed
- Implemented: 2026-08-25
- Owning modules: `repoagent/evaluation/container.py`, `repoagent/evaluation/polyglot.py`
- Tests: `tests/test_evaluation_container.py`, `tests/test_polyglot_evaluation.py`

`DockerContainerRunner` copies a benchmark grading workspace into a disposable
Docker-accessible staging directory and never bind-mounts the source repository.
Git metadata, RepoAgent/Pico state, environment files, prior artifacts, dependency
trees, and build targets are excluded. Symlinked inputs fail closed. Containers
run without network, Linux capabilities, or privilege escalation, with a read-only
root filesystem, bounded temporary storage, CPU, memory, PID, time, and retained
output. A unique named container is force-removed after success, failure,
timeout, or cancellation. Container writes affect only the disposable staging
copy and are never synchronized to the source workspace.

`prepare_polyglot_runner_workspace` physically copies only declared editable
solution files. Tests, `.meta` references, grader commands, and build support are
absent rather than merely hidden in a prompt. `PolyglotContainerGrader` later
recreates a private grading workspace from the frozen source exercise, overlays
only those produced solution files, and refuses any backend lacking an explicit
isolation claim. A real Docker Desktop 29.6.2 probe executed an Alpine container
with the production flags and returned `grader-container-ok`. This proves the
container boundary and grading path, not model coding quality.

`polyglot_workspace_context` additionally fixes both `cwd` and `repo_root` to
the isolated trial directory. This is mandatory even when a trial is stored
under another Git checkout: ordinary workspace auto-discovery would otherwise
walk upward and grant file-tool authority over the parent repository.

## TECH-064 - Single-task Polyglot Runtime Smoke

- Plan item: `P11-04`
- Status: scripted runtime/grader smoke passed; live model not run
- Implemented: 2026-08-25
- Evidence: local ignored bundle `artifacts/polyglot-smoke/scripted-affine-20260825-03`

The official `python/affine-cipher` fixture was reduced to its declared solution
file, passed through a normal RepoAgent Turn with a scripted `FakeModelClient`,
modified through the public `write_file` ToolGateway path, and graded against
the restored hidden tests inside the pinned local Python grader image. The
container collected 16 tests and passed 16/16 in 1.575 seconds. The evidence
bundle retains the Turn state/events, trace, call ledger, report, generated
patch, raw pytest output, fixture digest, image ID, source provenance, and file
checksums. It declares `run_kind=scripted` and `coding_quality_claim=false`.

Two failed precursors were retained locally. The first revealed that container-
created pytest cache files on a Windows staging mount could not be removed by
WSL; container-side staging scrubbing now runs on every exit. The second revealed
that ordinary Git root auto-discovery could escape a nested trial and write the
parent RepoAgent checkout; the mandatory explicit trial-root context fixed that
authority bug. Neither failed run is counted as a benchmark pass. No provider
credential or local Ollama service was available, so this result is not evidence
of real-model Polyglot performance.

## TECH-065 - Live Polyglot Result Semantics and Provider Findings

- Plan item: `P11-05` (partial)
- Status: single-task campaign implemented; live convergence gate failing
- Implemented/tested: 2026-08-25
- Owning modules: `repoagent/evaluation/polyglot_campaign.py`, `scripts/run_polyglot_task.py`, `repoagent/providers/clients.py`
- Dependency: `json-repair>=0.61.7,<1` (locked by `uv.lock`)
- Tests: `tests/test_polyglot_evaluation.py`, `tests/test_provider_runtime.py`
- Evidence: local ignored bundles under `artifacts/polyglot-live/`

`PolyglotSingleTaskCampaign` retains the generated patch, isolated grader output,
Turn evidence bundle, raw row, normalized result, usage, call-efficiency, latency,
failure category, and SHA-256 references. A campaign pass now requires both hidden
tests and `final_answer_returned`; `code_passed` and `turn_converged` remain separate
so a correct patch cannot hide a failed runtime lifecycle.

Three DeepSeek runs generated independent implementations of
`python/affine-cipher` that each passed all 16 hidden tests. They did not count as
complete campaign passes: with a 3,000-token input budget the model repeatedly
read the completed file until `step_limit_reached`. Trace evidence showed the
mandatory checkpoint consuming about 1,170 tokens and squeezing the runtime
prefix from 900 tokens to roughly 258-294 tokens in later rounds. A subsequent
8,000-token diagnostic failed before grading because the Anthropic-compatible
stream contained malformed native-tool argument JSON. This is useful failure
evidence, not a coding-quality score or resume claim. Further paid runs are
blocked until protocol recovery and convergence regressions are fixed offline.
The provider boundary now tries `json_repair` after strict JSON decoding fails,
while still requiring an object
and passing repaired arguments through the normal Tool Schema and policy gates.
Offline stream regressions cover a repairable trailing comma and rejection of a
repaired non-object payload; another paid run is still required to verify the
exact live failure no longer aborts the Turn.

The provider parsing order is deliberately bounded:

1. Already-decoded mapping arguments are accepted without rewriting.
2. String arguments first use standard-library `json.loads()` so valid provider
   output retains strict JSON semantics.
3. Only a `JSONDecodeError` activates `json_repair.loads()`; transport failures
   and non-string payloads are not disguised as repairable JSON.
4. The decoded value must still be an object. Arrays, scalars, and null remain
   protocol errors even when the repair library can parse them.
5. The resulting object enters the existing `ToolDefinition` argument Schema,
   effect approval, capability, sandbox, and execution gates. JSON repair does
   not authorize a tool, add missing business arguments, or bypass policy.

The focused provider tests construct Anthropic-compatible SSE
`input_json_delta` streams. One verifies that `{"path":"README.md",}` becomes
the expected `read_file` argument object; another verifies that a repairable
array is rejected before it can reach ToolGateway. The full suite after this
change initially passed 506 tests; after removing the temporary project-specific
terminal tool, the provider/runtime/checkpoint/Polyglot focused regression passed
117 tests. Six pre-existing `datetime.utcnow()` deprecation warnings remain. No
additional paid provider call was made after adding the repair path, so live
recovery remains unverified and is not counted as a pass.

Tool-loop exhaustion now uses one tools-disabled synthesis request while the Turn
remains interrupted through
`TaskState(status=stopped, stop_reason=step_limit_reached)`; see TECH-066.

## TECH-066 - Tools-disabled Exhaustion Synthesis

- Area: Agent-loop max-iteration exhaustion
- Status: implemented and regression-tested
- Implemented/tested: 2026-08-26
- Owning module: `repoagent/agent_loop.py`
- Tests: `tests/test_repoagent.py`

When the normal tool budget is exhausted, AgentLoop builds one final request from
the retained context plus a bounded synthesis instruction and sends it with an
empty tool tuple. The instruction requires a concise summary of completed,
verified, and unfinished work in the user's language. The synthesis request is
counted as another model attempt, enters `calls.jsonl`, contributes to aggregate
usage and cost completeness, and emits explicit requested/completed/failed trace
events.

A useful synthesis answer is persisted to session history and delivered to the
caller, but it does not convert the Turn into success. TaskState remains stopped
with `step_limit_reached`, so evaluation continues to report
`turn_converged=false`. Provider cancellation still propagates. Provider failure,
an empty response, or an unexpected tool call falls back to a deterministic
message without discarding prior workspace changes or run evidence. No custom
terminal tool is exposed to the model.

Focused Provider/Agent/Polyglot tests passed 111 cases before the full suite
passed 507 tests. Ruff and whitespace validation passed; six legacy
`datetime.utcnow()` deprecation warnings remain.

## TECH-067 - Fail-closed Provider Campaign Preflight

- Area: live Provider campaign admission
- Status: first strict gate implemented; immutable campaign binding pending
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/evaluation/provider_probe.py`, `repoagent/evaluation/polyglot_campaign.py`
- Tests: `tests/test_provider_probe.py`, `tests/test_polyglot_evaluation.py`

Live Polyglot execution now performs a bounded native Tool Calling probe before
the Agent or hidden grader can run. The probe requires the exact echo call,
stable Provider and model identities, actual usage accounting with all normalized
token fields, a frozen tokenizer identity, an explicit pricing source, and no
fallback model. A failure creates a checksummed `provider-preflight.json`, an
explicit `provider_preflight_failed` row category, and a failed result gate;
Agent execution and grading remain `not_run`.

The first gate does not yet bind an immutable preflight cache to commit, suite,
approval, and runtime configuration digests; scope an independent logical-call
budget and timeout; or recheck clean-worktree identity after preflight. Those
invariants remain required before a multi-task paid campaign. Focused Provider
probe and Polyglot campaign tests passed 18 cases.

## TECH-068 - Native Tool Transcript Continuity

- Area: Provider-neutral assistant/tool message protocol
- Status: current-Turn continuity implemented; cross-Turn structured replay pending
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/providers/base.py`, `repoagent/providers/clients.py`, `repoagent/agent_loop.py`
- Tests: `tests/test_provider_runtime.py`, `tests/test_repoagent.py`, `tests/test_usage_accounting.py`

`ModelRequest` can now carry validated `ModelMessage` values in addition to its
compatibility prompt. During a native Tool Calling Turn, AgentLoop retains the
initial assembled request, the assistant message with exact Tool Call IDs and
arguments, and one matched result per executed call. Tool outputs are enclosed
in nonce-paired untrusted-data boundaries before being returned to the model.
The raw execution result remains available to the existing session, trace, and
evidence paths.

Anthropic-compatible projection emits assistant `tool_use` blocks and coalesced
user `tool_result` blocks; consecutive user blocks are merged so a tools-disabled
exhaustion instruction remains protocol-valid. OpenAI Responses projection emits
`function_call` and matching `function_call_output` input items. The existing
plain-prompt path remains for text-only and legacy clients.

This implementation currently preserves structured continuity inside one Turn.
A later Turn still receives deterministically compacted text history rather than
a reconstructed native transcript, and context token admission is still based
on the compatibility prompt rather than the final Provider-specific projection.
Those limitations must be addressed before claiming complete message-protocol
parity or measuring context savings. Provider, AgentLoop, accounting, preflight,
and Polyglot focused tests passed 128 cases; an additional real-adapter preflight
contract verifies that configured DeepSeek identity survives Anthropic-compatible
normalization. The complete suite collected and passed 518 tests. Ruff, the
Polyglot CLI help smoke, and `git diff --check` passed.

## TECH-069 - Bounded Empty-response Recovery

- Area: AgentLoop empty and thinking-only response handling
- Status: implemented for generic and Anthropic-compatible model results
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/empty_recovery.py`, `repoagent/agent_loop.py`, `repoagent/providers/base.py`, `repoagent/providers/clients.py`
- Tests: `tests/test_empty_recovery.py`, `tests/test_provider_runtime.py`, `tests/test_repoagent.py`

Empty-response handling is now a pure, independently tested decision policy with
per-Turn counters. A thinking-only response is replayed at most twice, an empty
response immediately following Tool Calls receives one user nudge, and an
ordinary empty response is retried at most three times. Thinking recovery takes
priority over the post-Tool nudge; after prefill exhaustion, the plain retry
budget remains available. Recovery can be disabled through a validated
`RecoveryLimits` value on `RepoAgent`.

Recovery messages exist only in the current Provider transcript. Reasoning
replay, the synthetic `(empty)` assistant message, and the post-Tool user nudge
are never appended to persisted session history. Each action and exhausted
budget emits an explicit trace event. Persistent empty output returns a bounded
fallback after four total calls under the default policy rather than consuming
the general malformed-response limit.

`ModelResult` now retains structured reasoning text and immutable thinking
blocks. The Anthropic-compatible SSE adapter collects `thinking_delta` and
`signature_delta`, making real structured thinking available to the same
recovery classifier used by scripted Providers. OpenAI Responses reasoning
items are not yet normalized, so that transport currently falls back to plain
empty retry instead of reasoning prefill. Focused recovery, Provider, AgentLoop,
accounting, and preflight tests passed 128 cases.

## TECH-070 - Context-overflow Classification and Recovery

- Area: live Provider context-window overflow
- Status: implemented for structured native transcripts; legacy prompt reduction pending
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/context_overflow.py`, `repoagent/providers/base.py`, `repoagent/providers/clients.py`, `repoagent/providers/fallback.py`, `repoagent/agent_loop.py`
- Tests: `tests/test_context_overflow.py`, `tests/test_provider_runtime.py`

`ProviderError` now carries an independent `should_compress` verdict. HTTP error
classification detects context-length markers before the generic 400 bucket and
marks the call non-retryable and non-fallbackable while allowing transcript
reduction. Fallback chains never switch Provider on an overflow, and a final
overflow after an earlier legitimate fallback preserves the compression verdict
through the exhausted-chain wrapper.

AgentLoop responds by replacing older native Tool-result bodies with a fixed,
content-free placeholder while preserving message order, call IDs, assistant
Tool Calls, and the three most recent complete Tool results. Recovery is limited
to two reductions per Turn and only retries if at least one non-placeholder body
was actually elided. Failed Provider calls remain in call-efficiency and usage
completeness evidence; successful recovery and unrecoverable failure have
separate trace events.

The reducer acts on structured `ModelMessage` transcripts used by the
Anthropic-compatible and OpenAI Responses transports. Ollama and legacy
text-tool clients still rely on a rebuilt compatibility prompt and currently
fail closed when no structured Tool results are available; their deterministic
history compaction needs a separate overflow retry path. Focused overflow,
Provider, AgentLoop, usage, and preflight tests passed 130 cases.

## TECH-071 - Deterministic Tool-failure Loop Break

- Area: repeated Tool failure recovery
- Status: implemented and regression-tested
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/tool_loop_break.py`, `repoagent/agent_loop.py`
- Tests: `tests/test_tool_loop_break.py`

AgentLoop now tracks consecutive deterministic failures by Tool name across
iterations. After two failures in a row, it appends a runtime-owned `[loop]`
instruction requiring the model to stop unchanged retries and reconsider the
path, Tool, command, external dependency, or offline strategy. A fresh streak
may trigger again, but no Turn receives more than two nudges.

Classification uses the structured `ToolResult` status, error code, workspace
effects, and content. Cancellation, timeout, rate limiting, 502/503 responses,
partial success, workspace-changing outcomes, and successful empty searches do
not count as deterministic failures. Unknown Tools, invalid arguments,
non-changing shell failures, structured error payloads, and other stable
rejections do count. This policy complements the existing identical-call
rejection: it also detects a model varying arguments while repeatedly choosing
the same broken Tool.

The nudge is appended after the nonce-bounded untrusted Tool output, persisted
with the matching Tool result, projected into the next Provider request, and
recorded as a trace event with Tool name, call ID, streak, and Turn-level nudge
count. Focused loop-break, Provider, AgentLoop, and usage tests passed 131 cases.

## TECH-072 - Out-of-band Workspace Checkpoints

- Area: per-Turn filesystem recovery and interrupted-work evidence
- Status: implemented for local interactive/always policies
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/workspace_checkpoint.py`, `repoagent/agent_loop.py`, `repoagent/checkpoint.py`, `repoagent/runtime.py`
- Tests: `tests/test_workspace_checkpoint.py`, `tests/test_checkpoint.py`, `tests/test_task_state.py`

RepoAgent now keeps task-semantic JSON checkpoints and filesystem checkpoints as
separate contracts. The filesystem service uses the workspace as a Git work tree
and an isolated Git directory under the selected RepoAgent state root. Its fixed
identity and explicit `--git-dir`/`--work-tree` arguments never update the user's
branch, index, configuration, or commit history.

The runtime establishes a baseline before accepting a Turn, then snapshots the
terminal workspace with `git add -A`. This makes the reported `edited_files`
represent the current Turn even on a new workspace-checkpoint lineage, and it
captures writes, patches, shell-driven edits, renames, and deletions without
depending on a particular Tool implementation. Workspace `.gitignore` rules are
honored and a second exclude layer rejects runtime state, build outputs, virtual
environments, dotenv files, likely credential files, logs, and editor state.

Policy is `always`, `interactive`, or `never`; the CLI default enables snapshots
for persistent interactive sessions and skips them for one-shot requests. A Git
failure or timeout returns `unavailable` evidence and cannot fail the protected
Turn. Successful, unchanged, and unavailable outcomes are distinguished in
`task_state.json`, trace, report, and the semantic checkpoint. When an interrupted
Turn changed files, the next prompt contains the file list and short shadow commit
ID so the model must reason from the retained workspace rather than treating the
previous answer as completion.

Focused checkpoint, TaskState, AgentLoop, and runtime tests passed 93 cases. They
cover first-Turn baselining, user-Git isolation, deletion capture, layered ignore
rules, Unicode and symlink paths, unsafe shadow paths, missing workspaces, missing
or timed-out Git, same-service concurrency, GC configuration/heartbeat,
structured evidence, and next-Turn recovery injection. Cross-process locking and
an operator-facing restore command remain outside this slice. The complete suite
passed 563 tests with six pre-existing metrics deprecation warnings; Ruff, CLI
help/configuration smoke, and whitespace checks passed.

## TECH-073 - Docker Agent Shell Sandbox

- Area: real isolation for model-requested shell execution
- Status: implemented; Linux and Windows-CLI-over-WSL paths verified
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/sandbox.py`, `repoagent/runtime_assembly.py`, `repoagent/cli.py`, `repoagent/product_commands.py`
- Tests: `tests/test_sandbox.py`, `tests/test_product_cli.py`, `tests/test_safety_invariants.py`

The sandbox interface now has a CLI-selectable production backend instead of
only an injection seam. `--sandbox-backend docker` mounts the configured
workspace read-write at `/workspace` so Agent edits persist, while the container
root is read-only, networking is disabled, Linux capabilities are dropped,
`no-new-privileges` is set, PID/CPU/memory limits are enforced, and `/tmp` is a
bounded tmpfs. On POSIX hosts the container runs with the caller's UID/GID to
avoid leaving root-owned workspace files.

Only locale/terminal variables cross the container boundary; provider keys and
the rest of the host environment do not. The existing monotonic timeout,
cancellation callback, process-group reap, and bounded-output contract wraps the
Docker CLI. Every execution uses a unique name and attempts `docker rm --force
--volumes` in `finally`, including timeout and cancellation paths. A command cwd
outside the configured workspace is rejected before Docker invocation.

Selecting Docker performs `docker info` during runtime assembly. A missing CLI
or daemon is therefore reported before any model call and never falls back to
direct-host execution. `repoagent sandbox status --backend docker` distinguishes
an isolated backend from an available isolated runtime, so an installed but
stopped Docker service produces `is_isolated=true`, `available=false`, and a
failed status rather than a false readiness claim.

The Agent sandbox also accepts an explicit workspace-path projector. This is
required when WSL invokes the Windows `docker.exe`: the CLI cannot bind a raw
`/home/...` path, and Docker Desktop may not expose its distro mount service.
The Polyglot live runner now shares its `--wsl-windows-path` converter with both
the Agent shell sandbox and the grader staging backend. An actual smoke placed a
workspace under Windows Temp, projected it to a `C:\\...` bind source, and read it
inside Docker Desktop 29.6.2 with networking disabled and exit code 0.

Offline sandbox, CLI, and security verification includes the external-CLI path
projection contract. The native WSL socket remains absent, but the verified
Windows Docker CLI plus Windows staging path provides the isolated live-campaign
route without enabling direct-host fallback. Persistent container reuse, network
allowlists, sandboxed stdio MCP processes, and a BoxLite MicroVM backend remain
future slices. The complete suite passed 568 tests with six pre-existing metrics
deprecation warnings; Ruff and whitespace checks passed.

## TECH-074 - Source-bound Provider Campaign Preflight

- Area: paid/live evaluation admission and provenance
- Status: implemented for the Polyglot single-task campaign
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/evaluation/provider_probe.py`, `repoagent/evaluation/polyglot_campaign.py`, `scripts/run_polyglot_task.py`
- Tests: `tests/test_provider_probe.py`, `tests/test_polyglot_evaluation.py`

The native Tool-call preflight remains bounded to two attempts, 128 output tokens
and a 60-second per-call timeout by default. It still requires exact provider and
model identity, one exact echo Tool call, actual complete usage fields, explicit
tokenizer/pricing provenance, and no fallback. Those transport checks are now
part of a canonical approval digest rather than a reusable global capability
claim.

For a formal Polyglot campaign the digest also binds the source commit and tree,
dirty state, benchmark definition digest, secret-free runtime configuration,
Tool signature, sandbox identity/isolation, attempt budget, output ceiling and
timeout. The complete approval identity and digest are persisted in
`provider-preflight.json`. After the paid probe and before the Agent Turn, the
campaign recollects source provenance and fails the gate if commit, tree or dirty
state changed.

`scripts/run_polyglot_task.py` now requires a clean committed source by default.
`--allow-dirty-source` is an explicit development-only override; dirty runs retain
their exact tree digest but are not silently treated as formal evidence. Invalid
or incomplete approval identity fails before the first Provider call.

Provider and Polyglot focused verification passed 23 tests, covering bound
identity, explicit timeouts, missing fields, source drift after probe, and clean
commit admission. The campaign also uses a cross-process non-blocking lock keyed
by benchmark digest, acquired before Agent construction or Provider calls. An
exact approval cache under workspace state reuses a successful probe only when
both the approval digest and a canonical whole-record digest validate; malformed,
tampered, or identity-drifted cache records fail closed. The complete suite
passed 577 tests with six pre-existing metrics deprecation warnings; Ruff, both
CLI help smokes, and whitespace checks passed. Aggregate cost reservation and
multi-task campaign orchestration remain pending.

## TECH-075 - Budgeted Polyglot Campaign Orchestration

- Area: public coding benchmark execution and evidence
- Status: implemented for multi-task canary campaigns
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/evaluation/polyglot_suite.py`, `repoagent/evaluation/polyglot_campaign.py`, `scripts/run_polyglot_campaign.py`, `repoagent/sandbox.py`
- Tests: `tests/test_polyglot_evaluation.py`, `tests/test_sandbox.py`

`PolyglotCampaign` executes the selected task and repetition matrix through the
single-attempt evidence boundary. It retains aggregate `rows.jsonl` and
`results.json` files while each attempt keeps its patch, isolated grade, provider
preflight and checksummed Agent evidence bundle. Provider preflight failure aborts
further paid execution, but all remaining planned identities are written as
explicit `skipped` rows and remain in the reported denominator.

`CampaignBudget` requires an explicit pricing snapshot and computes a conservative
worst-case call, input-token, output-token and USD ceiling before the output
directory or Agent exists. Aggregate results separately gate observed per-attempt
call counts and actual partial cost. Missing price coverage fails the actual-cost
gate instead of being interpreted as zero spend. A benchmark-keyed process lock
prevents concurrent paid writers, and source provenance is captured before the
campaign and compared again after execution.

The live campaign CLI accepts separate cache-read and cache-write rates and passes
the same complete pricing snapshot to both budget evidence and the Agent call
ledger. This matters for Providers such as DeepSeek that report cached tokens
separately: a campaign with cache usage and no corresponding rate must fail cost
completeness instead of presenting cached input as free.

The live task and campaign entry points now force both model-directed shell tools
and hidden-test grading through the configured Docker runtime with isolation
required. The Docker executable, image and resource limits are shared across both
boundaries; unavailable isolation fails before model execution rather than falling
back to the host.

Focused Provider, runtime, sandbox and Polyglot verification passed 88 tests. The
complete suite passed 581 tests with six pre-existing metrics deprecation warnings;
Ruff, the product CLI and both Polyglot CLI help smokes, and whitespace checks
passed. No paid Provider or benchmark campaign was run in this implementation
slice.

## TECH-076 - Credential-free Polyglot CI Campaign

- Area: public benchmark pipeline regression
- Status: implemented; live canary remains pending
- Implemented/tested: 2026-08-26
- Owning modules: `scripts/run_polyglot_fixture_campaign.py`, `benchmarks/fixtures/polyglot-mini/`, `.github/workflows/ci.yml`
- Tests: `tests/test_polyglot_evaluation.py`; CI campaign and schema-validation commands

The CI workflow now runs a two-task Python fixture with two repetitions through the
real RepoAgent Turn, Tool Gateway, patch capture, grade and evidence-bundle paths.
The provider is deterministic and the fixture grader compares fixed text without
executing generated code, so the job needs no model credential or Docker daemon.
It validates `results.json` with per-row evidence required and uploads the complete
campaign directory as `polyglot-fixture-evidence`. Evidence validation resolves
every relative path under the result directory, rejects traversal and symlinks,
requires the matching digest field, and recomputes each file or bundle-manifest
SHA256.

The result schema explicitly states that scripted campaigns verify orchestration
and evidence rather than model coding quality. A local reproduction produced four
planned rows, four passes, no skips, eight recorded scripted calls and four complete
attempt evidence trees; the result validator accepted every row. This is a CI
contract only. It does not complete `P11-06`, which still requires the bounded
six-language live canary outside pull-request CI. Final local verification passed
582 tests with six pre-existing metrics deprecation warnings; full Ruff and
whitespace checks passed.

## TECH-077 - Structured Cross-Turn Conversation Replay

- Area: Provider conversation protocol and context admission
- Status: offline contract implemented; live replay pending
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/conversation.py`, `repoagent/agent_loop.py`, `repoagent/context_manager.py`, `repoagent/providers/base.py`, `repoagent/providers/clients.py`, `repoagent/providers/fallback.py`
- Tests: `tests/test_conversation.py`, `tests/test_provider_runtime.py`

Providers that declare structured-message support now receive persisted history as
native `ModelMessage` values before the current user request. Assistant Tool calls
retain their call ids, names and arguments; Tool results retain the matching id and
are wrapped again as untrusted data. The same sequence survives a new Turn and a
session reload. Legacy sessions containing a Tool result without its preceding
assistant Tool-call record are repaired into a valid pair rather than emitting an
orphan result.

History admission operates on complete user-turn groups. It selects the newest
groups within the input budget, clips oversized message content while retaining
closed untrusted boundaries, and drops a whole older group instead of splitting a
Tool call from its result. Canonical projected-message token estimates include
roles, content, Tool ids, names, arguments, reasoning text and thinking blocks.
Those tokens replace the former prompt-only count in context-window admission and
are retained in prompt metadata and traces. Structured history is no longer also
rendered into the text prompt, eliminating duplicate context.

Anthropic-compatible history additionally persists and replays signed thinking
blocks before assistant text and Tool-use blocks, which covers the built-in
DeepSeek protocol. OpenAI/Anthropic clients advertise this capability explicitly;
a fallback chain enables it only when every candidate supports it. Ollama,
FakeModelClient and other prompt-only clients keep deterministic flattened history,
so native alignment does not remove their conversation context.

Focused conversation, Provider, context, recovery, accounting and Agent tests
passed 157 cases. The complete suite passed 586 tests with six pre-existing metrics
deprecation warnings; Ruff and whitespace checks passed. A real DeepSeek replay is
still required before this audit item can be marked fully aligned.

## TECH-078 - OpenAI Responses Reasoning Recovery

- Area: Provider response normalization and empty-response recovery
- Status: offline contract implemented
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/providers/clients.py`, `repoagent/providers/base.py`, `repoagent/agent_loop.py`, `repoagent/empty_recovery.py`
- Tests: `tests/test_provider_runtime.py`, `tests/test_empty_recovery.py`, `tests/test_repoagent.py`

OpenAI Responses output items with `type=reasoning` now cross the Provider boundary
as both a display-neutral reasoning summary and the original structured item. The
normalizer handles ordinary JSON and completed SSE responses, while streamed
`response.reasoning_summary_text.delta` values provide a bounded fallback when the
terminal response omits a summary. Chat-Completions-compatible
`reasoning_content` remains supported independently.

For OpenAI/compatible endpoints with the existing Responses capability flag,
requests ask for `reasoning.encrypted_content`. A later assistant message replays
only server-issued reasoning items that contain an id or encrypted payload, and
only the Responses fields accepted by the projection. It does not fabricate an
opaque item from plain reasoning text and does not project Anthropic thinking
blocks into an OpenAI request.

Thinking-only recovery now preserves `reasoning_content` and `thinking_blocks` on
the synthetic assistant message instead of converting internal reasoning into
ordinary assistant text. An end-to-end offline test exercises a reasoning-only
Responses result followed by a successful retry, verifies the encrypted item in
the second HTTP request, and confirms that recovery scaffolding is absent from
persisted session history. These tests establish transport and bounded-recovery
contracts; they do not claim improved answer quality from a live model. Final
verification passed 589 tests with six pre-existing metrics deprecation warnings;
Ruff and whitespace checks passed.

## TECH-079 - Prompt-Only Context-Overflow Recovery

- Area: Provider-neutral context overflow recovery
- Status: implemented
- Implemented/tested: 2026-08-26
- Owning modules: `repoagent/context_overflow.py`, `repoagent/context_manager.py`, `repoagent/runtime.py`, `repoagent/agent_loop.py`
- Tests: `tests/test_context_overflow.py`, `tests/test_context_manager.py`, `tests/test_provider_runtime.py`, `tests/test_repoagent.py`

Structured OpenAI and Anthropic transports already recover from a classified
context overflow by replacing older Tool-result bodies in the native Provider
message sequence while retaining the three newest results. Prompt-only clients
such as Ollama and complete-only compatibility adapters consume
`ModelRequest.prompt`, so changing only `ModelRequest.messages` did not reduce the
actual request they transmitted.

The prompt-only branch now creates a copied history snapshot, elides older Tool
results without mutating persisted session history, and halves the emergency
history token budget for the retry. Context assembly accepts both overrides only
for that local request. A later Tool call refreshes the snapshot from current
session history so new evidence is retained. Exhaustion synthesis uses the same
reduced view instead of silently restoring the overflowing prompt.

Recovery remains fail-closed and bounded. With three or fewer Tool results there
is nothing eligible for this policy, so the original Provider error is retained
after one call. A focused Agent test verifies that the retry's actual prompt is
shorter, while the no-elision test verifies that an unchanged prompt is never
retried. Final verification passed 590 tests with six pre-existing metrics
deprecation warnings; Ruff and whitespace checks passed.

## TECH-080 - Bounded Live Polyglot Acceptance

- Area: real-provider coding task, isolation, grading, and cost evidence
- Status: single-task acceptance passed; 24-task canary pending
- Executed: 2026-08-26
- Source commit: `5a3b54b95d9912c1415af88e663c03ba7e3fab35`
- Benchmark: Aider Polyglot commit `7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f`, task `python/affine-cipher`
- Local evidence: `artifacts/polyglot-live/deepseek-affine-5a3b54b-live-pass-20260826/`

A clean-source live campaign used `deepseek-v4-flash` and the isolated
`repoagent-polyglot-python:20260825` Docker image. Provider preflight resolved the
requested model without fallback, returned the exact native Tool call, and
reported actual usage. The Agent then completed five model calls and four Tool
steps in the sequence `list_files`, `read_file`, `write_file`, `run_shell`, and
final answer. The Turn converged with `final_answer_returned`; the hidden Docker
grader passed all 16 tests.

All aggregate gates passed: the planned denominator was 1/1, worst-case admission
was $0.122665 under a $0.13 cap, observed task calls were 5 under a limit of 14,
source identity remained stable, and the five task calls had complete estimated
cost of $0.002248288 using the recorded official peak-rate snapshot. Usage was
2,005 fresh input, 17,152 cache-read, and 853 output tokens. The separate
preflight request is not included in the task-call cost total, so this number must
not be represented as an account-billing total.

The evidence validator recomputed the result schema and every referenced evidence
digest successfully. A preceding independent run on source commit `43983c8`
converged but failed one hidden test (15/16): the generated coprimality check only
tested divisibility by 26. Its failure bundle is also retained locally. The two
runs demonstrate why convergence and code correctness are separate gates, but
they are not a general quality estimate. The successful run has effective N=1
and a Wilson 95% pass-rate interval of approximately [20.65%, 100%].

## TECH-081 - Strict Paired Polyglot Comparison Contract

- Area: external baseline comparison and benchmark statistics
- Status: analysis contract implemented; paired live execution pending
- Implemented/tested: 2026-08-28
- Owning modules: `repoagent/evaluation/polyglot_pair.py`, `repoagent/evaluation/polyglot_campaign.py`, `repoagent/evaluation/polyglot_suite.py`, `repoagent/evaluation/cli.py`
- Tests: `tests/test_polyglot_pair.py`, `tests/test_polyglot_evaluation.py`

Polyglot result rows now bind each attempt to a digest of the model-visible public
task input and a separate digest of the hidden grader input. Campaign design also
records the Provider, protocol, model, temperature, top-p, output-token limit,
Provider-call limit and context budget used for a future pair. Skipped attempts
retain the same task/grader identity, so an aborted campaign cannot shrink the
planned denominator.

`repoagent-eval compare-polyglot-paired CONTROL TREATMENT --output RESULT`
accepts two live, single-variant result artifacts only when their benchmark,
task/repetition matrix, runtime identity and per-task grader identity match. A
mismatch fails before statistics are produced. The output retains pass, fail,
error and skipped states, reports paired quality W/T/L and exact McNemar counts,
and reports duration, call-count and estimated-cost deltas with explicit metric
coverage and paired bootstrap intervals. Missing efficiency values are excluded
from that metric rather than converted to zero, while their rows remain in the
quality denominator.

This is the comparison and evidence contract for `P11-07`, not completion of the
live experiment. A baseline adapter must still produce the same versioned result
schema, both variants must be executed under the frozen identity, and the
resulting comparison must be retained before a Harness improvement can be
claimed. Earlier one-off runs with different Tool protocols or execution
boundaries are diagnostic observations and are intentionally rejected as strict
pairs.

## TECH-082 - Reproducible Six-Language Polyglot Grader

- Area: isolated public-benchmark execution environment
- Status: six-language known-good smoke passed; live canary pending
- Implemented/tested: 2026-08-28
- Owning modules: `benchmarks/polyglot-image/`, `repoagent/evaluation/container.py`, `repoagent/evaluation/polyglot.py`, `scripts/run_polyglot_image_smoke.py`, `scripts/run_polyglot_campaign.py`
- Tests: `tests/test_evaluation_container.py`, `tests/test_polyglot_evaluation.py`, `tests/test_polyglot_image.py`
- Local evidence: `artifacts/polyglot-live/image-smoke-20260828/`

The repository now defines its Polyglot grader image instead of relying on an
unrecorded local Python image. The build pins Go 1.21.5, Rust 1.83.0, Node
20.18.1 and Gradle 8.7, uses JDK 21, and installs the C++/CMake and Python test
toolchains. The base image is locked by digest, and release builds disable
timestamped BuildKit provenance attestations so identical runnable manifests
retain the same execution identity. Java/JUnit and JavaScript/Jest dependencies
are fetched at image build time. Runtime grading remains network-disabled and
uses the prewarmed dependency cache.

`run_polyglot_image_smoke.py` restores one benchmark-owned known-good reference
per language only inside the grader boundary, then executes the same hidden-test
path used after an Agent turn. It records the benchmark commit/digest, image ID,
per-language exit status, output and reference mapping. This is an environment
acceptance test, not a coding-quality result.

The smoke exposed four previously hidden infrastructure errors. C++ CMake derives
its target from the exercise directory name, so the container runner now preserves
the workspace basename. Go test binaries and Gradle native libraries cannot run
from the deliberately `noexec` `/tmp`, so language build state is placed in the
disposable mounted workspace while `/tmp` stays non-executable. The Java grader
now removes `@Disabled` annotations in its private grading copy, matching the
benchmark's full-test semantics instead of accepting one enabled test and fifteen
skips. Windows-backed staging cleanup now restores owner write permission before
deletion and still fails closed if any new attempt directory remains.

The final offline smoke passed 6/6 languages on benchmark commit
`7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f` and definition digest
`sha256:408e9c87f72d0e4469e480e912fdc58f915d0778861c6e1593199e10cceb8bd4`.
The immutable image identity was
`repoagent-polyglot@sha256:abb4183a827978e195474e6a0594ffa59916a1efcd625ecde6ab5d3386381096`.
C++ passed 17 cases, Java passed all 16 enabled cases, JavaScript passed 16,
Python passed 16, Rust passed 12, and Go completed its package test. Formal
campaign entry now rejects a mutable image tag before loading the dataset or
making Provider calls. A 24-task live model run remains required to complete
`P11-06`.

## TECH-083 - In-Turn Transcript Admission and Failure Accounting

- Area: structured Provider transcript compaction and failed-turn evidence
- Status: implemented; clean single-task rerun passed, 24-task canary pending
- Implemented/tested: 2026-08-28
- Owning modules: `repoagent/context_overflow.py`, `repoagent/agent_loop.py`, `repoagent/agent_turn_runner.py`, `repoagent/providers/profiles.py`
- Integration: `scripts/run_polyglot_campaign.py`, `repoagent/evaluation/polyglot_pair.py`
- Tests: `tests/test_context_overflow.py`, `tests/test_model_profiles.py`, `tests/test_polyglot_evaluation.py`, `tests/test_polyglot_pair.py`
- Diagnostic evidence: `artifacts/polyglot-live/deepseek-v4-flash-canary24-d1620d6-20260828/` (local and ignored)
- Acceptance evidence: `artifacts/polyglot-live/deepseek-go-alphametics-a918ec1-windows-workspace-20260828/` (local and ignored)
- Budget diagnostic: `artifacts/polyglot-live/deepseek-v4-flash-canary24-caa0d9f-20260828/` (local and ignored; stopped after 15 complete rows)

The first 24-task live canary attempt was stopped during its fourth task after
two consecutive infrastructure failures. Completed DeepSeek calls caused the
in-turn structured message transcript to exceed the configured 8,000-token input
budget before the next Provider request. Admission happened outside the existing
Provider-error recovery block, so the turn failed even though the reported
32,768-token physical window still had headroom. The partial campaign is a
diagnostic artifact and must not be used as a model-quality denominator.

`fit_messages_to_token_budget` now runs before every structured Provider request
and exhaustion-synthesis request. It preserves the initial user message plus
native Tool call IDs, names and matching Tool results, while deterministically
bounding replay-only assistant content, display reasoning and Tool results.
Thinking-only retry messages and old Tool call/result exchanges may
be removed as whole units. Signed thinking and Tool arguments in retained
exchanges are always replayed unchanged. Reduction is applied to a request
snapshot, so the authoritative in-turn transcript, session and trace evidence is
not rewritten.
The runtime records before/after token counts and the reduction trigger as
`context_budget_recovered` evidence. If the mandatory user content and minimum
structural transcript still cannot fit, admission continues to fail closed.

The diagnostic run also exposed a cost-accounting defect: failed Turns had a
terminal `turn.json` and `calls.jsonl`, but `AgentTurnRunner` left TaskState as
running and omitted `report.json`. Polyglot rows therefore reported zero calls
for failures despite five completed calls on Go and six on Java. The exception
path now persists `failed/model_error` TaskState and a final report before
returning the failed TurnOutcome. Failed campaign rows consequently retain usage,
CallEfficiency and incomplete-pricing state instead of silently assuming zero.

DeepSeek V4's documented one-million-token physical context is now explicit in
the built-in profile, independently of the smaller configured input budget.
Physical window tokens and provenance participate in strict Polyglot pairing, and
the campaign CLI exposes an explicit override. This corrects capability identity;
it does not relax the 8,000-token compaction workload used by the canary.

The first post-fix Go replay proved protocol correctness but also exposed a host
mount confounder: Docker Desktop could grade from its Windows staging directory,
while Agent `run_shell` calls against a WSL-resident attempt workspace failed at
the distro mount service. The campaign now accepts a separate
`--agent-staging-root`; result evidence can remain under the requested output
root while executable attempt workspaces reside on a Docker-visible host path.
With that root on `/mnt/c`, `go test` succeeded inside the Agent sandbox and the
same `go/alphametics` task passed hidden grading, normal Turn convergence and all
campaign gates in 11 calls for an estimated complete cost of USD 0.0113416128.
This is infrastructure acceptance for the repaired path, not a multi-task quality
claim.

The subsequent 24-task restart completed 15 rows before manual stop. It retained
no infrastructure-error rows, but Java `alphametics` used 15 Provider calls
against the declared per-attempt limit of 14. The campaign previously enforced
that limit only as a post-run aggregate gate, so recovery and exhaustion
synthesis could spend beyond the admitted attempt envelope. `RepoAgent` now has
an optional execution-level `max_provider_calls`; the Polyglot runner binds it to
`--max-provider-calls-per-attempt`, prevents an unbudgeted synthesis call, and
retains a non-converged terminal report at the limit. The interrupted 15-row
artifact remains diagnostic and cannot complete `P11-06`.

A later run on `53384a7` wrote all 24 planned rows after the operator requested
stop, but seven consecutive DeepSeek connection failures produced incomplete
usage evidence. Its actual-cost gate therefore failed and the run is diagnostic,
not release evidence. Multi-task campaigns now fail fast after any infrastructure
`error` row while writing the remaining planned rows as `skipped`; ordinary
hidden-test failures continue because they are valid quality observations.

## 5. Decision Index

| Decision | State | Rationale |
| --- | --- | --- |
| Keep `repoagent` as stable internal package | accepted | Future brand changes should not trigger another import migration |
| Keep `RepoAgent.ask()` during Spine migration | accepted | Protect current application callers while changing internal orchestration |
| Implement Turn lifecycle before scheduler concurrency | accepted | Concurrency over an undefined lifecycle multiplies failure modes |
| Treat coding as primary product workload | accepted | It provides objective tasks, executable verification, and clear demos |
| Keep Harness interfaces reusable | accepted | Prevent Git/repository details from leaking into core runtime state |
| Record planned work outside this ledger | accepted | Avoid presenting future behavior as implemented capability |
| Reuse mature modules by default | accepted | Module-by-module adaptation preserves mature invariants and avoids low-value rewrites |
| Migrate one dependency-closed slice per commit | accepted | Keeps review, testing, and rollback understandable |
| Isolate foreground and background capacity | accepted | Runtime work cannot starve interactive coding requests |

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
| Checkpoint projection | `repoagent/checkpoint.py` | partial | TECH-003 |
| Tool execution | `repoagent/tool_executor.py`, `repoagent/tools.py` | partial | TECH-004 |
| Context reduction | `repoagent/context_manager.py` | partial | TECH-005 |
| Memory | `repoagent/features/memory.py` | partial | TECH-005 |
| Provider contracts and adapters | `repoagent/providers/base.py`, `repoagent/providers/clients.py` | typed streaming and native tools implemented | TECH-011 |
| Evaluation | `repoagent/evaluation/` | partial | TECH-007 |
| Naming and compatibility | `repoagent/config.py`, `repoagent/paths.py` | implemented | TECH-001 |
| Runtime Spine | `repoagent/spine/`, `repoagent/agent_turn_runner.py` | implemented single-Turn lifecycle | TECH-008 |
| Turn scheduling | `repoagent/spine/scheduler.py`, `repoagent/spine/_barrier.py` | implemented; deep cancellation partial | TECH-009 |
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

## TECH-004 - Current Tool Execution Baseline

- Plan items: predecessor to P3
- Status: partial
- Owning modules: `repoagent/tool_executor.py`, `repoagent/tool_context.py`, `repoagent/tools.py`
- Tests: `tests/test_tool_executor.py`, `tests/test_tools.py`, `tests/test_allowed_tools.py`, `tests/test_security.py`

The current implementation provides a legal-tool set, workspace path checks, approval policy, and structured execution results. It does not yet provide process isolation, MCP, capability tokens, native tool schema generation, or a proven single gateway for all side effects.

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

Runner exceptions are normalized to `TurnState.FAILED` with a persisted error string. Cancellation is represented in the state model, while provider/tool-level cooperative propagation remains P1-09. Append-first Turn persistence and host-start recovery are documented in TECH-010.

### Verification

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
PATH="$PWD/.venv/bin:$PATH" .venv/bin/ruff check .
.venv/bin/repoagent --help
.venv/bin/python -m repoagent --help
```

Observed: 154 tests passed with six pre-existing `datetime.utcnow()` deprecation warnings; Ruff and both CLI smoke commands passed. Focused Spine and compatibility tests contributed 14 new cases.

### Tradeoffs and Follow-ups

`AgentLoop` remains synchronous and runs through `asyncio.to_thread()` so nested synchronous delegation remains compatible and the event loop stays available. Admission, ordering, capacity, and teardown are now owned by TECH-009; provider/tool-level cancellation remains pending.

## TECH-009 - Session Scheduler and Teardown Barrier

- Plan items: `P1-06`, `P1-07`, `P1-08`, `P1-10`, `P1-12`; partial `P1-09`
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

The scheduler handles cancellation while queued, while waiting for capacity, and while executing an async runner. `TurnRuntime` converts cancellation into a persisted terminal outcome. The current `AgentTurnRunner` delegates the synchronous AgentLoop through `asyncio.to_thread()`; Python cannot safely kill that worker thread or an active synchronous HTTP/subprocess call. Provider-level and tool-process cancellation therefore remain P2-04 and P3-05, and `P1-09` is not marked complete.

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

- Plan items: `P2-01`, `P2-02`, `P2-03`; partial `P2-04`, `P2-07`
- Status: implemented typed streaming boundary; active-I/O cancellation pending
- Implemented: 2026-08-24
- Owning modules: `repoagent/providers/base.py`, `repoagent/providers/clients.py`
- Integration: `repoagent/agent_loop.py`, `repoagent/runtime.py`
- Tests: `tests/test_provider_runtime.py`, `tests/test_repoagent.py`, `tests/test_agent_loop.py`

### Problem

AgentLoop previously called `complete(prompt, max_tokens)` and then inspected the mutable `last_completion_metadata` side channel. Provider text, usage, cache metadata, timeout behavior, and failures therefore had no single result contract, and custom provider behavior could leak into orchestration.

### Interface

- `ModelRequest` carries prompt, output budget, cache policy, timeout override, correlation IDs, attempt number, and provider-neutral `ModelTool` schemas.
- `ModelResult` carries text, normalized tool calls, finish reason, usage, provider/model identity, latency, and immutable metadata.
- `ModelUsage` separates actual, estimated, and missing sources and normalizes cache token fields.
- `ModelEvent` is the common text-delta, tool-call, and terminal streaming event shape.
- `ProviderError` exposes category, retry/fallback verdicts, provider, and optional HTTP status without caller string matching.
- `generate_model()` invokes typed providers and contains compatibility for complete-only clients.

### Invariants

- AgentLoop consumes `ModelResult` and does not parse provider response objects or mutable usage side channels.
- Token counts and latency cannot be negative.
- Tool-call arguments are copied into an immutable mapping at the provider boundary.
- Invalid typed return values fail with `ProviderProtocolError`.
- Every provider failure reaching AgentLoop writes a `model_failed` trace event before propagating.
- Structured error evidence contains stable classifications, not secret-bearing response text.
- Every stream must end with one normalized completed event; truncated provider streams fail closed.
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

### Failure, Timeout, and Cancellation

HTTP, connection, protocol, and timeout failures now have explicit categories and retry/fallback properties. A `ModelRequest.timeout_seconds` override reaches built-in transports. Completed wire events are mandatory, so a dropped stream is not accepted as a successful partial response. The current standard-library HTTP calls remain synchronous; cancelling the scheduler task cannot reliably abort a connection that is blocked before a response handle exists. Active-I/O cancellation remains `P2-04`, and `P1-09` stays partial.

### Verification

Focused tests cover request validation, usage-source normalization, immutable native tool calls, complete-only compatibility, invalid stream ordering, provider timeout forwarding, Ollama JSONL, OpenAI Responses SSE, Anthropic Messages SSE, tool schema translation, multi-tool execution, chunk-boundary final-answer filtering, AgentLoop correlation, and persisted provider failure evidence.

### Tradeoffs and Follow-ups

- `last_completion_metadata` remains populated as a compatibility projection, not as the orchestration source of truth.
- Complete-only custom clients and Fake retain a terminal fallback; only the three network adapters claim wire-level streaming.
- Native tool calls execute serially until ToolGateway can classify safe read-only calls for bounded concurrency.
- Final-text streaming is intentionally protocol-aware: free-form reasoning outside `<final>` is buffered/discarded rather than shown to users.

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

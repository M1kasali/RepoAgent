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
| Provider contracts and adapters | `repoagent/providers/base.py`, `repoagent/providers/clients.py`, `repoagent/providers/fallback.py` | typed streaming, cancellation, and explicit fallback implemented | TECH-011 |
| Model profiles | `repoagent/providers/profiles.py`, `repoagent/cli.py` | validated built-in profiles and compatibility assembly implemented | TECH-012 |
| Usage accounting | `repoagent/providers/base.py`, `repoagent/agent_loop.py`, `repoagent/spine/runner.py` | multi-call totals and source completeness implemented | TECH-013 |
| Call efficiency | `repoagent/pricing.py`, `repoagent/call_efficiency.py`, `repoagent/run_store.py` | explicit price snapshots and per-attempt cost evidence implemented | TECH-014 |
| Cache and compaction accounting | `repoagent/providers/base.py`, `repoagent/pricing.py`, `repoagent/call_efficiency.py` | provider-aware cache cost and compaction call classification implemented | TECH-015 |
| Provider replay and live acceptance | `repoagent/call_replay.py`, `live_tests/test_live_providers.py` | deterministic offline verification and explicit live opt-in implemented | TECH-016 |
| Tool Gateway contracts | `repoagent/tool_contracts.py` | immutable typed definition, request, effect, and result contracts implemented | TECH-017 |
| Tool definition projection | `repoagent/tools.py`, `repoagent/providers/tool_schema.py`, `repoagent/prompt_prefix.py` | one definition drives schemas, validation, effects, and prompt signatures | TECH-018 |
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

The scheduler handles cancellation while queued, while waiting for capacity, and while executing an async runner. `TurnRuntime` converts cancellation into a persisted terminal outcome. `AgentTurnRunner` creates a cancellation token for its worker thread; cancellation reaches AgentLoop and the provider adapters, then the runner waits for worker convergence before returning control. Built-in providers close an active HTTP response, but Python still cannot kill a thread blocked before a response handle exists or stop a synchronous tool subprocess. Connection setup is timeout-bounded and tool-process cancellation remains `P3-05`, so `P1-09` is not marked complete.

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

The standard-library transport cannot expose a response handle while DNS, connection setup, or response-header receipt is still blocked, so that phase remains bounded by the configured timeout. Complete-only compatibility clients can observe cancellation only before and after their blocking call. Synchronous tool-process cancellation is owned by `P3-05`, so `P1-09` stays partial.

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
- Status: implemented accounting contract; model compactor integration remains owned by `P4-04`
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
- Any model-backed compactor added by `P4-04` must issue `call_kind=compaction` through this ledger; compaction cost contributes to total successful-Turn cost.

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
- P4 must reuse this accounting boundary when it introduces model-based summarization; a direct unmetered provider call would violate the contract.

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
- Status: implemented contract; execution routing remains legacy until `P3-03`
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

The module intentionally contains no handler callable and performs no execution. This keeps the external interface small: implementations and adapters can vary behind the future Tool Gateway without exposing their internal dependencies to AgentLoop.

### Verification

Focused tests cover recursive immutability, stable definition identities, closed schema validation, effect/concurrency coherence, JSON compatibility, nested argument copies, request correlation, parent-call rejection, structured success, workspace effects, failure codes, duration validation, and public exports. Existing tool and provider tests remain green, confirming the new contracts do not change the legacy execution path.

### Tradeoffs and Follow-ups

- `BASE_TOOL_DEFINITIONS` is now the authoritative built-in registry; its projection and validation rules are recorded in TECH-018.
- `ToolResult` is deliberately distinct from the compatibility `ToolExecutionResult`; P3-03 will adapt and then retire the loose metadata result.
- Timeout and cancellation fields are not guessed into the definition yet. P3-05 will add them when the execution lifecycle is owned by the Gateway.

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
  -> ToolExecutor effect / approval decisions
  -> bound runner semantic checks and execution
```

### Verification

Focused tests assert that registry entries have no legacy `schema`, `risky`, or `description` copies; provider schemas equal the definition projection; prompt signatures are order-stable; defaults and strict types reach runtime validation; invalid calls do not request approval; repeated default-equivalent calls are rejected; and all prior tool, safety, provider, allowlist, and AgentLoop behavior remains green.

### Tradeoffs and Follow-ups

- Tool-specific semantic checks remain an explicit second stage because JSON Schema cannot safely establish workspace state or patch uniqueness.
- The existing `ToolExecutor` still owns routing and returns its compatibility result. P3-03 will introduce the Gateway implementation and adapt AgentLoop to `ToolRequest`/`ToolResult`.
- The validator intentionally implements the JSON Schema subset used by RepoAgent tools. New schema keywords require contract tests before use.

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

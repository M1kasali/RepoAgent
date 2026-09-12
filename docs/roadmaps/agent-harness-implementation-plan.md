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
| Agent loop | Core implemented, including native replay and recovery | Turn-based runner with terminal outcomes | P1 |
| Session state | Core implemented | versioned session manager, export, atomic lifecycle | P1 |
| Scheduling | Core implemented; live performance acceptance pending | per-session FIFO, cross-session concurrency, quotas, cancellation | P1 |
| Provider layer | Core implemented; transport coverage parity incomplete | typed request/result, streaming, fallback, routing | P2 |
| Call efficiency | Accounting implemented; paired live cost acceptance pending | actual usage ledger, pricing, cache accounting | P2 |
| Tool execution | Core implemented | one typed gateway, audit, timeout, bounded parallel reads | P3 |
| MCP | Three real transports, diagnostics and Docker-owned stdio implemented and locally verified | discovery, schema projection, execution, trust policy | P3 |
| Sandbox | Docker lifecycle delivered: shared shell/MCP and durable on-demand orphan reconciliation; not full BoxLite parity | direct and isolated adapters, fail-closed policy | P3 |
| Context engine | Budgeting, admission and deterministic compaction implemented | segment assembly, token budgets, curator, compaction | P4 |
| Memory | Local and optional SQLite/FTS5 implemented; original Myna deferred | backend contract, consolidation, provenance, lifecycle | P4 |
| Skills | Retrieval, fusion, admission and hydration implemented (TECH-109); live benefit unverified | discovery, activation, lazy loading, references, local pool | P4 |
| Tracing | Core implemented; own release-bound measurements required | correlation context, semantic events, usage, query/export | P5 |
| Evaluation | Framework implemented; multidimensional acceptance parity incomplete | reproducible campaign runner, paired trials, scorecards | P6 |
| Subagents | Base contracts implemented; live benefit unverified | isolated manager, budgets, messaging, roles, evidence | P7 |
| Model routing | Deterministic profiles implemented | deterministic profiles, fallback chain, explainable selection | P7 |
| Plugin system | Declarative tools implemented; upstream extension coverage incomplete | manifest, discovery, external trust, lifecycle, Gateway-only tools | P7 |
| CLI/TUI/Gateway | Native terminal, RPC, session/model management and durable directory Gateway implemented (TECH-111 through TECH-121); not blanket upstream parity | separated assembly, unified commands, TUI transport, single-instance gateway | P8 |
| Channels/Cron | Directory adapter, schedules and optional QQ implemented; QQ live acceptance pending; Feishu/WeCom paused | intake, delivery, media, deduplication, claims, scheduled execution | P8 |
| Evolver | Multi-round isolated execution, sealed validation and approved activation implemented (TECH-102 through TECH-108); live benefit unverified | isolated candidates, sealed gates, activation, rollback | P9 |
| Release engineering | Complete | clean-head evidence bundle, CI gates, migration docs | P10 |

### Mainline Reconciliation - 2026-09-11

The original phase checkboxes describe the implemented slices at their stated
gates, not complete upstream behavioral parity. A component test is not a
product workflow or live benefit demonstration. The current matrix above takes
precedence over historical completion labels for remaining work. The mainline
is the complete Harness, prioritized by scheduling, cost, memory/context, tools,
tracing, reproducible evaluation and controlled evolution. P11 Polyglot tuning
and further paid campaigns are paused; they do not block mainline delivery.

- [x] `M1-01` Real stdio MCP SDK session, explicit configuration, discovery,
  Gateway execution, cancellation, shutdown and no isolated-to-host fallback.
  (TECH-089; not complete MCP parity.)
- [x] `M1-02` HTTP/SSE transports with endpoint/redirect trust enforcement,
  per-server connection diagnostics and real transport lifecycle tests.
  (TECH-090; static authentication headers, not automatic OAuth.)
- [x] `M1-03` Persistent sandbox process lifecycle and sandbox-owned MCP stdio;
  preserve fail-closed behavior until a backend supports it. Split into:
- [x] `M1-03a` Docker-owned persistent MCP processes: implementation, offline
  regression and local real-Docker acceptance delivered (TECH-091/092/093).
  WSL recovery and distro integration restored workspace mounting. Verified
  discovery/reuse, cancellation/deadlines, crash/reconnect and runtime teardown.
- [x] `M1-03b` A shared persistent sandbox for shell and MCP, with explicit
  start/stop, cross-call state and cancellation ownership. Select
  `docker-persistent`; the old `docker` backend retains disposable semantics.
  Delivery is scoped to local Docker lifecycle, not full BoxLite parity:
- [x] `M1-03b1` Staged persistent-shell adapter: explicit/lazy start, idempotent
  stop, shared scratch state, serialized ownership and fail-closed invalidation;
  unit and real-Docker checks (TECH-094; staged behavior superseded by TECH-095).
- [x] `M1-03b2` Share the persistent container with MCP; independently terminate
  exec processes, handle discovery/session teardown, wire the opt-in
  `docker-persistent` factory/CLI and verify mixed calls and runtime reuse
  (TECH-095). Normal cancellation preserves peers; cleanup failure invalidates
  the environment. Shell calls remain serialized; no multi-tenant guarantee.
- [x] `M1-03b3` Durable host-only ownership, live-owner leases, engine/label-bound
  deletion, restart/CLI orphan reconciliation and retained uncertain-create
  records (TECH-096). On-demand cleanup, not background recovery or process-state
  restoration; real SIGKILL and controlled delayed-create acceptance included.
- [x] `M2-01` Connect Evolver generation, evaluation, multi-round state/resume,
  human approval, actual runtime strategy selection and rollback end to end.
  Completed for the opt-in isolated task Runtime (TECH-108), not interactive
  CLI hot-reloading or live-model effectiveness acceptance.
- [x] `M2-01a` Materialize generated proposals into pinned immutable commits,
  validate declared paths/content/modes and resume interrupted materialization
  idempotently without changing the user's source worktree (TECH-097).
- [x] `M2-01b` Connect candidate generation, real evaluator adapters, cumulative
  budgets, multi-round journal/resume and sealed evaluation to the coordinator.
- [x] `M2-01b1` Execute deterministic checks from pinned commits in Docker;
  bind plans/receipts to source and image identities, replay completed evidence,
  reject ambiguous automatic reruns, and preserve failed cleanup workspaces
  (TECH-098). This is not paired quality evaluation or multi-round evolution.
- [x] `M2-01b2` Add paired evaluator execution and measurement validity, durable
  cumulative budgets across rounds, generator/termination scheduling and sealed
  evaluation; define explicit recovery for uncertain started attempts.
- [x] `M2-01b2a` Validate the full two-arm measurement matrix before attribution
  and statistical gates; reject missing/invalid/unpriced results and preserve
  full-matrix cost/trial accounting (TECH-099). This primitive is a pure gate;
  executable check orchestration is tracked separately below.
- [x] `M2-01b2b` Execute paired checks on pinned base/candidate commits; reserve
  run-wide pair/cost budgets durably, verify per-arm receipts and resume only
  known-safe boundaries (TECH-100). Real Docker binary grading, not Agent task
  scoring. Reservations are conservative and never automatically refunded.
- [x] `M2-01b2c` Connect actual Agent/model task evaluation and enforce Provider
  call/token limits inside its adapter; add verified reservation settlement,
  explicit uncertain-attempt reconciliation, generator/termination scheduling
  and sealed evaluation. No paid acceptance implied by binary-check fixtures.
- [x] `M2-01b2c1` Add a trial-local model admission gateway with call/output
  limits, identified full-request counting, explicit pricing reservations and
  fail-closed usage validation; verify tool/final execution through the actual
  Agent loop with a scripted Provider (TECH-101). No paid/candidate evaluation.
- [x] `M2-01b2c2` Load the pinned Harness snapshot into an isolated Agent worker,
  separate task fixtures and hidden grading, connect the bounded Provider and
  persist its evidence in the paired coordinator. Prove the candidate source,
  not merely its checkout path, is what ran before live model acceptance.
- [x] `M2-01b2c2a` Execute the pinned `repoagent/` package in an isolated Docker
  worker with scripted Provider responses, separate task fixtures and host-only
  exact-file grading; bind module hashes and paired receipts (TECH-102).
  This verifies source execution, not inference quality or paid model budgets.
- [x] `M2-01b2c2b` Connect the host-owned bounded Provider proxy to the isolated
  worker, persist call evidence and reconcile costs against outer reservations.
- [x] `M2-01b2c2b1` Add the host RPC dispatcher with bounded contract decoding,
  sequence fencing, trusted evidence-sink ordering and fail-closed budgeted
  calls (TECH-103). Transport and durable sink wiring remain under c2b.
- [x] `M2-01b2c2b2` Implement owned Docker stdio model transport, worker client
  and exclusive host-side call journal (TECH-104). Verify the Agent subprocess
  and Docker transport separately with scripted host Providers.
- [x] `M2-01b2c2b3` Assemble this channel into the pinned snapshot evaluator,
  bind model/journal identities into paired receipts and outer cost limits.
  TECH-105 verifies the path with scripted host leaf clients; live model
  acceptance is deferred; verified settlement is connected in TECH-108.
- [x] `M2-01b2d` Connect proposal generation, deterministic admission and paired
  comparisons into bounded multi-round search with durable history (TECH-106).
  Fixed-baseline search returns qualified candidates, not activated strategies.
- [x] `M2-01b2e` Close search with isolated sealed validation and explicit
  finalist selection, then hand verified evidence to human approval.
- [x] `M2-01b2e1` Freeze one qualified finalist per completed search, validate
  sealed results and expose explicit approval handoff (TECH-107). Isolated
  backend is injected; concrete hosted sealed adapter is connected in TECH-108.
- [x] `M2-01c` Wire human approval and activation/rollback into actual Runtime
  strategy selection; distinguish Harness source from task workspace and verify
  that the approved immutable strategy is the one actually executed.
  SnapshotDeployment resolves a complete approved commit per isolated task,
  including explicitly enabled skills. Normal CLI/TUI defaults are unchanged.
- [x] `M3-01` Align Skill retrieval/ranking/gating rather than equating keyword
  activation with the complete retrieval pipeline.
  TECH-109 connects cached BM25, weighted rank fusion, explicit activation vs
  lazy references, availability/tool admission, optional model gate and resource
  hydration to Runtime. Default local retrieval has no Provider call; injected
  multi-source/gate paths have offline coverage, not live effectiveness claims.
- [ ] `M3-02` Integrate the original Pico Myna backend after establishing its
  source/version identity and availability, and
  verify recall/store/Turn injection; do not claim Myna or LoCoMo results from
  local memory tests or unavailable external artifacts.
  Deferred by user decision; do not substitute another memory project.
- [x] `M3-02a` Connect explicit installed-plugin selection to Runtime assembly,
  fail closed for unavailable/ambiguous plugins, align recall with user track,
  and clean up partially started backends (TECH-110). Fixture-tested only.
  M3-02 remains open. The CodeCairn bridge was withdrawn at the user's request;
  its trial does not establish original Myna identity or complete this task.
- [x] `M3-03` With user approval, implement an independent built-in SQLite/FTS5
  backend instead of claiming unavailable external implementation parity
  (TECH-133). Verify process restart, repository/owner isolation, bounded
  persistence and new-session prompt injection. External Myna installation and
  LoCoMo effectiveness remain unverified; this is not an external-plugin result.
- [x] `M3-04` Run one bounded real-model cross-session memory case with an
  empty-track control (TECH-134). Three processes / three model calls: control
  answered UNKNOWN, shared track returned the exact stored fact, all Turns
  completed. No generalized memory accuracy or external benchmark claim.
- [ ] `M4-01` Complete product surfaces and channel adapters after core loops.
- [x] `M4-01a` Ship a runnable directory Gateway CLI with explicit sender policy,
  safe non-interactive approval, lifecycle cleanup, malformed-message isolation
  and real Runtime round-trip tests (TECH-111).
- [x] `M4-01b` Complete durable intake/delivery recovery across process restarts
  for the shipped directory Gateway (TECH-112): SQLite receipts, stable Turn
  identity, terminal-evidence recovery, bounded reply retries and explicit review
  of interrupted execution. Generic host/platform transport parity is not implied.
- [ ] `M4-01c` Complete native TUI/RPC interaction and externally configured
  platform adapters; a line-input terminal and directory queue are not parity.
- [x] `M4-01c1` Connect runnable stdio RPC to Runtime submission, subscription,
  cancellation and actual tool confirmation; fail closed on timeout/EOF and
  retain protocol/real-tool/child-process tests (TECH-113).
- [x] `M4-01c2` Build native terminal views and richer RPC surfaces
  (model management, questions); text preview streaming is opt-in.
  Platform adapters remain separate.
- [x] `M4-01c2a` Add workspace-scoped session listing/history and idle-only
  create/resume through full Runtime reconstruction, with storage identity
  checks and isolation regressions (TECH-114). One active session per connection.
- [x] `M4-01c2b` Wire Provider-derived text previews to opt-in RPC subscribers,
  with sequence/terminal boundaries and cross-chunk secret filtering before
  event persistence (TECH-115).
- [x] `M4-01c2c` Add an optional native terminal frontend for multi-line input,
  provisional text, terminal results, cancellation, fail-closed approval and
  session create/resume (TECH-116). Preserve the line/RPC entry points and test
  desktop/narrow terminal layouts. This is not full frontend parity.
- [x] `M4-01c2d` Connect bounded ask_user batches through capability-authorized
  tools, clarify RPC, suggested/free-form answers, timeout and cancellation
  cleanup, and native terminal controls (TECH-117).
- [x] `M4-01c2e` Add secret-free model options and idle-only connection-local
  profile selection; update budgets/token counting atomically, retain selection
  through session reconstruction, and cover real next-Turn behavior (TECH-118).
- [x] `M4-01c2f` Complete Provider configuration management (credential save,
  disconnect, curated model editing) and extended session interactions.
- [x] `M4-01c2f1` Add atomic user-level settings for supported API-key Providers,
  saved-key resolution, secret registration, model list editing and native settings
  controls (TECH-119). Configuration writes do not prove remote availability.
- [x] `M4-01c2f2` Add session titles, verified redacted history exports and
  version-fenced deletion of inactive sessions, with native management controls
  and stale-writer tombstones (TECH-120).
- [x] `M4-01c2f3` Complete clear/undo/branch workflows with consistent history,
  checkpoint and memory state, revision-fenced confirmation and terminal controls
  (TECH-121). These operations neither roll back files nor erase shared memory.
- [x] `M4-01c3a` Add optional QQ SDK gateway for C2C, group mentions and guild
  direct messages, with sender gating, source-bound replies, isolated SDK loop
  and offline Runtime tests (TECH-122). Real platform validation is still pending.
- [ ] `M4-01c3b` Complete Feishu and WeCom adapters, including their distinct
  addressing/media contracts; QQ does not imply parity for these platforms.
  Paused by user decision on 2026-09-12; resume only on explicit request.
- [ ] `M4-01c3c` Validate enabled external platforms with explicit credentials
  and retain live startup/intake/reply/shutdown receipts.
- [ ] `M5-01` Run own module-specific paired acceptance for the seven mainline
  areas, with frozen workload, baseline and retained receipts; do not borrow
  upstream resume numbers or substitute Polyglot scores.
- [x] `M6-01` Harden coding-file edits for LF/CRLF equivalence without rewriting
  untouched mixed endings; retain exact unique-match checks and verify the full
  read/edit/check tool workflow (TECH-123). No fuzzy patching or coding-quality
  improvement claim is implied.
- [x] `M6-02` Add exact-first, line-whitespace-only edit fallback and explicit
  boolean replace_all; reject overlapping spans, report match mode/count and
  verify approval and Runtime tool integration (TECH-124).
- [x] `M6-03` Run one bounded live coding smoke and independently inspect its
  outcome: code tests pass, but the eight-call Turn stops without normal completion
  (TECH-125; not a successful end-to-end acceptance claim).
- [x] `M6-04` Fix smoke verdict classification for budget-stopped Turns; replay
  small-context edit/test history retention offline and clarify edited_files
  provenance with workspace snapshots disabled before any further paid rerun
  (TECH-126/127). No general convergence improvement is claimed.
- [x] `M6-05` Retain verified execution outcomes and inspect native request
  composition (TECH-129, TECH-131, TECH-132). The dedicated run_tests tool binds
  unittest evidence to source and exposes current/stale state in model context.
  This does not retroactively validate older traces or prove failure recovery.
- [x] `M6-05a` Retain bounded historical shell command/status/exit-code observations
  in task checkpoints independently of workspace snapshots (TECH-128). These are
  raw process observations, not verified test outcomes; dedicated evidence was
  subsequently added in TECH-131.
- [x] `M6-05b` Inspect current native request assembly and refresh stale current-Turn
  context before normal calls and step-limit synthesis (TECH-129). Cover budget
  reduction, prior history and provider message projections offline. Original
  live-request replay remains unproven; verified outcome retention was
  subsequently added in TECH-131 and observed live in TECH-132.
- [x] `M6-06` Repeat the same bounded real coding smoke on clean commit 207f09b
  with unchanged task/model/budget/image (TECH-130): original tests 6/6, tests
  unchanged, normal Runtime completion in five calls / 9.18 seconds. Preserve
  the prior eight-call incomplete result; no statistical or causal claim.
- [x] `M6-07` Add source-bound unittest verification via a dedicated sandboxed
  tool; retain framework verdicts and invalidate stale records after file changes
  (TECH-131). Shell exit codes alone remain insufficient. This is local test
  evidence, not independent validation against adversarial or weakened tests.
- [x] `M6-08` Observe real model adoption of run_tests on clean commit f10806f
  without explicitly requesting that tool (TECH-132): six original tests passed,
  current verification reached the final native request, and Runtime completed
  in five calls / 8.76 seconds. Failure recovery was not exercised in this run;
  its existing coverage remains offline, not a live effectiveness claim.
- [x] `M6-09` Run one bounded, clean-source real-model failure-recovery case:
  observe an in-Turn failing run_tests result before editing, repair the source,
  rerun unchanged tests, independently verify the result, and require normal
  Runtime completion. A final passing suite alone is insufficient evidence.
  First bounded attempt on clean 252c157 failed (TECH-138): earlier context was
  elided, repeated reads/tests consumed 12 tool calls, and no repair occurred.
  Retain this failure; next reproduce context eviction offline before retrying.
  Selective reduction is implemented and regression-tested (TECH-139). Same-budget
  rerun on 52a3418 repaired code and passed independent tests (6/6), but the Agent
  did not retest or complete (TECH-140). M6-09 remains open.
  Two later same-budget runs did complete failure/repair/retest (6/6), after
  test-result semantics and next-step corrections, but still stopped at the
  tool-step limit (TECH-142/144). Normal completion is the remaining gate.
  That gate passed on clean dc432b3 (TECH-148): four model calls, six tool
  executions, 12.45 seconds, original failing verification followed by current
  6/6 pass, unchanged independent tests, and normal Runtime completion. Same
  model and limits; earlier failures retained. This closes one controlled
  case, not a held-out benchmark or general reliability claim.

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
- [x] `P0-07` Add CI for Ruff, full pytest, CLI smoke, script smoke, and package build. (Delivered in P10.)
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
- [x] `P3-08` Implement MCP discovery, registration, execution, and schema validation. (Stdio, HTTP/SSE, diagnostics and Docker-owned stdio locally verified; shared persistent sandbox remains M1-03b.)
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
- [x] `P11-06` Run a credential-free fixture campaign in CI and a 24-task six-language live canary outside PR CI. (24/24 live attempts executed on `c42e2bb`; all engineering gates passed.)
- [x] `P11-07` Add paired baseline-versus-Harness execution with identical model, task, attempt, and decoding configuration. (The 24-pair live canary completed with matching identities: RepoAgent 4 wins, 20 ties, 0 losses versus pico-harness; exact two-sided McNemar p=0.125, so the result is directional rather than statistically significant.)
- [ ] `P11-08` Run the frozen 225-task release campaign only after the canary safety, completion, and budget gates pass.

`P11-08` readiness work (2026-09-07 onward, TECH-085 through TECH-088):

- [x] Retain all five single-task budget/recovery diagnostics, including protocol failure and false convergence; none is a release-quality improvement result.
- [x] Withdraw unproven budget-reminder and retry-correction prompt candidates.
- [x] Stop exhausted empty recovery without manufacturing a successful final answer; retain checkpoint and unsuccessful-task cost evidence.
- [x] Diagnose the four code-pass/non-converged traces and define an eight-task, single-variable development protocol with explicit stop criteria. ([Diagnosis](../architecture/polyglot-convergence-diagnosis-20260907.md).)
- [x] Bind the environment-context candidate to a clean commit and an admitted paired budget, then execute the development protocol. (Clean `b031b9a` versus `2f310fa`: 16/16 executed, 96 Agent evidence files verified, engineering gates passed. Both variants passed 2/8 end-to-end and 4/8 code checks; 1W/6T/1L fails the zero-regression promotion gate. See TECH-087.)
- [x] Audit model-visible task contracts and build metadata before more paid quality tuning. (TECH-088: all 24 canary tasks reviewed; 20 omit build metadata, two omit Java support candidates, and two Go editor files correctly remain hidden test data. [Per-task audit](../architecture/polyglot-input-contract-audit-20260910.md). No scores or runtime behavior changed.)
- [ ] Implement a versioned runner-input policy with explicit file roles and reviewed public interface/build contracts; prevent editor/test-data leakage, bind visible contents and roles in pairing identities, and verify with offline fixtures before any paid rerun. Do not retrospectively rescore v1 or combine the Go performance change into this treatment.
- [ ] Measure workspace-snapshot overhead separately before changing Go tool deadlines or cache tracking.
- [ ] Re-run the complete canary against a clean, fixed baseline before authorizing the 225-task campaign.

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

Current status and explicit deferrals are summarized in
[Mainline Status](mainline-status.md). Original Myna and further platform
adaptation are paused. SQLite and reconciled documents were committed as
252c157 after full regression. Selective context reduction followed in 52a3418
(TECH-139). Test-result semantics and conditional guidance followed in 65972a4
and 6e1500e. Native prefixes and live budget feedback followed in e5ac7db and
dc432b3. M6-09 passed with unchanged limits and normal completion (TECH-148).
Next consolidate release verification/evidence and scope M5-01 paired acceptance.
Do not start a full Polyglot campaign or
substitute unrelated memory integrations. Module-level paired effectiveness
measurements remain separate work under M5-01.

## 10. Historical Delivery Notes

The following notes preserve earlier phase gates and campaign chronology.
Their statements about the next step or completion are historical, not the
current queue or an assertion of complete upstream parity.

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

`P11-06` is complete. CI runs and uploads the credential-free two-task,
two-repetition fixture campaign, while the external live gate executed 24
language-balanced DeepSeek V4 Flash tasks on clean commit `c42e2bb`. All 24 rows
executed with zero infrastructure errors or skips, complete cost evidence and
stable source identity. The measured quality baseline is 4/24 end-to-end passes;
it is retained as a bounded diagnostic baseline rather than a competitive claim.

The six-language grader image is now defined in-repository and its known-good,
network-disabled acceptance passed 6/6 languages against the frozen benchmark.
This repaired C++ exercise-directory preservation, Java full-test enablement,
Go/Gradle execution under a `noexec` temporary filesystem, and Windows-backed
staging cleanup. Formal campaigns require an immutable image digest. These are
environment gates only; the separate completed live result is recorded below.

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
payload, while offline regressions directly cover that branch. At that point the
next `P11-06` step was a fresh 24-task run with the same 12,000-token runtime/cost
envelope and prepared Windows-visible staging roots.

That formal run completed on clean commit `c42e2bb`: 24/24 executed rows, zero
errors and skips, 263 completely priced calls, USD 0.2301833184 actual estimated
task-call cost, and all campaign gates passing. Four tasks passed end to end;
eight patches passed hidden tests, exposing four code-correct Turns lost solely
to non-convergence. The run also exercised `_raw_arguments` live and recovered to
a valid `write_file` call without executing the malformed payload. `P11-06` is
closed; `P11-07` paired baseline execution is now the active slice.

The `P11-07` analysis boundary and external baseline adapter are now implemented.
Polyglot attempts persist frozen runtime, task and grader pairing identities, and
`compare-polyglot-paired` refuses mismatched benchmark, model/decoding/limit
configuration, task matrix, or grader input before producing paired W/T/L,
McNemar and efficiency deltas. `run_pico_baseline_campaign.py` executes the real
pico-harness `RuntimeTrialHost` while reusing the exact RepoAgent Provider
transport, hard call limit, effective input/output budgets, immutable Docker
image and hidden grader. Pico retains its own system prompt, context assembler
and native coding-tool loop; shell commands are redirected through the same
disposable Docker boundary. A no-provider smoke completed with one call and the
expected seven-tool surface, and the RepoAgent suite passed 619 tests. This does
not yet complete `P11-07`: the live 24-task baseline and resulting strict paired
report remain pending. Prior one-off comparisons with different protocols remain
diagnostic only.

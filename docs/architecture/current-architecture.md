# RepoAgent Architecture

RepoAgent is a local-first coding-agent runtime. Its primary engineering problem is not producing one model response; it is preserving task identity, policy, context, evidence, and recovery semantics across a multi-step repository change.

## Runtime Shape

```text
CLI / TUI / Channel / Cron
            |
       RuntimeHost
            |
   Scheduler + TurnRuntime
            |
        RepoAgent loop
       /      |       \
 Provider  Context   ToolGateway
 adapters  Memory    / approval
                   / capability
                  / sandbox
            RunStore + Trace + Evidence
```

All product entry points normalize work into the same `TurnRequest`. The scheduler preserves per-session order while separating foreground and background capacity. The Agent loop builds bounded context, selects a routed provider, validates typed tool calls, and persists terminal state through the same runtime regardless of transport.

## Core Boundaries

| Boundary | Responsibility |
| --- | --- |
| Spine | Turn identity, state transitions, scheduling, cancellation |
| Provider runtime | Protocol normalization, usage accounting, fallback |
| Context and memory | Token admission, compaction, retrieval, consolidation |
| ToolGateway | Schema validation, effect classification, approval, capability, sandbox |
| RunStore and tracing | Crash-safe state, call ledger, semantic trace, replay evidence |
| Evaluation | Isolated workspaces, paired experiments, fault/red-team campaigns, release bundles |
| Evolver | Bounded candidates, sealed scoring, statistical gates, human activation, rollback |
| Product surfaces | CLI, TUI, local gateway, channel adapters, persistent cron |

## Request Lifecycle

1. An entry point submits a normalized request to `RuntimeHost`.
2. `Scheduler` deduplicates accepted work and enforces session ordering.
3. `TurnRuntime` records the Turn before execution and exposes cancellation.
4. `RepoAgent` assembles context under the model window and output reservation.
5. The routed provider returns text, tool calls, usage, and routing metadata.
6. Tool calls pass through argument validation, effect-aware approval, scoped capability checks, deadline/output limits, and the selected sandbox adapter.
7. Every step updates task state, trace, call ledger, checkpoints, and evidence.
8. Terminal state is persisted before the result is delivered to the originating transport.

## Controlled Evolution

The Evolver cannot directly rewrite production policy. A candidate is bound to failure evidence, an exact base commit, a narrow mutation allowlist, and resource budgets. It is applied in a detached worktree, passes deterministic checks and paired evaluation, receives an explicit one-time human confirmation, and is then appended to a tamper-evident activation ledger. Production routing is derived by replaying that ledger; rollback appends history instead of deleting it.

## Evidence Scope

The offline contract suite proves deterministic runtime invariants such as tool boundaries, recovery behavior, terminal artifacts, and verifier outcomes. It does not prove competitive coding performance. Coding-quality claims require a tagged release bundle from a representative public or held-out benchmark, with model, task denominator, commit, raw rows, and limitations retained.

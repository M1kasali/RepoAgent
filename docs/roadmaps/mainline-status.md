# Mainline Status

Reconciled 2026-09-12 against the implementation ledger and current roadmap.
This is a delivery-status reconciliation, not a new full-source parity audit.
Implemented behavior and measured effectiveness are separate claims.

## Implemented

| Area | Delivered scope | Evidence boundary |
| --- | --- | --- |
| Runtime and scheduling | Turn lifecycle, session ordering, concurrency quotas, cancellation and persistence | Own full paired live performance campaign remains pending |
| Providers and cost | Native requests, usage/cost ledgers, retries and budget admission | Provider coverage and live cost improvements are not universally established |
| Tools, MCP and sandbox | Typed gateway, bounded reads, three MCP transports, shared Docker lifecycle | Not complete BoxLite parity; MCP automatic OAuth is not claimed |
| Context and Skills | Budget assembly, compaction, retrieval/fusion/admission, lazy hydration | Skill live effectiveness remains unverified; TECH-109 |
| Local memory | Existing local memory plus opt-in SQLite/FTS5, provenance and track isolation | SQLite is independent, not Myna; one controlled DeepSeek recall case passed |
| Tracing and evaluation | Correlation, retained receipts, replay and paired evaluation infrastructure | Own mainline metric campaigns remain pending; no borrowed resume numbers |
| Subagents and Evolver | Budgets, messaging, isolated candidate execution, multi-round search, sealed checks, human approval and activation | Real-model effectiveness remains unverified; TECH-102 through TECH-108 |
| Product surfaces | CLI, native terminal, RPC, session/model management, durable directory Gateway | Optional QQ is fixture-tested, not live-platform accepted |
| Coding verification | Source-bound unittest records, freshness checks, native prompt propagation | Success/adoption smoke passed; live failure-to-repair case is still needed |

## Remaining Work

- M6-09: one real-model failed-test, repair, retest and normal-completion case.
- M5-01: module-specific paired acceptance for the mainline, with own workloads,
  denominators, costs and immutable evidence. Infrastructure is not itself a
  measured improvement.
- Release evidence: freeze the current source after commit. Existing v0.1.1
  release evidence does not cover all subsequent development changes.

## Deferred

- Original Pico Myna: source/version identity and availability unresolved;
  deferred by user. The withdrawn CodeCairn bridge is not a substitute.
- Feishu/WeCom adapters and additional platform work: paused by user.
- QQ real-platform acceptance: pending explicit platform setup, not a reason to
  expand other integrations now.
- Polyglot: input-policy corrections, snapshot-overhead diagnosis, clean canary
  rerun and the 225-task campaign remain deferred; they do not block this slice.

## Current Sequence

1. Verify and commit the retained SQLite implementation and status corrections.
2. Execute M6-09 on clean source with synthetic data, isolated tools, bounded
   real-model calls and independent unchanged-test verification.
3. Record the actual outcome, including failure or incomplete execution. Do not
   generalize a single controlled case into a recovery success rate.

Details and historical evidence are in the
[implementation ledger](../architecture/implementation-ledger.md) and
[implementation plan](agent-harness-implementation-plan.md).

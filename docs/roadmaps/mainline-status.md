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
| Coding verification | Source-bound unittest records, freshness checks, native prompt propagation | Recovery on 52a3418 repaired code (independent 6/6) but did not retest or complete; TECH-140 |

## Remaining Work

- M6-09: repair the observed context eviction/repeated-read failure and obtain
  a real failed-test, repair, retest and normal-completion case. The first
  bounded run failed; the scoped reduction fix passed regression, but its live
  rerun repaired code without retesting or completing. Acceptance is still open.
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

1. Done: retained SQLite and status corrections committed as 252c157; full
   regression passed 1,284 tests with 43 skips.
2. Done: one bounded M6-09 run on clean source, with unchanged independent
   tests, was recorded as failed. Eight model calls / 16.57 seconds, 12 tool
   calls, no source repair and Runtime stopped at the step limit.
3. Done: selective old-output reduction committed as 52a3418; 1,287 tests
   passed with 43 skips. Same-budget rerun repaired the code (independent 6/6)
   but did not rerun tests and stopped at the step limit (TECH-140).
4. Next: diagnose lost mutation evidence and test-failure interpretation using
   offline regressions before further live acceptance. Do not increase budgets
   merely to claim success or generalize a single case into a recovery rate.

Details and historical evidence are in the
[implementation ledger](../architecture/implementation-ledger.md) and
[implementation plan](agent-harness-implementation-plan.md).

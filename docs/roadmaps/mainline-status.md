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
| Coding verification | Source-bound unittest records, freshness checks, native schema-aware prompts, live budget feedback and revalidation guidance | Bounded failure/repair/retest/normal completion passed on dc432b3; one controlled case, TECH-148 |

## Remaining Work

- M5-01: module-specific paired acceptance for the mainline, with own workloads,
  denominators, costs and immutable evidence. Infrastructure is not itself a
  measured improvement.
- Formal release: local clean candidate 6cd7ade passed build, isolated wheel
  install and 12/12 scripted contracts (TECH-149). A version/tag decision and
  full release/CI workflow remain pending; the old v0.1.1 tag is unchanged.

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
4. Done: test-result semantics and conditional next-step guidance committed as
   65972a4 and 6e1500e; full regression 1,294 passed, 43 skipped. Two bounded
   runs reached repair/retest (6/6) but not normal completion (TECH-142/144).
5. Done: native schema-aware prefixes and live budget feedback in e5ac7db and
   dc432b3. Full regression 1,299 passed, 43 skipped. M6-09 passed in four model
   calls / six tool executions / 12.45 seconds, with unchanged limits (TECH-148).
6. Done: clean candidate 6cd7ade, package install, 12/12 runtime contracts and
   evidence consolidation (TECH-149); see [release notes](../release.md).
7. In progress: M5-01 tool microbenchmarks now cover simulated delays and real
   warm local reads (TECH-150). Both preserve results, but the local-file
   treatment is slower; no general coding speedup is claimed.
   Scheduler capacity comparison also passes 640 synthetic executions with
   per-request timing, conservation and FIFO checks (TECH-151). Same-session
   foreground requests intentionally cannot bypass earlier background turns.
   Trace append measurements now retain 3,600 verified events and paired timing
   samples (TECH-152); local per-event delta P95 is 6.92-7.25 ms, not a claim
   about whole-request overhead or negligible instrumentation cost.
   Seven Runtime accounting cases also pass with eight scripted provider
   invocations (TECH-153). Missing usage/pricing suppresses complete unit cost;
   real provider cost reduction has not been established by these fixtures.
   Context/SQLite visibility acceptance is recorded (TECH-154): one of two
   tight contexts assembles, one is rejected at section floors; all three
   shared-versus-isolated track probes pass. No reread or answer-quality claim.
   Follow-up fix (TECH-155) relaxes only inferred optional-content floors when
   necessary. Both original tight workloads now assemble within 3,000 tokens;
   explicit floors and system-prefix protection remain unchanged. The earlier
   rejected receipt is retained, not rescored.
   Full regression passes 1,326 tests / 43 skips with inherited secret variables
   removed only in the test child process. An unfiltered run had 13 failures
   from existing streamed secret-prefix retention; that environment-sensitive
   output behavior is documented separately and is not fixed by TECH-155.
   Follow-up TECH-156 adds confirmed-completion stream finalization: ordinary
   unmatched suffixes are released; full secrets remain masked, and abnormal
   endings discard pending text. This addresses the prior `done` to `don`
   defect without removing inherited secrets from production or test commands.
   Original-environment regression now passes 1,352 tests / 43 skips, alongside
   accounting 7/7 and tight-context 2/2 rechecks; 227 receipt hashes verified.
8. Next: continue remaining M5-01 module measurements and decide whether to
   prepare a new formal release. Do not generalize the debugged fixture into
   a recovery rate, reopen deferred integrations or launch full Polyglot.

Details and historical evidence are in the
[implementation ledger](../architecture/implementation-ledger.md) and
[implementation plan](agent-harness-implementation-plan.md).

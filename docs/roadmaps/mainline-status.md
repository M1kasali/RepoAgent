# Mainline Status

Reconciled 2026-09-13 against the implementation ledger and current roadmap.
This is a delivery-status reconciliation, not a new full-source parity audit.
Implemented behavior and measured effectiveness are separate claims.

## Original-Protocol Follow-Up (2026-09-14)

The later user-requested comparison reopened specific compatibility work; it
does not retroactively make the September 13 closeout a full parity audit.

- [x] Add real INJECT/INTERRUPT scheduling, mailbox fallback and durable merged
  completion; connect injection to the Agent's next model boundary. See
  [Busy policies](../architecture/busy-policy.md).
- [x] Pass original R0 correctness predicates for 10,000 requests on each subject
  and independently verify 10,000 native journals (80 merged). Native state used
  tmpfs; ordinary-disk RunStore exceeded the original bulk timeout and remains
  a separate performance issue, not erased by this successful correctness run.
- [x] Support explicit, frozen benchmark mutation targets without changing
  product strategy allowlists; verify native materialization with the original
  small-real grader (comment-only candidate, not a model improvement result).
- [x] Run the original one-round live evolution and sealed validation; replay
  its sole candidate through native search and original scoring. Both yield
  training 40% -> 60%, test 25% -> 50%; 56 trial records match (TECH-171).
- [x] Add opt-in original single-module repair and focused-Fisher evaluation
  APIs; verify 1000 policy cases, 1000 Fisher tables and 52 original scoring
  records through native proposer/evaluator replay (TECH-172).
- [x] Connect automatic cold-start, lexicographic WHY selection, stratified
  sentinels and bounded multi-round per-parent training search (TECH-173).
- [x] Connect focused-search output to frozen, one-way sealed-retention
  finalization and JSON/Markdown reports (TECH-174). Full original-grader replay
  performs 68 scoring trials; one WHY/candidate per round remains the scope,
  without wider tree/archive or full orchestrator parity claims.
- Native test results above are replay, not an independent blind sealed campaign;
  original significance credit is false.
- [x] Resolve strict provider identity with the user-approved V4.1-Flash scope:
  request and raw response both use `deepseek-flash`. Official documentation
  confirms the retired v4 name redirects to V4.1; no identity guard was disabled.
- [x] Complete one fresh native live small-real evolution (TECH-175): one paid
  call, 68 original-grader trials, training 40% -> 60%, test 25% -> 50%, z=1,
  no two-sigma credit or activation. The original test split is already known;
  this is live functional acceptance, not a fresh blind benchmark campaign.
- [ ] Complete remaining same-protocol live/context/tracing/evolution comparisons.
  No historical resume numbers are adopted as RepoAgent measurements.

Latest full regression including focused sealed finalization:
1,511 passed, 52 conditional skips, six existing warnings (TECH-174).

## Delivery Decision

The currently agreed implementation scope is closed for this iteration.
Per user direction, stop expanding evaluation infrastructure and stop treating
future effect campaigns as prerequisites for delivering the application.
This is an implementation closeout, not a declaration of complete upstream
parity, production readiness or a new tagged release.

Latest verification: 1,423 ordinary tests passed, 52 conditional skips and six
existing warnings (TECH-164). Separately executed Docker integration covered a
48-trial hosted matrix with a fake provider; private corpus integration covered
24 scripted tasks (TECH-163). These are mechanism checks, not model quality.
Use README for installation/startup and the table below for feature boundaries.

## Implemented

| Area | Delivered scope | Evidence boundary |
| --- | --- | --- |
| Runtime and scheduling | Turn lifecycle, session ordering, concurrency quotas, cancellation and persistence | Own full paired live performance campaign remains pending |
| Providers and cost | Native requests, usage/cost ledgers, retries and budget admission | Provider coverage and live cost improvements are not universally established |
| Tools, MCP and sandbox | Typed gateway, bounded reads, three MCP transports, shared Docker lifecycle | Not complete BoxLite parity; MCP automatic OAuth is not claimed |
| Context and Skills | Budget assembly, compaction, retrieval/fusion/admission, lazy hydration | Skill live effectiveness remains unverified; TECH-109 |
| Local memory | Existing local memory plus opt-in SQLite/FTS5, provenance and track isolation | SQLite is independent, not Myna; one controlled DeepSeek recall case passed |
| Tracing and evaluation | Correlation, retained receipts, replay and paired evaluation infrastructure | Own mainline metric campaigns remain pending; no borrowed resume numbers |
| Subagents and Evolver | Budgets, messaging, isolated execution, multi-round search, behavioral grading, paired sealed checks, human approval/rollback and one-shot pilot runner | Real-model effectiveness remains unverified; TECH-102 through TECH-108 and TECH-159 through TECH-164 |
| Product surfaces | CLI, native terminal, RPC, session/model management, durable directory Gateway | Optional QQ is fixture-tested, not live-platform accepted |
| Coding verification | Source-bound unittest records, freshness checks, native schema-aware prompts, live budget feedback and revalidation guidance | Bounded failure/repair/retest/normal completion passed on dc432b3; one controlled case, TECH-148 |

## Separate Follow-Ups

- Real effect campaign, paused: combine corpus/intervention review, real
  provider/counter checks and explicit budget approval into one campaign-start
  check when requested. Do not create additional implementation phases for it.
  Infrastructure and fixture results do not establish measured improvement.
- Formal release, separate decision: bc18a86 previously passed clean package
  installation and scripted contracts (TECH-157); later changes are source
  development, not a newly accepted wheel/tag. Version remains 0.1.1 and the old
  v0.1.1 tag is unchanged. No version bump/publication is part of this closeout.

## Operational Limits

- Default execution is direct host execution; Docker isolation must be chosen
  explicitly. Approvals and sender allowlists are not authentication or a
  multi-tenant security boundary.
- Native TUI and MCP need their optional dependencies. RPC is a local,
  single-active-session connection. QQ is fixture-tested, not live accepted.
- Local/SQLite memory is available; Myna is not integrated. Memory and local
  configuration are not encrypted, and session deletion does not erase backups.
- Evolver experiments require explicit operator configuration. The hosted pilot
  runner needs a reviewed real-provider factory/counter and spending decision;
  it does not run automatically during ordinary coding sessions.
- Private datasets and local artifacts are deliberately outside product Git
  history. They need separate retention and must not enter candidate snapshots.

## Deferred

- Original Pico Myna: source/version identity and availability unresolved;
  deferred by user. The withdrawn CodeCairn bridge is not a substitute.
- Feishu/WeCom adapters and additional platform work: paused by user.
- QQ real-platform acceptance: pending explicit platform setup, not a reason to
  expand other integrations now.
- Polyglot: input-policy corrections, snapshot-overhead diagnosis, clean canary
  rerun and the 225-task campaign remain deferred; they do not block this slice.

## Historical Progress

The following chronology preserves previous results. References to "next" or
"pending" describe those milestones, not the current delivery queue. Current
scope is governed by Delivery Decision and Separate Follow-Ups above.

<details>
<summary>Implementation and verification history</summary>

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
8. Done: pushed clean candidate bc18a86 passed build, independent wheel install,
   156 module identity checks, five CLI smokes and 12/12 installed contracts
   (TECH-157). Regression 1,352 passed / 43 skipped; 367 evidence hashes checked.
   Version/tag unchanged; only local WSL Python 3.12 was exercised.
9. Next: continue remaining M5-01 module measurements and decide whether to
   prepare a new formal release. Do not generalize the debugged fixture into
   a recovery rate, reopen deferred integrations or launch full Polyglot.
   Nine opt-in Docker Evolver/Skills cases now pass (TECH-158), including
   deployment/rollback and tie rejection. Exact-file/candidate-only checks
   alone do not establish coding-quality gains.
   Behavioral JSON probes are now implemented (TECH-159): equivalent code can
   pass without matching reference bytes. Opt-in sealed baseline pairing is
   now implemented (TECH-160): origin-bound control, two-arm budget reservation,
   retained per-arm outcomes and recomputed win/tie/loss. No real-model
   improvement is claimed; task/protocol/budget freezing remains next.
   Behavioral-grader verification: 1,364 ordinary tests passed / 47 skipped;
   separately enabled Docker integration passed 25/25, including four new cases.
   Sealed-pair verification: 1,386 ordinary tests passed / 48 skipped; focused
   suite with Docker enabled passed 45/45 and 2,591 payload hashes verified.
   Offline pilot preflight is now implemented (TECH-161), including private
   external inputs, 12+12 split validation, intervention-only source changes,
   two-arm reservation and drift verification. It is NOT a live runner or a
   completed real corpus; M5-01k2/k3 remain open and no spending is authorized.
   Preflight verification: 1,406 ordinary tests passed / 48 skipped, focused
   checks 46/46 and 1,774 payload hashes verified. No new paid or Docker campaign.
   A private authored 12+12 microtask corpus now exists outside the checkout
   (TECH-162), with 75 cases and 72 isolated grader trials: 24 references passed,
   24 mutants and 24 stubs rejected. This is not an Agent/model campaign;
   independent review and paid runner binding remain pending. Corpus content is
   intentionally not in product Git history. Full task-workspace integration
   now passes 24/24 (TECH-163): real read/fail/write/retest/final/hidden-grade
   workflow, 144 scripted responses, no paid calls. This is not model efficacy.
   Snapshot-test verification: 1,407 ordinary tests passed / 51 skipped;
   explicit Docker/focused checks 44/44 and 3,501 payload hashes verified.
   The frozen hosted execution mechanism is now connected (TECH-164), including
   explicit receipt acknowledgement, exclusive start, per-trial durability and
   training/sealed win/tie/loss reports. A real Docker fixture ran 48 trials /
   96 fake model calls, with 24 ties and no promotion. Actual provider/counter
   validation, corpus review, intervention selection and paid approval remain
   open; no paid effectiveness result exists.
   Runner regression: 1,423 ordinary tests passed / 52 skipped; final focused
   checks 36 passed / one separately executed Docker skip. All 1,249 retained
   payload hashes and the 48 model journals/trial receipts were verified.

</details>

Details and historical evidence are in the
[implementation ledger](../architecture/implementation-ledger.md) and
[implementation plan](agent-harness-implementation-plan.md).

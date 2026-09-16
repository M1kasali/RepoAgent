# Issue Maintainer Demo Plan

Started: 2026-09-14. Branch: `feat/issue-maintainer-demo`.
Harness baseline retained on main: `cc7bc63`.

## Scope

A maintainer explicitly supplies a public GitHub issue and local repository.
The application snapshots the issue, binds an immutable revision, investigates
in isolation and delivers an evidence-backed report. A separate explicit command
may create a candidate repair and verify it independently. Ordinary coding CLI
behavior remains unchanged. No webhook, chat platform, UI, external comments,
automatic PR/merge, multi-agent runtime or new self-evolution work is required.

This is a demo validated on historical public cases, not a deployed customer
service. Historical fixes must not enter model-facing input. Public cases may
already be known to the model; do not claim blind benchmark generalization.

## Checklist

### P0: Baseline and Architecture
- [x] Verify clean main and create independent development branch.
- [x] Write staged checklist and technical implementation record.
- [x] Inspect existing CLI, Agent execution, sandbox and evidence interfaces.
- [x] Freeze the smallest supported configuration and trust boundaries.

### P1: Select a Reproducible Case
- [x] Select a primary historical issue and backup from primary sources.
- [x] Pin buggy revision; verify deterministic reproduction in Docker.
- [x] Preserve source attribution; separate maintainer oracle from Agent input.

### P2: Intake and Case State
- [x] CLI issue intake, local snapshot input for offline/demo use, bounded GitHub reads.
- [x] Validate execution config (URL, repository identity and immutable revision done).
- [x] Durable local case record, exclusive execution, explicit failure states.
- [x] Unit tests for malformed input, unwanted URLs and repeated/interrupted work.

### P3: Investigation Loop
- [x] Reuse native Agent and tools within an isolated, disposable target workspace.
- [x] Store model/tool evidence and produce Markdown/JSON reports.
- [x] Distinguish needs-information, blocked, not-reproduced and reproduced.
- [x] Require executable evidence; model statements alone do not prove reproduction.
- [x] Automated success, failure and isolation tests.

### P4: Explicit Repair and Independent Verification
- [x] Separate repair command; protect original checkout and frozen verifier.
- [x] Collect minimal patch and changed files; never automatically publish.
- [x] Apply patch to clean baseline and execute the same verification protocol.
- [x] Verify baseline fails and repaired code passes; report regressions/blocked states.
- [x] Tests for evidence mismatch, protected files and verifier failure.

### P5: Acceptance and Demo Material
- [x] Run at least one real V4.1-Flash investigation/repair workflow.
- [x] Exercise insufficient-information and environment failure paths with fixtures.
- [x] Run affected and full regression tests; inspect cleanup and baseline isolation.
- [x] Record reproducible commands, measured outcome, limitations and resume wording.
- [x] Reconcile this checklist with actual delivered scope before closing the goal.

## Completion Evidence

Demo scope completed on 2026-09-14. One real historical issue reached
`candidate_ready` with 22 model calls and five fixed checks passing after patch
application to a clean export. This is not a generic success-rate benchmark.
Full regression: 1550 passed, 54 skipped; subsequently added digest regressions
and formatting were covered by the affected suite: 52 passed including Docker.
JUnit results are in `artifacts/issue-demo-live/`. Original target checkout is
clean; main remains `cc7bc63`. No commit, push or external publication performed.

Remaining non-goals: UI/webhooks/chat adapters, deployment, multiple real cases,
autonomous oracle generation, full upstream suites and adversarial-proof grading.
See `docs/issue-maintainer-demo.md` for exact commands, evidence and limitations.

## Working Rules

Update checkboxes incrementally. Append concrete implementation and test evidence
to `docs/architecture/issue-maintainer-demo.md`; raw cases, checkouts, credentials
and model evidence are not product Git files. Do not claim a stage complete only
because APIs exist. Investigate ordinary failures autonomously; ask the user for
new sensitive access, public writes, major scope changes or essential ambiguity.
No commit/push/merge unless subsequently requested.

## Follow-up: Terminal Demonstration (2026-09-14)

The initial demo was committed/pushed as `be6a82a` at the user's request.
This follow-up changes presentation only, not verification or model strategy.

- [x] Keep default JSON output compatible and add opt-in text summaries.
- [x] Add actual phase progress on stderr, without exposing model/source bodies.
- [x] Inspect the completed local case without additional model calls.
- [x] Document commands for saved-result inspection and new execution.
- [x] Run presentation, workflow and existing CLI regressions including Docker.

Follow-up verification: 59 passed, including real Docker; Ruff and diff checks
passed. JUnit: `artifacts/issue-demo-live/presentation-regression.xml`.
The full Harness suite was not rerun for this presentation-only follow-up.

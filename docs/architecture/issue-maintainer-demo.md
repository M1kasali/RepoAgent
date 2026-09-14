# Issue Maintainer Demo: Technical Record

## IM-001: Scope and Baseline (2026-09-14)

Development is isolated on `feat/issue-maintainer-demo`, starting from `cc7bc63`.
The existing main branch and general coding interfaces remain the Harness baseline.
The new application adds an issue-oriented workflow rather than renaming the
Harness or claiming existing evaluation APIs already implement a support product.

The intended first surface is a maintainer-invoked CLI with local case storage.
Public issues and supplied reproduction code are untrusted data. External writes
are out of scope. Repair is explicitly requested, not automatic after intake.
Execution must use bounded isolation without passing model credentials into the
target workspace. Model conclusions and executable acceptance are separate.

Demo implementation is complete. Progress and acceptance conditions are in
`docs/roadmaps/issue-maintainer-demo-plan.md`. Research and raw demonstration
material are retained under ignored `artifacts/` paths.

## IM-002: Case Selection and Intake

Primary case: python-dotenv #360, buggy revision
`fc138ce8a430b758f4f2c89bc8104f259e2cba38`. A missing newline makes a new binding
concatenate with the existing binding. Source import reproduces deterministically
in a no-network Python 3.12 container, without installation or remote services.
Source: https://github.com/theskumar/python-dotenv/issues/360
Backup: Click #2500. Private historical-control details remain in
`artifacts/issue-demo-selection/SELECTION.md`, outside model-facing input.

`repoagent issue open URL --repo PATH --revision SHA [--snapshot JSON]` validates
public GitHub issue URLs, exact revisions and matching local origin before intake.
`issue show CASE_ID` reads persisted state. Intake retains only title/body; comments,
linked patches and development metadata are excluded. Offline snapshots must use
the same URL. Body content can still contain suggestions; historical demo input
is an explicitly curated behavior-only snapshot, not an alleged blind raw issue.
HTTP and local inputs are bounded, case IDs are generated, and case storage may
not overlap the target checkout. Existing atomic JSON persistence and file locks
are reused. Initial intake tests: 10 passed. CLI and execution tests follow.

The installed GitNexus index is from July and predates the current modules;
implementation inspection uses current source rather than treating that index
as authoritative. No index metadata or unrelated source was changed.

## IM-003: Native Execution and Failure Diagnosis

The host exports an exact Git tree without checkout hooks, copies the Harness
and json-repair dependency into a disposable Docker workspace, and launches the
normal Agent tool loop. Model calls cross the existing stdio RPC to a host-owned
budgeted provider; API credentials and acceptance probes are not mounted in the
Agent workspace. Network is disabled, container capabilities are dropped, and
memory/process/time/output limits reuse existing adapters. Target repositories
are limited to small regular-file Python source trees without installation.

The demo freezes a digest-pinned Python image, source path, permitted repair
files and maintainer-authored Python acceptance probe. It is not an autonomous
test-oracle generator. Investigation cannot alter existing source; repair may
only alter explicitly permitted existing files. Missing body, infrastructure
failure, non-reproduction, stopped Agent and failed verification have distinct
outcomes. A stopped Agent cannot become a completed reproduced investigation.

Live diagnosis exposed two integration problems: dataclass conversion of native
immutable tool schemas failed before a provider call; and the legacy synchronous
provider path dropped structured history. The issue application now uses the
existing JSON serializer and collects results through the existing structured
streaming provider path. Regression tests cover both. Generic provider APIs are
unchanged. A further attempt exhausted 12 calls after successfully reproducing
the bug; the bounded demo now allows 24 calls per phase, explicitly describes
unavailable pytest/mock dependencies and requests a stdlib-only check.

Unsuccessful cases remain under `artifacts/issue-demo-live/cases`; they are not
successes. Case `issue_c955ff15d3f244d488b1ae7a` completed investigation in 11
provider calls. Repair subsequently completed with another 11 calls.

## IM-004: Repair and Independent Verification

`issue fix` requires a completed reproduced investigation and unchanged config
digest. Repair runs in a separate source export. Only permitted existing files
may change; additions, deletions, symlinks and edits to tests/protected files fail
scope validation. `.issue/` and `.repoagent/` hold disposable scratch/session data,
not deliverable source.

The host generates a unified diff, including missing-final-newline markers.
A fresh export applies it with `git apply --no-index`, checks exact resulting
bytes and runs the frozen probe. The delivered patch digest must match the one
verified. The verifier's source/probe mount is read-only; scratch files use /tmp.
A per-run completion marker rejects early process exit 0 before the probe finishes.
This is not a guarantee against adversarial code forging an in-process check.

Real acceptance case `issue_c955ff15d3f244d488b1ae7a` reached `candidate_ready`.
Only `src/dotenv/main.py` changed; the pinned baseline exited 1 with the expected
assertion and the clean patched tree exited 0 after all five maintainer checks.
The target checkout remained clean. No external issue/PR action was performed.
Input/config fixtures under `tests/fixtures/issue_demo` are semantically identical
to the local real-run inputs. Instructions and resume scope are in
`docs/issue-maintainer-demo.md`.

## IM-005: Regression Evidence

Initial affected tests including real Docker: 50 passed. Docker tests exercise
native tool RPC with a scripted provider, absence of a host sentinel secret,
withheld oracle input, read-only probes, exact patch application and clean source
verification. Scripted-provider tests establish mechanics, not model quality.

The first full suite had 1549 passed, 53 skipped and one failure: a new test called
the real local environment loader and contaminated a later default-model test.
The fixture now stubs that loader; the failing ordering passes (18 tests).
Full-suite rerun: 1550 passed, 54 skipped, six pre-existing datetime deprecation
warnings. The subsequently added config/patch-digest tampering tests and final
formatting passed the affected suite: 52 passed including real Docker.
JUnit files: `artifacts/issue-demo-live/full-regression.xml` and
`artifacts/issue-demo-live/issue-docker-regression.xml`. Ruff and diff whitespace
checks passed. Original target checkout is clean and no Docker workers remain.

Direct public GitHub intake also succeeded without a snapshot:
`artifacts/issue-demo-intake/cases/issue_d4afae50bbdd4db889dbbf3b` is a received-only
case with original title/body. It did not trigger a paid model call. This intake
check is separate from the curated historical end-to-end demo.

All completion statements are limited to the planned CLI demo. No public writes,
production deployment, main-branch merge, commit or push were performed.

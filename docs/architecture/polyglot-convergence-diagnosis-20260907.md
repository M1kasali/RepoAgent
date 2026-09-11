# Polyglot Convergence Diagnosis

Date: 2026-09-07. Diagnosis baseline: `b031b9a`; evaluated candidate: `2f310fa`.

## Scope and Evidence

This is an offline diagnosis of the four code-pass/non-converged attempts in
`artifacts/polyglot-live/deepseek-v4-flash-canary24-c42e2bb-20260904/`.
The original runtime was `c42e2bb`, not the current baseline. Historical results
are not rewritten or attributed to the new patch.

The reproducible extraction is retained in
`artifacts/verifications/convergence-diagnosis-b031b9a-20260907/`.
Its manifest records the extraction command, source provenance and output hashes.
`four-task-audit.stdout.txt` binds the input results and traces by SHA-256, verifies
all six files in each of the four Agent evidence bundles, and preserves request
numbers, tool-call IDs, timings, errors and terminal states. Long command/result
previews are truncated; the original traces remain authoritative.

The hidden grader ran after each Agent turn. A later hidden-code pass does not
mean the Agent knew its code was correct while deciding whether to finish.

## Findings

Request numbers below come from `model_requested.attempts`. Tool-step counts are
separate: a request can return multiple tools, an empty response or a summary.

| Task | Calls / tool steps | Observed failure path | Terminal boundary |
| --- | --- | --- | --- |
| `rust/accumulate` | 12 / 12 | Implementation written at request 4. Request 7 compiled a host absolute path inside Docker and failed. Request 9 compiled to `/tmp` but could not execute the binary. Request 10 successfully ran its own checks from the workspace. Request 11 attempted `git_status` as a shell executable during cleanup and failed. | Tool budget, then a forced summary |
| `rust/acronym` | 13 / 12 | Repeated directory/file and compiler discovery before writing the implementation at request 9. Request 11 used a host absolute source path inside Docker and failed; request 12 rediscovered the container working directory. | Tool budget, then a forced summary |
| `rust/book-store` | 13 / 12 | Implementation written at request 6. Request 7 could not execute its Cargo binary under `/tmp`. Request 8 tried to reuse that temporary project in a new container, where it no longer existed. Requests 10-11 tested a Python translation, first with an incorrect expected value. Request 12 attempted `git_status` as a shell command. | Tool budget, then a forced summary |
| `go/alphametics` | 14 / 11 | Thinking-only recovery at requests 3, 6 and 7; request 5 hit `max_tokens` and produced a rejected, incomplete write. Request 8 wrote the implementation. Requests 10-11 wrote local tests. Requests 12-13 timed out; request 14 never started its shell command. | Provider-call budget, without another synthesis call |

### Confirmed: Shell Namespace and Lifetime Are Not Communicated

`WorkspaceContext.text()` describes host `cwd` and `repo_root`.
`DockerSandboxAdapter.execute()` mounts the repository at
`/workspace/<workspace-name>` and starts the shell there. Two recorded Rust
commands used the host `/mnt/c/...` path, and the compiler reported that the source
did not exist. This establishes a namespace mismatch, not a broken Rust solution.
That missing execution context contributed a plausible opportunity for wasted
calls; a live causal effect on convergence has not yet been measured.

The adapter also creates a fresh container per call. The `book-store` trace shows
that a `/tmp` project was absent in the next call. Both `accumulate` and
`book-store` encountered execution denial under `/tmp`; `accumulate` subsequently
ran the same style of check from the mounted workspace successfully.

These are reasons to expose backend facts, not to remove `no-new-privileges`,
enable networking or make the container persistent. The historical pico comparison
used RepoAgent's injected Docker executor, so its result alone cannot validate this
host/container integration contract. The pico reference's generic context also
renders its configured workspace, and its forced exhaustion summary deliberately
remains interrupted; neither is a justification for changing our success criteria.

### Strong Lead: Go Snapshot Work Competes With Command Time

After `go vet`, the trace reports 412 changed paths, predominantly under
`.repoagent-home/.cache/go-build`. Two subsequent test-file writes took
14.026 and 14.604 seconds. Later commands reported another 332 and 109 changed
paths. The final command requested a ten-second timeout, but its tool result was
`tool execution did not start`, with a total duration of 25.769 seconds.

The Gateway creates `ToolExecutionControl` before its pre-execution workspace
snapshot. `capture_workspace_snapshot()` recursively enumerates and hashes files,
and `.repoagent-home` is not excluded. A snapshot can therefore consume command
admission time. The current trace does not separate snapshot, Docker startup and
process time, so it cannot attribute the earlier Go timeouts quantitatively. This
requires a separate controlled cache-size experiment; it is not fixed by the
environment-context patch. Do not simply ignore all workspace changes, which
would weaken mutation evidence.

### Other Factors

- The two `git_status` shell invocations confuse a Tool API with an executable.
- `acronym` repeated successful discovery calls; it was not a hard-error retry loop.
- Go's truncated write and thinking-only responses consumed calls before a usable
  implementation existed. Existing recovery and schema rejection behaved as
  designed in these events.
- Forced summaries are useful delivery, not evidence of natural convergence.
- No observed trace supports treating all four failures as one generic final-answer
  prompting bug or claiming that raising the budget alone will solve them.

## Candidate Implemented for Verification

One candidate changes model-visible Docker execution facts only. The adapter
describes its actual shell directory, persistent workspace mount, fresh-container
lifetime, temporary-file limitations, read-only root and disabled network. The
same path helper now drives both the command argv and the description.

The context is included near the start of the existing stable prefix before
normal token admission. An initial test placing it at the tail demonstrated that
normal prefix clipping could discard it, so it is not appended after admission.
No hint is persisted as a Tool result or session message. Direct/injected backends
and tool sets without `run_shell` retain the original prefix. No tools are enabled,
no shell commands are rewritten, and neither budgets nor successful-stop semantics
change. This is an environment-contract correction, not a demonstrated quality gain.

The no-model Docker probe passed on Docker 29.7.2 with the same immutable image
as the historical canary. It confirmed the advertised working directory, a
read-only root, `/tmp` execution denial, retained workspace files and discarded
`/tmp` files across calls. Its checksummed output and exact probe command are in
`artifacts/verifications/sandbox-environment-smoke-20260907/` (local, ignored).

Full development verification passed 637 tests in 107.51 seconds, plus Ruff,
diff checks, evaluation CLI and Polyglot plan checks. Six existing deprecation
warnings remain. Logs and JUnit XML are retained under
`artifacts/verifications/sandbox-execution-context-20260907/`.

## Frozen Development Protocol

- Baseline: clean `b031b9a`; candidate: a clean commit containing only this
  environment-context change and its tests/docs. Do not run a dirty live candidate.
- Development tasks: the four diagnosed tasks plus `go/book-store`,
  `javascript/beer-song`, `python/affine-cipher` and `python/bottle-song`, which
  previously passed. These are deliberately selected development tasks, not a
  held-out sample or evidence of general superiority.
- Use one attempt per task per variant; interleave each pair. Fix the dataset,
  image digest, model, protocol, temperature, context budget, output budget,
  Tool budget and Provider-call ceiling identically. Preflight both variants and
  admit the complete paired budget before any paid request.
- Retain the historical configuration for this candidate: `deepseek-v4-flash`,
  Anthropic-compatible protocol, temperature 0.2, 12,000 input tokens, 4,096 output
  tokens, a 1,000,000-token context-window override, 12 Tool steps and 14 Provider
  calls per attempt. Use dataset commit `7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f`
  and the image digest recorded in the Docker probe; freeze one pricing snapshot
  and an explicit total spending ceiling during budget admission.
- Compare end-to-end pass, hidden-code pass, natural convergence, namespace and
  temporary-file errors, calls, cost and duration. Count every failed attempt.
- Stop on isolation/provenance/budget failure. Do not keep rerunning individual
  tasks until a favorable result appears. A net quality loss or new regression
  blocks promotion; an unchanged result does not justify an improvement claim.
- No live quality experiment was launched during the initial diagnosis. Verify the real
  container contract without a model first. Measure Go snapshot overhead as a
  separate candidate, not part of the same treatment.
- Only after a useful development signal, repeat the complete 24-task confirmation
  canary. Because that set has already informed debugging, do not call it an
  untouched holdout. The 225-task release remains gated.

## Eight-Pair Live Result

The protocol above subsequently completed all 16 attempts on 2026-09-07. Control
was clean `b031b9a4346da342b1c094445a22e7b940378c2a`; treatment was clean
`2f310faa576e1ae6f226d0e6d48a37d200642cf4`. Both ran from separate detached
worktrees, not the documentation worktree. No task was rerun or skipped.
Task order was fixed, alternating control-first and treatment-first by pair.
All per-campaign engineering gates passed, including source stability, execution
coverage, infrastructure errors, call limits and complete task-cost accounting.

Model/runtime settings remained those listed above. Dataset commit was
`7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f`; the Docker image was
`repoagent-polyglot@sha256:abb4183a827978e195474e6a0594ffa59916a1efcd625ecde6ab5d3386381096`.
Both variants used Windows-backed workspaces through WSL/Docker Desktop, with
1 GiB memory, one CPU, 128 PIDs and no container network.

The [official price snapshot](https://api-docs.deepseek.com/quick_start/pricing/)
used USD 0.44 input/cache-miss, 1.32 output and 0.014 cache-read per million tokens.
These peak-rate estimates replace the older experiment's price assumptions for
this run only; they are not invoices. Admission allowed USD 0.17 per attempt,
USD 2.72 total, with a conservative USD 2.568192 upper estimate including probes.

| Metric, eight tasks per variant | Control | Treatment |
| --- | ---: | ---: |
| End-to-end passes | 2/8 | 2/8 |
| Hidden-code passes | 4/8 | 4/8 |
| Naturally completed turns | 2/8 | 5/8 |
| Task Provider calls | 92 | 74 |
| Tool steps | 78 | 64 |
| Sum of attempt durations, seconds | 1157.346 | 1275.838 |
| Estimated task cost, USD | 0.119996 | 0.106079 |

Durations include grading and applicable preflight work; they are not a pure
model or Tool microbenchmark. Costs exclude the two uncached Provider probes
(one per variant). There were 166 task calls plus two probe calls. All task
costs were complete; combined estimated task cost was USD 0.226074584.
Fewer calls (19.6%) and lower task cost (11.6%) coincided with greater total
duration (10.2%), not a quality improvement.

`E/C/N` below denotes end-to-end pass, code pass and natural completion. `1` means
the criterion passed. A forced summary remains unsuccessful.

| Task | Control E/C/N | Treatment E/C/N | Calls, control/treatment |
| --- | --- | --- | --- |
| `go/alphametics` | 0/0/0 | 0/1/0 | 10/13 |
| `rust/accumulate` | 0/1/0 | 0/0/1 | 13/7 |
| `rust/acronym` | 0/0/0 | 0/0/1 | 13/9 |
| `rust/book-store` | 0/0/0 | 0/0/0 | 13/13 |
| `go/book-store` | 0/1/0 | 0/1/0 | 14/14 |
| `javascript/beer-song` | 1/1/1 | 0/0/1 | 10/7 |
| `python/affine-cipher` | 1/1/1 | 1/1/1 | 6/6 |
| `python/bottle-song` | 0/0/0 | 1/1/1 | 13/5 |

The end-to-end comparison is **1 win, 6 ties, 1 loss**, with exact two-sided
McNemar p=1.0. The existing zero-paired-regression gate fails on
`javascript/beer-song`; the win on `python/bottle-song` does not cancel that gate.
This small, selected development sample neither establishes superiority nor
statistical equivalence. The environment contract is verified as factual, but
its promotion as a quality optimization is not supported. No 24-task confirmation
or 225-task campaign was launched from this result.

### Mechanism and Failure Review

Manual trace review counted affected tasks, not error strings or independent
statistical samples. These are post-hoc observations, not new scoring rules:

| Observed environment failure | Control tasks | Treatment tasks |
| --- | --- | --- |
| Host source path used inside Docker | `rust/book-store` (request 10) | None observed |
| Execution denied under `/tmp` | `rust/accumulate` (10), `rust/acronym` (11) | `rust/acronym` (6), `rust/book-store` (8) |
| Reuse of discarded `/tmp` project/files | `rust/acronym` (12), `rust/book-store` (11), `go/book-store` (8) | `go/alphametics` (5-6), `rust/book-store` (9) |

The context did not eliminate temporary-file mistakes. The treatment's Rust
book-store attempt validated algorithms in scratch files but never replaced the
target implementation before the step limit. Its Rust acronym implementation
missed camel-case handling; its own checks also initially used an incorrect
expected value. A shell status of `ok` was not treated as proof that self-tests
passed: pipelines and trailing cleanup commands sometimes masked nonzero exits.

The control Go alphametics attempt exhausted empty-response recovery without
writing its solution. Both Go book-store attempts passed hidden tests but failed
to finish naturally. Large Tool durations remain visible; this experiment does
not separate snapshot hashing, Docker startup and command execution costs.

### Input-Contract Risk

The visible Rust accumulate instructions explicitly direct the solver to tests
for the expected signature, while the append document suggests generalizing
against those tests. The visible skeleton fixes `Vec<i32>`, but the runner
withholds the tests that require generic input/output and a mutable closure.
The JavaScript beer-song instructions and skeleton do not specify whether the
return value is a string or an array; the hidden grader requires an array.
Treatment used a string and failed, while control happened to return an array.

Both variants received identical frozen inputs, so the paired observations remain
valid for this protocol. However, attributing every failure to coding ability
would be unjustified. This is a custom hidden-test protocol over the Polyglot
dataset, not evidence of an official Aider leaderboard score. No grader content
was sent back to these attempts and no result was rescored after this finding.

Before further paid quality tuning, audit the model-visible interface contracts
and build metadata across the canary. Distinguish public task requirements from
hidden assertions/reference solutions; do not silently expose the current grader
or insert task-specific answers. Any input-policy correction must have a new
contract/digest and a newly frozen paired experiment. Separately measure Go
snapshot overhead without a model before changing deadlines or cache tracking.

### Evidence and Reproduction

Local, ignored evidence is retained in
`artifacts/experiments/environment-pair-20260907/`. Active `plan.json` binds the
driver hash, clean sources, dataset, image, schedule and budget. `progress.json`,
`summary.json`, eight paired reports and all 16 per-attempt outputs retain failures
as well as passes. The preflight-v1 driver/plan are archived setup versions, not
additional paid trials.

`analyze.py` revalidates the plan/driver binding, result and log hashes, clean-source
identities, task/runtime pairings and all 96 files in the 16 Agent bundles. It
also records the five source-file hashes supporting the input-contract findings.
`analysis.json` contains totals and request-indexed timelines; `audit-manifest.json`
checksums the local experiment files. All 274 recorded files passed an independent
hash/size check. Timeline previews are truncated; original traces remain
authoritative. The offline audit passed and made zero Provider calls.

```bash
.venv/bin/python artifacts/experiments/environment-pair-20260907/analyze.py
```

The live driver intentionally refuses automatic resume/rerun. Provider model
aliases, remote cache state and stochastic outputs prevent a promise of identical
future live results. Offline evidence reconstruction is distinct from rerunning
the model. These observations are not resume-ready quality-improvement claims.

Fresh closeout verification passed 637 tests in 104.54 seconds, with the six
existing deprecation warnings, plus Ruff, diff checks, evaluation CLI and
Polyglot plan checks. The checksummed logs and JUnit XML are in
`artifacts/verifications/environment-pair-closeout-20260907/`. Only verification
notes were updated after this check; the tested runtime did not change.

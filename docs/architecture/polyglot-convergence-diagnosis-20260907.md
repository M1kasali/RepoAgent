# Polyglot Convergence Diagnosis

Date: 2026-09-07. Current baseline: `b031b9a`.

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

## Next Experiment

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
- No live quality experiment was launched in this diagnosis. Verify the real
  container contract without a model first. Measure Go snapshot overhead as a
  separate candidate, not part of the same treatment.
- Only after a useful development signal, repeat the complete 24-task confirmation
  canary. Because that set has already informed debugging, do not call it an
  untouched holdout. The 225-task release remains gated.

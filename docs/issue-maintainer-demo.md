# Issue Maintainer Demo

## Business Workflow

Audience: a Python open-source maintainer investigating an actionable bug report.
The maintainer chooses an issue, an immutable buggy revision and an acceptance
probe. RepoAgent investigates the source and returns evidence. Only a separate
explicit repair command requests a candidate patch. A fresh environment applies
that patch and runs the frozen probe before reporting `candidate_ready`.

This branch adds an application over the existing Harness. It is not a new
generic Agent framework, deployed support service or automatic GitHub bot.
The original Harness remains on `main` at `cc7bc63`.

## Run the Historical Example

### Inspect the Saved Demo Without Model Calls

On the development machine, this reads the completed case without starting any
Agent, container or provider request:

```bash
.venv/bin/python -m repoagent issue show issue_c955ff15d3f244d488b1ae7a \
  --store artifacts/issue-demo-live/cases --format text
```

The text summary shows phase states, baseline/candidate exit codes, recorded model
calls, changed filenames and existing report/patch paths, not full source or issue
body. It displays recorded evidence, not a newly rerun verification. Local case
artifacts are not included in Git; a fresh clone must run the example below.

### Execute a New Case

Prerequisites: installed RepoAgent dependencies, host Python 3.12+, Git, working
Linux Docker, and a configured `REPOAGENT_DEEPSEEK_API_KEY` or `DEEPSEEK_API_KEY`.
Run from this RepoAgent checkout, not from the untrusted target repository.
No key is passed into the Docker worker. The demo selects `deepseek-flash`
(V4.1-Flash at acceptance time) through the existing DeepSeek provider profile.
Model aliases can change; the model and pricing snapshot are saved with each run.

Prepare a separate target checkout (skip clone if this directory already exists):

```bash
git clone --branch v0.19.1 --single-branch https://github.com/theskumar/python-dotenv.git artifacts/issue-demo-target
docker pull python@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17
```

Create a case. This fixture is a curated behavior-only snapshot, not raw input
for a blind benchmark. It contains no historical patch or fixed revision.

```bash
.venv/bin/python -m repoagent issue open https://github.com/theskumar/python-dotenv/issues/360 \
  --repo artifacts/issue-demo-target \
  --revision fc138ce8a430b758f4f2c89bc8104f259e2cba38 \
  --snapshot tests/fixtures/issue_demo/issue-360.json \
  --store artifacts/issue-demo/cases
```

Use the returned `case_id` in place of `CASE_ID`. Omitting `--snapshot` performs
a bounded public GitHub API read of the issue title/body, without comments.

```bash
.venv/bin/python -m repoagent issue investigate CASE_ID \
  --config tests/fixtures/issue_demo/config-360.json --store artifacts/issue-demo/cases --format text
.venv/bin/python -m repoagent issue show CASE_ID --store artifacts/issue-demo/cases --format text
```

Review the report first. Then explicitly request a repair:

```bash
.venv/bin/python -m repoagent issue fix CASE_ID --store artifacts/issue-demo/cases --format text
```

The case directory contains `case.json`, `report.md`, `report.json`, model
budgets and journals, isolated Agent sessions, baseline/candidate command
outcomes, and `fix/candidate.patch`. Nothing is commented, pushed, merged or
closed externally. The target checkout remains unchanged.

`--format text` is available on all four issue commands. During execution it sends
phase progress to stderr and the final summary to stdout. It reports actual phase
boundaries, not per-tool streaming or estimated percentages. The default remains
JSON with no progress lines; explicit `--format json` has the same behavior, so
existing scripts can continue parsing stdout unchanged. A stopped Agent remains
visible even if an old case has an incorrectly recorded reproduced status.

Each phase allows at most 24 model calls, 4096 output tokens per call,
128000 conservatively counted input units per call, a $1 estimated budget,
90 seconds per provider request and 600 seconds for the worker. The byte-based
input reservation is a conservative accounting bound, not measured token usage.
Independent verification allows 60 seconds. No tools install dependencies.

## State and Trust Boundaries

- `needs_information`: empty issue body; no model call.
- `not_reproduced`: the supplied baseline acceptance probe already passes.
- `environment_blocked`: baseline did not produce the expected marked assertion.
- `investigation_incomplete`: model stopped without a completed investigation.
- `reproduced`: host reproduction evidence plus completed Agent investigation.
- `verification_failed`: unsuccessful/incomplete/no-change repair.
- `candidate_ready`: completed repair, allowed changed files and passing clean verification.
- `execution_failed`: execution raised an error; evidence is retained.

Completed investigations cannot silently replay. An uncertain interrupted run
blocks automatic replay; create a new case only after inspecting the old run.
Cases and execution config are trusted local state, not an HTTP multi-user API.
The CLI returns a saved domain status even for non-successful investigation;
automation must inspect `status`, not treat process exit 0 as a successful fix.

The acceptance script is maintainer-authored, stored outside the Agent mount,
and run from a read-only verification mount. It may use `/tmp` for scratch data.
The Agent can modify its disposable worker mount; its narration and session logs
are not trusted proof. The host owns budget and final verification outcomes.
This is process isolation and independent checking, not a proof against actively
malicious Python code spoofing an in-process oracle. Human review is still required.

## Measured Result (2026-09-14)

Case `issue_c955ff15d3f244d488b1ae7a` under local
`artifacts/issue-demo-live/cases` reached `candidate_ready`:

- Investigation: 11 real model calls; repair: 11 calls.
- Original revision: expected failing assertion, exit 1.
- One changed file: `src/dotenv/main.py`.
- Patch applied to a fresh source export; all five maintainer checks passed, exit 0.
- Checks cover missing newline, existing newline, trailing comment, empty file,
  and updating an existing binding. These are five checks on one issue, not five issues.
- Peak-rate estimate: investigation $0.02509848, repair $0.03712698,
  combined $0.06222546. This is not an invoice or a cost-reduction experiment.

Four earlier development attempts did not complete the workflow: one failed
before a provider call, two lost structured history and stopped, and one completed
reproduction work but exhausted its call limit. Their records remain local.
One early case (`issue_f10b54b71ae34fa6b13dddac`) incorrectly says `reproduced`
despite a stopped Agent; exclude it from success counts. A regression test now
prevents that status error. Do not report the final run as first-attempt success.
Across development attempts, 53 calls have recorded usage, for a peak-rate
estimate of $0.098668788. The failed pre-call attempt has zero provider calls.

Only one historical issue was exercised end to end. Full upstream pytest,
repository-general performance, external deployment and a blind success rate
were not established. No claim of exceeding commercial coding agents is made.

Model/pricing reference: [DeepSeek official pricing](https://api-docs.deepseek.com/quick_start/pricing/).
Case reference: [python-dotenv issue 360](https://github.com/theskumar/python-dotenv/issues/360).

## Resume Scope

Built a maintainer-oriented Issue investigation and candidate-repair application
on an existing Agent Harness, combining immutable repository snapshots, isolated
native tool execution, host-side model budgets, explicit repair approval and
independent patch verification. Demonstrated one historical Python issue from
reproduction to a single-file patch passing five frozen acceptance checks,
with saved execution evidence and no automatic external publication.

## Tests and Engineering Records

Acceptance: full suite 1550 passed / 54 skipped; final affected suite (including
two later digest regressions and Docker) 52 passed. Full-suite skips include
opt-in integration tests, not successful executions. JUnit records are retained
in `artifacts/issue-demo-live/`. There are six existing datetime deprecation
warnings. Ruff checks pass.

```bash
.venv/bin/python -m pytest tests/test_issue_cases.py tests/test_issue_workflow.py tests/test_issue_execution.py -q
REPOAGENT_TEST_DOCKER=docker .venv/bin/python -m pytest tests/test_issue_execution.py -q
```

[Implementation checklist](roadmaps/issue-maintainer-demo-plan.md) and
[technical record](architecture/issue-maintainer-demo.md) distinguish delivered
behavior from future ideas. Skills, memory, new self-evolution and multi-platform
integration are deliberately not added to this minimal workflow.

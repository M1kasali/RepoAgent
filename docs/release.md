# Release and Evidence

## Implementation Closeout - 2026-09-13

The current agreed implementation scope is closed for this iteration (TECH-165).
This does not create a release, bump version 0.1.1, move the existing v0.1.1 tag
or establish full upstream parity. Changes after the clean bc18a86 candidate
include host behavioral probes, sealed baseline pairing, offline pilot preflight,
snapshot unittest execution and the one-shot hosted pilot runner.

Latest source regression: 1,423 passed, 52 conditional skips and six existing
warnings; separate Docker integration and retained evidence are recorded in
TECH-163/164. These are local functional results, not a claim of live model
improvement or acceptance of a newly built distribution. Reuse these results
for this documentation-only closeout; no new paid or full benchmark campaign.

Installation/startup is documented in [README](../README.md), feature boundaries
in [Mainline Status](roadmaps/mainline-status.md), and experiment mechanics in
[Evolver Workflow](architecture/evolver-workflow.md). Future real-effect campaigns
are paused, not delivery blockers. Private corpus/oracle files and local logs
remain outside product Git history and require separate retention.

## Supported Matrix

- Python: 3.10, 3.11, 3.12
- OS: Ubuntu and Windows
- Packaging: source distribution and pure-Python wheel
- Dependency source: tracked `uv.lock`, installed with `uv sync --frozen --dev`

Every pull request runs Ruff and the full test suite across the supported matrix. A separate job builds the package, installs the wheel into a fresh virtual environment, and smokes all public command entry points.

## Local Verification Evidence

Development verification can be retained without turning a dirty checkout into
a public claim:

```bash
uv run python scripts/record_verification.py \
  --polyglot-dataset /path/to/polyglot-benchmark
```

The command writes a timestamped directory under `artifacts/verifications/`
containing pytest JUnit output, command stdout/stderr, a Polyglot canary plan when
requested, source/environment provenance, exit codes, durations, and SHA-256
records. `artifacts/` is intentionally ignored because local logs can contain
machine paths or sensitive command output. These bundles support debugging and
later evidence selection; they are not resume evidence. Resume claims continue
to require the clean-tag release flow below.

## Tag Contract

The package version `X.Y.Z` must be released from tag `vX.Y.Z`. The tag must point to clean `HEAD`, and `uv.lock` must be tracked. Verify locally before tagging:

```bash
uv run repoagent-release-check --tag vX.Y.Z
uv run ruff check .
uv run pytest -q
uv build
```

## Evaluation Evidence

The tag workflow reruns the deterministic runtime-contract suite and creates a self-contained release directory containing:

- unified `results.json` with commit, environment, model, benchmark, raw rows, denominators, gates, and limitations;
- the exact benchmark definition;
- per-task Turn state, trace, report, call ledger, and evidence manifests;
- a release manifest binding the tag, commit, benchmark digest, and SHA-256/size of every file.

Verify a downloaded bundle with `verify_release_bundle()` before using any metric. Resume-claim generation accepts only a verified tagged bundle:

```bash
python scripts/collect_resume_metrics.py \
  --release-bundle release/repoagent-vX.Y.Z \
  --output-json release/resume-claims.json \
  --output-markdown release/resume-claims.md
```

Scripted contract results must remain labeled as runtime-contract evidence. They are not a claim that RepoAgent outperforms production coding agents.

## Current Local Candidate

Candidate `bc18a863b2f4ee8bad001113b1061b9fe64e17a2` passed clean-source
verification after the budget-floor and stream-finalization fixes. Version is
still `0.1.1`; no new tag, publication or push was performed during acceptance.

- Full regression: 1,352 passed, 43 skipped, six existing warnings in 178.70
  seconds. Repository-wide Ruff and diff check passed.
- Offline wheel and source distribution built; the wheel installed in a fresh
  environment outside the checkout, with locked json-repair 0.63.4.
- Dependency check and all five CLI help checks passed. All 156 installed
  Python module hashes matched source; isolated import location was verified.
- Installed CLI passed 12/12 scripted runtime contracts using checkout assets.
  The relocated contract bundle verified; the default tagged-release verifier
  rejected this untagged candidate as required.
- All 367 verification payload hashes passed. Archive filenames were checked
  for ignored runtime/config paths, not exhaustively scanned for secret content.

Evidence is local under `artifacts/acceptance/candidate-bc18a86/`: verification
manifest, distribution files, installed-package report, summary and outer hash
index. The initial postprocessing script mistakenly treated uv's `.gitignore`
as an archive; all verification commands had passed. A separate retained review
script filtered the two actual distributions and completed archive verification.

This exercised Linux/WSL Python 3.12 only, not the full supported matrix,
optional integrations or real-model effectiveness. Documentation was updated
after the frozen clean-commit acceptance. The old `v0.1.1` tag remains unchanged
and does not identify this candidate despite matching package filenames.

## Previous Local Candidate

Candidate `6cd7ade05e19a8c760871a5205ac0e80149fa1ac` was verified on clean
source. This is not a new release: version remains `0.1.1`, no tag was created
and nothing was published. The existing `v0.1.1` release does not cover these
later changes.

| Check | Observed result |
| --- | --- |
| Source regression | 1,299 passed, 43 skipped; Ruff passed |
| Package build | Offline wheel and source distribution built successfully |
| Isolated wheel install | Installed outside the checkout with locked json-repair 0.63.4; dependency check passed |
| Installed code identity | All 153 Python module hashes matched source; import location verified inside the new virtual environment |
| CLI smoke | All five public command entry points accepted --help |
| Installed runtime contracts | 12/12 scripted cases passed, using the checkout's benchmark assets |
| Evidence integrity | 367 candidate payload hashes checked; self-contained contract bundle verified after relocation |
| Release boundary | Default tagged-release verifier rejected the untagged candidate, as required |

Local artifacts, intentionally untracked:

- `artifacts/acceptance/candidate-6cd7ade/verification/manifest.json`: commands,
  source/environment identity, outputs and hashes.
- `artifacts/acceptance/candidate-6cd7ade/verification/dist/`: candidate wheel
  and source distribution; do not confuse their 0.1.1 filenames with the old tag.
- `artifacts/acceptance/candidate-6cd7ade/evidence-index.json`: candidate checks
  and seven independently rechecked historical experiment receipts.
- `artifacts/acceptance/candidate-6cd7ade/manifest.json`: outer receipt binding
  the local scripts, summary, index and verification manifest.

Only local Linux/WSL Python 3.12 was exercised. This does not certify the full
CI matrix, optional integrations, external platforms or live-model quality.
The recovery success on `dc432b3` remains a single debugged case; SQLite's live
recall result remains historical dirty-tree development evidence, not current
release effectiveness. Prior failures remain indexed. No resume-claim file was
generated from this candidate.

The next formal release requires a deliberate version/tag decision and the
existing tagged workflow. Own module-level paired measurements remain separate
work, not a prerequisite for claiming that this local installation smoke passed.

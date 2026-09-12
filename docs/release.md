# Release and Evidence

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

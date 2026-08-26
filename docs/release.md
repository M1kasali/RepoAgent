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

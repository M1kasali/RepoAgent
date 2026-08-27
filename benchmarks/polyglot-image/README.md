# Polyglot Benchmark Image

This image is the offline grader environment for the frozen six-language Aider
Polyglot workload. It contains C++/CMake, Go, Java/Gradle, JavaScript/Jest,
Python/pytest, and Rust/Cargo. Runtime containers use no network, a read-only root
filesystem, dropped capabilities, bounded CPU/memory/PIDs, and a disposable
workspace.

Build the local image:

```bash
docker build \
  --provenance=false \
  --file benchmarks/polyglot-image/Dockerfile \
  --tag repoagent-polyglot:local \
  .
```

Before a paid or publishable campaign, resolve the immutable identity:

```bash
docker image inspect repoagent-polyglot:local \
  --format '{{index .RepoDigests 0}}'
```

Formal campaigns reject mutable tags. Pass the returned
`repoagent-polyglot@sha256:...` reference to `--image`.

`--provenance=false` keeps the runnable image manifest stable across identical
local builds. Generate and retain SBOM/provenance as a separate release artifact;
do not let an automatically timestamped attestation change the execution identity.

Verify one known-good exercise per language before any model calls:

```bash
python scripts/run_polyglot_image_smoke.py \
  --dataset /path/to/polyglot-benchmark \
  --output /tmp/repoagent-polyglot-image-smoke \
  --image repoagent-polyglot:local
```

When Docker Desktop cannot mount the active WSL distribution, place staging on a
Windows-backed path and use the Windows Docker CLI:

```bash
python scripts/run_polyglot_image_smoke.py \
  --dataset /path/to/polyglot-benchmark \
  --output /tmp/repoagent-polyglot-image-smoke \
  --image repoagent-polyglot:local \
  --docker /mnt/c/path/to/docker.exe \
  --staging-root /mnt/c/path/to/repoagent-staging \
  --wsl-windows-path
```

The smoke copies benchmark reference implementations only into isolated grader
workspaces. It does not expose them through `PolyglotRunnerInput`, Agent prompts,
or campaign workspaces, and its pass rate is not a model-quality measurement.

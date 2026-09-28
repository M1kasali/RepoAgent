# Third-Party Source Notices

The context assembly implementation in `repoagent/context_engine/` includes
code adapted from Pico (https://gitee.com/htxoffical/pico-harness), licensed
under Apache-2.0. The applicable license is retained in
`Apache-2.0-pico-harness.txt`. RepoAgent adaptations include package imports,
product naming, typed runtime integration, persistence and cancellation
adapters, and archive containment checks.

Pico's runtime also incorporates nanobot code:

- Source: https://github.com/HKUDS/nanobot
- Copyright (c) 2025 nanobot contributors
- License: MIT, retained in `MIT-nanobot.txt`.

These notices apply to the incorporated source; they do not relicense unrelated
RepoAgent code or imply endorsement by the original authors.

## BoxLite and Session migration

`repoagent/boxlite_sandbox/` and `repoagent/session/` also incorporate Pico
Apache-2.0 source from commit `c3a7a1d9032b539ca7a7cc52e46c9c0e29d5cdc3`.
The executor, configuration, runtime cache, debug server, session manager,
epoch I/O and portable locks retain the reference algorithms. Import namespaces
and installation instructions are renamed. The reference's obsolete network
keywords are mapped to BoxLite 0.9.5 NetworkSpec (same policy); this patch is
recorded explicitly in the source manifest. `paths.py` maps the product data
location and `helpers.py` contains the two original session path helpers.
`pico-boxlite-session-source.json` records source hashes and exact replacements.
The corresponding `test_pico_session_*` and `test_pico_boxlite_*` tests are adapted
from the same reference (namespace changes and explicit pytest asyncio markers).
RepoAgent's dictionary and synchronous tool adapters are separate integration code.

## Main runtime and terminal UI

`repoagent/harness/`, `ui-tui/`, `tests/harness_reference/` and retained benchmark
fixtures incorporate source from the same pinned commit above. See
`pico-runtime-NOTICES.md`, `MIT-hermes-agent.txt` and `MIT-ink.txt` for the terminal
UI attribution chain. `pico-runtime-source.json` records original and adapted
SHA-256 hashes. `scripts/import_reference_runtime.py` reproduces the namespace,
product-state, protected-source-path and SDK adaptations; behavioral algorithms
are retained. Entrypoint routing, wheel packaging and added integration tests
are RepoAgent integration code.

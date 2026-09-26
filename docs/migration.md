# Migration Guide

This guide covers migration from the earlier Pico/RepoAgent state and configuration names to the current RepoAgent runtime.

## State Directory

New workspaces use `.repoagent/`. If a workspace already contains `.pico/` and does not contain `.repoagent/`, RepoAgent continues using `.pico/` to avoid silently splitting sessions and runs.

To migrate explicitly:

1. Stop all RepoAgent gateway, cron, TUI, and channel processes for the workspace.
2. Back up `.pico/` without changing its contents.
3. Rename `.pico/` to `.repoagent/` on the same filesystem.
4. Run `repoagent doctor --cwd <workspace>` and inspect sessions with `repoagent session list`.
5. Keep the backup until at least one session, trace, and scheduled-job read succeeds.

Do not merge two live state directories. Session revisions, Turn event sequences, cron leases, and Evolver hash chains are concurrency-sensitive.

## Configuration

Current configuration uses `REPOAGENT_*`. Legacy `PICO_*` variables remain fallback aliases, while an explicitly set `REPOAGENT_*` value wins. User configuration lives at `~/.config/repoagent/.env`; shell variables take precedence and a target repository `.env` may override user configuration.

Move credentials without committing them:

```dotenv
REPOAGENT_PROVIDER=deepseek
REPOAGENT_DEEPSEEK_API_KEY=replace-me
REPOAGENT_DEEPSEEK_MODEL=replace-me
```

## Schema Compatibility

| Artifact | Current version | Compatibility behavior |
| --- | --- | --- |
| Session | `_schema_version: 1` | unversioned legacy sessions are read; unknown future versions fail |
| Turn snapshot/events | `format_version: 1` | legacy snapshot version 0 is read; unsupported versions fail |
| Checkpoint | `phase1-v1` | mismatch is recorded and resume fails closed |
| Tool contract/capability | `format_version: 1` | unsupported versions are rejected |
| Cron store/plugin manifest | `schema_version: 1` | unsupported versions are rejected |
| Evaluation result | `repoagent.evaluation-result/v1` | validator recomputes row identities and denominators |
| Evolver ledger | `repoagent.evolver-ledger-event/v1` | every historical digest and sequence is verified |

There is no automatic downgrade. Preserve the original state before opening it with a newer release and use tagged release notes for any future schema migration command.


## Pico BoxLite / JSONL alignment (2026-09-26)

Reference: `c3a7a1d9032b539ca7a7cc52e46c9c0e29d5cdc3`.
Source-level mapping and hashes: `LICENSES/pico-boxlite-session-source.json`.

- BoxLite core is imported with namespace/install-text replacements, including
  SandboxConfig, exec/start_process, shared VM, runtime cache, debug socket and cleanup.
- `BoxliteSandboxAdapter` owns an asyncio thread because RepoAgent tools are synchronous.
  MCP streams are marshalled to that loop. Normal command timeout stays on the original
  executor's kill path; host cancellation closes the owned executor. SDK discovery
  sessions release their process bridges; the VM survives until runtime shutdown.
- BoxLite subagents get a separate executor bound to the isolated child workspace.
- `auto` and `boxlite` fail closed. Host `direct`/`none` and existing Docker choices remain.
- SessionManager, epoch I/O and portable locking retain the reference implementation.
  The RepoAgent facade maps opaque IDs to `cli:<id>`, history to message rows, and remaining
  runtime state into metadata.repoagent. Existing revision-based RPC confirmation remains;
  the manager still supplies its epoch and content fences.
- Old JSON is migrated on resume/save under its original writer lock; legacy
  listing/export inspection remains read-only. Original bytes remain as a
  backup; a legacy deletion marker prevents old SessionStore writers from resurrecting
  obsolete history. Restart after interrupted migration validates both formats before
  publishing the marker. New session listing, resume, export, clear, undo and branch use
  the facade, so callers do not parse raw JSONL themselves.
- Original upstream session and sandbox unit tests are retained under `test_pico_*`.
  Additional tests exercise actual RepoAgent storage, migration, MCP SDK and runtime wiring.
- Real BoxLite probe on this WSL host failed because `/dev/kvm` exists but is not readable
  and writable by the current account. Startup failed closed with no registered owned VM.
  The opt-in `tests/test_boxlite_live.py` remains the actual hardware acceptance test.

### Pinned SDK compatibility correction

The reference pins `boxlite==0.9.5` but passes the old `network="none"` and
`allow_net=[...]` BoxOptions keywords. Both raise TypeError with that real SDK.
The sole executor algorithm delta maps these to `NetworkSpec(mode="disabled")`
and `NetworkSpec(mode="enabled", allow_net=[...])`, respectively. The policy,
unrestricted pre-pull and lifecycle remain the reference behavior. See the
[0.9.5 SDK contract](https://pypi.org/project/boxlite/0.9.5/) and exact patch in
the source manifest. SDK-backed offline tests construct real BoxOptions for
all three network settings; they do not claim to test actual network enforcement.

Final local verification: full tests 1,952 passed / 58 skipped; final integration
follow-up 78 passed. Ruff and source manifest verification passed. On 2026-09-26,
after activating the user's KVM group, the real VM smoke passed (1 passed in
28.90 s): workspace writes, scratch reuse, timeout recovery and adapter cleanup.
The smoke now uses a short temporary runtime path for Unix sockets and the
product's 300 s creation / 30 s verification timeouts. Extended live testing now
covers MCP sharing/cancellation/crash cleanup, network enforcement, parent/child
isolation and runtime/PID cleanup. Acceptance is still incomplete: timed-out
executions leave descendants running, and intermittent SDK `spawn_failed` errors
were observed. The adapter now confirms process exit after a failed MCP kill,
preserving the shared VM after server crashes; related offline regressions pass
(64 executor/adapter tests plus 16 MCP transport tests). Subsequent same-host
comparison reproduced timeout descendants and sporadic spawn errors in the
unmodified Pico executor; the original ExecTool also leaves timeout descendants.
A migration difference is fixed: a failed command now preserves the started VM,
as Pico does, instead of erasing its state. Startup failures and cancellation
still close it. Updated focused regressions: 82 passed. A controlled host-interface
network test passes without public DNS/site dependence; public domain/SNI checks
are separate. The special host-loopback gateway remained reachable despite an
unrelated allowlist, so host-loopback isolation is not claimed. See the
[hardware acceptance record](boxlite-acceptance.md) for evidence and limits.
Existing Docker acceptance is independent of this BoxLite result.

Subsequent full regression: 1,958 passed / 67 skipped / 13 warnings (317.39 s).
The offline runtime-contract demo passed all 12 scenarios and verified 12 evidence
bundles. A new separately run BoxLite E2E passed in 72.99 s: real AgentLoop repair
and unittest execution, MCP scratch exchange, authorized child read/write tools
with trace assertions, child VM cleanup, and JSONL resume/append across different
OS processes. Model responses are scripted. The full run predates this new
opt-in case; its pass is not included in the 1,958 total. Known SDK limits remain.

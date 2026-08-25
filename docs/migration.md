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

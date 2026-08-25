# Threat Model

## Assets

- repository source and uncommitted work;
- provider credentials and other environment secrets;
- command execution authority;
- session, memory, trace, and evaluation data;
- sealed graders and Evolver activation history.

## Trust Boundaries

Model output is untrusted. Repository files, Skill content, MCP/plugin metadata, channel messages, tool output, and benchmark fixtures may also contain adversarial instructions. Host configuration, explicit human approvals, capability signing keys, and isolated grader backends are trusted administration inputs.

## Controls

| Threat | Control |
| --- | --- |
| Path traversal or symlink escape | workspace path validation, resolved-root checks, candidate symlink rejection |
| Arbitrary tool use | typed schemas, allowlists, effect classification, scoped capability tokens |
| Dangerous command execution | explicit approval policy, deadline/output limits, optional required isolation |
| Secret disclosure | named-secret tracking, output redaction, provider reports expose presence only |
| Duplicate work or side effects | stable request IDs, scheduler/channel/cron deduplication, claim leases |
| Prompt/Skill/plugin escalation | bounded context, host trust store, declarative runner IDs, no manifest self-approval |
| Evaluation leakage | disjoint task splits, sealed storage, isolated backend, blind receipts |
| Self-modification without review | mutation allowlists, detached worktrees, paired gates, one-time human approval |
| Evidence rewriting | atomic artifacts, checksummed bundles, append-only Evolver hash chain |

## Explicit Non-goals

The default direct-host sandbox is not a security boundary. Strong isolation requires configuring an isolated sandbox adapter and denying execution when isolation is unavailable. RepoAgent is not designed for mutually hostile tenants sharing one OS account. Python module privacy and local file permissions do not defend against an attacker who already controls the host account. Provider-side retention and training policies remain properties of the selected provider.

## Residual Risks

- approved commands can still damage accessible host data;
- semantic prompt injection cannot be eliminated by string filtering;
- redaction only covers configured or detected secret material;
- third-party sandbox, provider, MCP, and plugin implementations may violate their declared behavior;
- deterministic contract tests do not measure unknown attacks or general coding quality.

Security-sensitive deployments should use a disposable workspace, least-privilege credentials, network allowlists, required isolation for execute/external tools, and retained evidence review.

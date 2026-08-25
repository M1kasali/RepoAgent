# Security Policy

## Supported Versions

Security fixes are provided for the latest tagged release. Older revisions should be upgraded before reporting behavior that has already changed on `main`.

## Reporting a Vulnerability

Do not open a public issue for an unpatched vulnerability. Use GitHub's private vulnerability reporting for this repository. Include the affected commit/tag, operating system, configuration, minimum reproduction, impact, and whether credentials or repository data were exposed.

Do not include live credentials, private source code, or destructive payloads. Use synthetic fixtures and revoked test tokens. You should receive an acknowledgement within seven days; disclosure timing will be coordinated after impact and remediation are confirmed.

The security boundary and known non-goals are documented in [the threat model](docs/security/threat-model.md).

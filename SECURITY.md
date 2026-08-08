# Security Policy

## Reporting Security Issues

Please do not open public issues for security-sensitive reports.

Use GitHub private vulnerability reporting when it is enabled for the repository.
Otherwise, open a minimal public issue that asks for a private reporting channel without describing the vulnerability.

Include the affected version, operating system, Python version, a minimal reproduction, impact, and any safe mitigation you found.
Redact secrets and private learning content before sending anything.

## Sensitive Data

openLearn is designed to keep learning data local by default. Reports and examples should not include:

- API keys or tokens.
- `config.json` contents.
- Private topic notes from `learning-topics/`.
- Class materials, private documents, or proprietary source material.
- Backup archives, event logs, screenshots, or terminal recordings that reveal learner activity.

## Release and dependency reports

For a compromised package, exposed artifact, or suspect dependency, report the package version, artifact filename, hash, install command, and redacted error output.
Do not uninstall or reset learner data while investigating.
The recovery procedure in [the release runbook](docs/RELEASING.md) explicitly preserves user homes.

## Supported Versions

The project is pre-1.0.
Security fixes are applied to the latest code on `main` and shipped in the next tagged release.

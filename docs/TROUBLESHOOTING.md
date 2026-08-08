# Troubleshooting and support

Start with the installed command and a temporary home when you are isolating a problem.

```bash
openlearn --version
openlearn data inventory
openlearn doctor
```

`openlearn data inventory` identifies the home currently in use without needing to inspect its files manually.
Use `OPENLEARN_HOME` to test with an empty directory rather than changing your real learner home.

## Installation or command not found

Confirm that the Python environment where you installed openLearn is active.

```bash
python -m pip show openlearn
python -m pip install --upgrade openlearn
openlearn --version
```

On Windows, activate the virtual environment or invoke its `python.exe` directly.
See [installation instructions](INSTALL.md) for platform-specific setup.

## Provider setup or model failures

Run `openlearn init` to select or correct a provider.
Run `openlearn config show` to inspect the selected model, base URL, and whether a key is configured.
The command intentionally does not print API-key values.
Hosted providers require credentials you control, while a correctly configured localhost OpenAI-compatible provider can be keyless.

## Maker Bench does not open

Run `openlearn web --no-browser` and open the loopback URL it prints.
Use `openlearn web --port 9000 --no-browser` if the default port is unavailable.
Maker Bench runs on the local machine and shares the same home as the CLI.

## Secure code execution is unavailable

The workbench may still open, but secure execution needs Docker or Podman.
Install or start one, then run `openlearn doctor` for the precise runtime and image status.
Do not use reduced isolation for untrusted code.

## Safe support report

For a normal product issue, include the openLearn version, OS, Python version, a minimal command sequence, redacted error output, and whether the issue happens with a new temporary `OPENLEARN_HOME`.
Do not include API keys, `config.json`, backup archives, event logs, private topic files, imported content, screenshots containing learner data, or a full home directory.
Open a GitHub issue for non-sensitive defects.
Follow [the security policy](../SECURITY.md) instead for a vulnerability, secret exposure, or suspect release artifact.

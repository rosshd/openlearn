# Install openLearn

openLearn supports Python 3.11 through 3.13 on Linux, macOS, and Windows.
The command line and Maker Bench use the same local home and files.
Docker or Podman is optional unless you run secure Python code checks.

## Install

Create a virtual environment before installing when practical.

### macOS and Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip openlearn
openlearn --version
```

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip openlearn
openlearn --version
```

If PowerShell blocks activation, follow your organization's execution-policy guidance or invoke `.venv\Scripts\python.exe -m pip` directly.
`pipx install openlearn` is also suitable when you want an isolated command-line installation managed by pipx.

## First launch

```bash
openlearn init
openlearn templates
openlearn web
```

`openlearn init` configures a provider only when you choose model-backed learning.
You can use a hosted OpenAI-compatible provider with your own account and key, or a local keyless endpoint such as Ollama.
openLearn does not supply, bill for, or share a provider account for Community users.
Maker Bench is loopback-only and opens in the default browser.
Use `openlearn web --no-browser` on a headless machine, or `openlearn web --port 9000` to select a loopback port.

## Upgrade and uninstall

Upgrade the package in the same environment where you installed it.

```bash
python -m pip install --upgrade openlearn
openlearn --version
```

To remove only the installed package:

```bash
python -m pip uninstall openlearn
```

Package upgrades and uninstalls do not remove learner data.
Before changing machines or deleting data, create a verified backup as described in [Data and privacy](DATA_AND_PRIVACY.md).

## Optional code runner

The code workbench remains available without a container runtime.
Secure execution needs Docker or Podman.
If execution is unavailable, install or start Docker or Podman and run:

```bash
openlearn doctor
```

Do not treat `--reduced-isolation` as a secure sandbox.
It runs learner code locally with warnings and can access account files and the network.

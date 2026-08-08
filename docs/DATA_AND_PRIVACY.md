# Data and privacy

openLearn is local-first.
It stores learner-owned course files, state, imports, and optional saved provider settings in one local home.
The application does not send product telemetry or usage analytics by default.
Model-backed actions send the selected prompt and bounded course context to the provider you configure.
Local tools such as source import stay local until you ask a model-backed action to use their selected content.

## Find the active home

Run the following command to see the resolved home and durable inventory.

```bash
openlearn data inventory
```

`OPENLEARN_HOME` has highest priority.
When it is unset, openLearn uses the current directory if it contains `learning-topics/`.
Otherwise it uses the platform data directory:

- Linux: `$XDG_DATA_HOME/openlearn`, or `~/.local/share/openlearn` when `XDG_DATA_HOME` is unset.
- macOS: `~/Library/Application Support/openlearn`.
- Windows: `%LOCALAPPDATA%\openlearn`.

Set a deliberate home before starting openLearn if you want the data elsewhere.

```bash
export OPENLEARN_HOME="$HOME/openlearn-data"
```

In Windows PowerShell:

```powershell
$env:OPENLEARN_HOME = "$HOME\openlearn-data"
```

The durable inventory includes `learning-topics/`, `state.json`, `config.json`, and `tui_history.txt` when present.
Topic files can include Markdown course notes, learner state, event logs, interview profiles, imported context, and drill files.
Temporary server leases, locks, staging files, and unrelated files in a shared home are not treated as learner data.

## Backup and restore

Create a verified archive before moving, resetting, or deleting a home.

```bash
openlearn data backup /safe/location/openlearn.olbackup
```

Backups exclude saved provider credentials by default while retaining other settings.
Include saved credentials only when you understand that the archive needs credential-level protection.

```bash
openlearn data backup /safe/location/openlearn-with-credentials.olbackup \
  --include-credentials \
  --credential-confirmation "INCLUDE SAVED CREDENTIALS"
```

Restore only into a new or empty destination.

```bash
openlearn data restore /safe/location/openlearn.olbackup /new/location/openlearn-data
```

The restore command verifies the archive before writing it.
Keep backup archives outside the home they protect and do not attach them to public bug reports.

## Move, reset, and delete

These operations require a backup whose verified inventory still matches the current home.
They refuse broad or unsafe targets and preserve unrelated files in a shared directory.

Move to a new home, then set `OPENLEARN_HOME` to the printed destination before the next launch.

```bash
openlearn data move /safe/location/openlearn.olbackup /new/location/openlearn-data \
  --confirmation "MOVE OPENLEARN HOME"
```

Reset removes learner data while preserving settings by default.

```bash
openlearn data reset /safe/location/openlearn.olbackup \
  --confirmation "RESET OPENLEARN DATA"
```

Delete removes only verified openLearn data after the exact confirmation.

```bash
openlearn data delete /safe/location/openlearn.olbackup \
  --confirmation "DELETE OPENLEARN HOME"
```

For move, reset, or delete operations that include saved credentials, add both credential options from the backup example.
Use a credential-containing archive only on storage you control.

Package uninstall and upgrade leave this home untouched.
Data is removed only through an explicit reset, delete, or manual filesystem action.

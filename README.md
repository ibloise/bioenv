# bioenv

A small Python CLI for managing shared Micromamba bioinformatics tools on a multiuser
Linux workstation.

**Status: 0.2.0.** The CLI validates and inspects the catalog, creates tool environments,
publishes selected launchers, and checks installations. `update` and `remove` are not
implemented.

## Developer quick start

```bash
cd ~/Dev/bioenv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
bioenv --version
bioenv --config config/tools.example.yaml list
bioenv --config config/tools.example.yaml info bakta
bioenv --config config/tools.example.yaml validate
bioenv --config config/tools.example.yaml install bakta --dry-run
```

For the corporate proxy, use the already-configured system trust bundle and proxy
environment before running `pip`, if needed.

## Available operations

- `bioenv list`, `bioenv info TOOL`, and `bioenv validate` inspect the catalog.
- `bioenv inspect TOOL` lists executable files in the tool environment's `bin` directory.
- `bioenv check TOOL` verifies the environment, current managed launchers, and configured
  databases. Missing optional databases pass; missing required databases fail.
- `bioenv install TOOL` creates the environment and publishes only declared commands.
- `bioenv publish TOOL` publishes commands from an existing environment after checking
  that each configured executable is present.

`install --dry-run` reports the Micromamba command and planned launchers without changing
the filesystem. `publish --dry-run` validates the existing environment and reports the
launchers without writing them. `install` refuses an existing prefix; inspect it with
`check` and use `publish` only if it is the intended environment.

The live catalog is separate from the installed package, at `/etc/bioenv/tools.yaml`.
The example catalog is at `config/tools.example.yaml`. The catalog selects executable
paths and environment variables, so privileged operations must use a catalog and
Micromamba installation controlled by an administrator. Read-only commands do not need
root; `install` and `publish` need write access to their configured paths. A system-wide
pipx installation is the intended deployment route; do not use `sudo pip` into Ubuntu's
system Python or install a privileged release from a writable development directory.
For example, after that deployment, an administrator can run
`sudo bioenv --config /etc/bioenv/tools.yaml install bakta`. The catalog, its parent
directory, configured Micromamba executable, and destination directories must be
controlled by administrators before using `sudo`.

## Runtime dependencies and directories

The Python package requires Python 3.10 or newer and installs PyYAML. `install` also
requires an executable Micromamba at `settings.micromamba`; published launchers invoke
that same executable every time they run, through `/bin/sh`. Micromamba is a separate
system dependency, not installed by `pip` or `pipx`. It needs access to its configured
package channels, either through the network or an existing package cache. Its root
prefix stores shared environments and cache under `settings.mamba_root`
([Micromamba documentation](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html)).

Before first use, an administrator must install Micromamba, create the catalog directory
and file, and prepare the shared storage paths with appropriate ownership and access:

- `/etc/bioenv/` and `/etc/bioenv/tools.yaml` (or the path passed with `--config`) must
  exist and be readable by the operator.
- `settings.mamba_root` and `settings.envs_root` must be writable by the account running
  `install`. With the example settings these are `/opt/bioinformatics` and
  `/opt/bioinformatics/envs`. Create these shared parent directories before installation
  and grant write access to the administrator running it. Micromamba creates tool
  prefixes and its package cache under these locations.
- `settings.bin_dir` must be writable by the account running `publish`. `bioenv` tries
  to create this directory if it is missing; for the shared default `/usr/local/bin`,
  have an administrator create and protect it.
- Any required database directory must already be mounted or populated and readable by
  the users running the tool. `bioenv` does not download or create databases.

## Security notes

- Treat the YAML catalog as trusted administrative input. It chooses absolute paths for
  environments, launchers, and Micromamba, and it supplies variables exported by the
  generated shell scripts. Shell values are quoted, but their effect on the launched
  program still depends on their contents.
- When using `sudo`, use a root-controlled catalog, its parent directory, Micromamba
  executable, and destination directory tree. Do not pass a catalog writable by an
  untrusted user: privileged commands act on the paths it specifies.
- `bioenv` refuses symlinked environment prefixes and launcher directories, and refuses
  to replace existing commands that do not carry its launcher marker. The marker is an
  ownership convention, not a cryptographic proof; protect the launcher directory from
  untrusted writes.
- If Micromamba fails, it may leave an incomplete environment. Inspect the target before
  retrying. If environment creation succeeds but publishing fails, fix the reported
  issue and rerun `publish`.

## Design constraints

- One stable shared environment per tool: `/opt/bioinformatics/envs/<tool>`.
- Only explicitly listed commands are exported; never publish everything in `bin/`.
- Runtime variables such as `BAKTA_DB` belong to the catalog.
- Database downloads are explicit and databases are not automatically deleted.
- Existing unrelated files and symlinks in the launcher directory are not replaced.

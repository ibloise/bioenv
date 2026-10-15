# bioenv

A small Python CLI for managing shared Micromamba bioinformatics tools on a multiuser
Linux workstation.

**Status: 0.3.0.** The CLI validates and inspects the catalog, creates, updates and removes
tool environments, publishes selected launchers, and checks installations.

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
- `bioenv update TOOL` updates the installed package to the catalog version, or asks
  Micromamba to resolve it without an explicit package version when none is declared.
- `bioenv update TOOL --latest` ignores the catalog package version pin for this operation
  and asks Micromamba to resolve an update under configured channels, platform and
  dependency constraints. It does not check the highest version published or query an
  external version service. If Micromamba
  succeeds without changing the main package version, bioenv reports success and the
  unchanged version; dependency updates may still have been applied.
- `bioenv publish TOOL` publishes commands from an existing environment after checking
  that each configured executable is present.
- `bioenv remove TOOL` removes the declared environment and its current bioenv launchers.
  It requires interactive confirmation unless `--yes` is supplied.
- `bioenv remove TOOL --launchers-only` removes only the declared launchers and keeps the
  environment. Use this mode to recover launchers left behind after an environment removal
  succeeded but launcher cleanup failed. `--dry-run` displays the planned Micromamba operation when applicable,
  launchers, missing resources and any conflicts without changing files or prompting.

```bash
bioenv --config /etc/bioenv/tools.yaml update fastqc --dry-run
bioenv --config /etc/bioenv/tools.yaml update fastqc --latest --dry-run
sudo bioenv --config /etc/bioenv/tools.yaml update fastqc
bioenv --config /etc/bioenv/tools.yaml remove fastqc --dry-run
sudo bioenv --config /etc/bioenv/tools.yaml remove fastqc
sudo bioenv --config /etc/bioenv/tools.yaml remove fastqc --launchers-only --yes
```

Declaring a tool in the administrator-controlled catalog grants bioenv authority to
administer the matching `<envs_root>/<tool>` prefix, including environments originally
created outside bioenv. Removal uses `micromamba env remove`; it never recursively deletes
an environment directory itself. Launcher cleanup happens only after Micromamba succeeds,
and only for unchanged launchers carrying the matching bioenv marker. Foreign, modified,
symlinked, or other-tool launchers block removal. If Micromamba fails, launchers are kept;
if launcher cleanup fails after the environment is gone, the error lists the launchers still
pending cleanup. Recover them with `remove TOOL --launchers-only`; bioenv will still reject
foreign, modified or symlinked launchers. The operation is not atomic and bioenv does not
roll back or rebuild a partially removed environment. Use `--dry-run` to review the plan, and `--yes` only when
the displayed resources are the intended target.

`install --dry-run` reports the Micromamba command and planned launchers without changing
the filesystem. `publish --dry-run` validates the existing environment and reports the
launchers without writing them. `update --dry-run` runs Micromamba's solver simulation and
shows its proposed transaction; Micromamba may refresh package metadata in its cache.
`install` refuses an existing prefix; inspect it with
`check` and use `publish` only if it is the intended environment.

Updates are in place and have no automatic rollback. A changed main package version is
reported as `old -> new`; an unchanged version is reported as successful completion with no
main-package version change. If Micromamba or post-update checks
fail, inspect the environment before taking further action. A requested catalog version and
the version ultimately installed can differ when no version is pinned or when `--latest` is
used. `--latest` leaves package selection to Micromamba and does not establish the highest
version published. The solver can reject unavailable packages or
incompatible dependency constraints; bioenv does not rebuild environments to work around
those constraints. `update` requires an existing Conda environment containing the declared
package and never creates one.

The live catalog is separate from the installed package, at `/etc/bioenv/tools.yaml`.
Set `BIOENV_CONFIG` to choose a default catalog path; an explicit `--config` takes
precedence. The example catalog is at `config/tools.example.yaml`. The catalog selects
executable paths and environment variables, so privileged operations must use a catalog and
Micromamba installation controlled by an administrator. Read-only commands do not need
root; `install`, `update`, `publish` and `remove` need write access to their configured
paths. A system-wide pipx installation is the intended deployment route; do not use
`sudo pip` into Ubuntu's system Python or install a privileged release from a writable
development directory.
For example, after that deployment, an administrator can run
`sudo bioenv --config /etc/bioenv/tools.yaml install bakta`. The catalog, its parent
directory, configured Micromamba executable, and destination directories must be
controlled by administrators before using `sudo`.

## Runtime dependencies and directories

The Python package requires Python 3.10 or newer and installs PyYAML. `install` and
`update` require an executable Micromamba at `settings.micromamba`; published launchers invoke
that same executable every time they run, through `/bin/sh`. For SPAdes, the example catalog
maps the public `spades` launcher to the Conda executable `spades.py`; list-based catalogs
remain supported. Micromamba is a separate
system dependency, not installed by `pip` or `pipx`. It needs access to its configured
package channels, either through the network or an existing package cache. Its root
prefix stores shared environments and cache under `settings.mamba_root`
([Micromamba documentation](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html)).

Before first use, an administrator must install Micromamba, create the catalog directory
and file, and prepare the shared storage paths with appropriate ownership and access:

- `/etc/bioenv/` and `/etc/bioenv/tools.yaml` (or the path passed with `--config`) must
  exist and be readable by the operator.
- `settings.mamba_root` and `settings.envs_root` must be writable by the account running
  `install`, `update` or `remove`. With the example settings these are `/opt/bioinformatics`
  and `/opt/bioinformatics/envs`. Create these shared parent directories before installation
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
- `install`, `update`, `publish` and `remove` use a per-tool Linux file lock to coordinate
  bioenv mutations and launcher changes. Micromamba commands run outside bioenv do not honor
  this lock and can still modify the same prefix concurrently. Operations are not transactional
  and bioenv does not roll back partial environment changes.

## Design constraints

- One stable shared environment per tool: `/opt/bioinformatics/envs/<tool>`.
- Only explicitly listed commands are exported; never publish everything in `bin/`.
- Runtime variables such as `BAKTA_DB` belong to the catalog.
- Database downloads are explicit and databases are not automatically deleted.
- Existing unrelated files and symlinks in the launcher directory are not replaced.

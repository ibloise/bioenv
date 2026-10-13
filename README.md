# bioenv

A small Python CLI for managing shared Micromamba tools on a multiuser Linux workstation.

**Status: initial project scaffold.** Only the `list`, `info`, and `validate` commands are
implemented. The project does not yet create/remove environments or launchers.

## Developer quick start

```bash
cd ~/Dev/bioenv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
bioenv --version
bioenv --config config/tools.example.yaml list
bioenv --config config/tools.example.yaml info bakta
bioenv --config config/tools.example.yaml validate
```

For the corporate proxy, use the already-configured system trust bundle and proxy
environment before running `pip`, if needed.

## Planned operations

- `bioenv install TOOL`: create Micromamba environment and expose selected commands.
- `bioenv update TOOL`: update environment without changing its path.
- `bioenv inspect TOOL`: inspect executables present in the environment.
- `bioenv check TOOL`: verify environment, launchers, and configured data paths.
- `bioenv remove TOOL`: remove only manager-owned launchers and the target environment.

The catalog remains separate from the installed package, at `/etc/bioenv/tools.yaml`.
A working example is at `config/tools.example.yaml`.

## Future deployment with pipx

After implementing and testing the management commands, deploy a release using a
system-wide pipx installation rather than `sudo pip` into Ubuntu's system Python.
Exact deployment commands depend on the installed pipx version and local proxy
configuration. **Do not use pipx installation from the writable development directory
as a privileged production deployment.** Package a release or install from a trusted tag.

## Design constraints

- One stable shared environment per tool: `/opt/bioinformatics/envs/<tool>`.
- Only explicitly listed commands are exported; never publish everything in `bin/`.
- Runtime variables such as `BAKTA_DB` belong to the catalog.
- Database downloads are explicit and databases are not automatically deleted.
- Do not overwrite unrelated files in `/usr/local/bin`.
- Read-only commands work without root; mutations require privileges.

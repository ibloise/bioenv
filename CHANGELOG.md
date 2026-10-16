# Changelog

## Unreleased

- Extend `bioenv db install` with optional local `--file` and `--source` inputs substituted
  through catalog `{file}` and `{source}` markers; origins are validated and never managed.
- Document external download responsibility and Kraken2 multi-step preparation limitations.


## 0.4.0

- Add optional database environment variables and declarative install arguments to tool
  database entries, while retaining legacy absolute database paths.
- Resolve relative database paths beneath `settings.databases_root`, reject path escapes, and
  export configured database variables in generated launchers.
- Add `bioenv db check` and `bioenv db install`, including a read-only `--dry-run`; database
  installs run declared commands inside the tool's Micromamba environment and are never
  automatically updated or removed.
- Document Bakta as the first declarative database example.

## 0.3.0

- Add in-place `bioenv update TOOL` using the catalog package and optional version pin.
- Add `bioenv update TOOL --latest` to omit the catalog package version pin and let the
  configured Micromamba solver choose the newest compatible version.
- Add solver-backed `update --dry-run`, existing-environment checks, executable
  verification, launcher synchronization, and per-environment mutation locks.
- Add `bioenv remove TOOL` with confirmation, `--yes`, `--dry-run`, and `--launchers-only`.
  Removal uses catalog authority, Micromamba, and validation of unchanged managed launchers;
  it does not delete environment directories recursively or attempt automatic rollback.
- Stabilize `update --latest` so a successful operation with an unchanged main package version
  reports success and still verifies executables and synchronizes launchers.
- Allow safe recovery of managed launchers after environment removal with
  `remove TOOL --launchers-only`, and serialize standalone `publish` with other mutations.
- Map the SPAdes public launcher to its `spades.py` executable in the example catalog while
  retaining list-based command declarations.

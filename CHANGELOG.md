# Changelog

## Unreleased

- Replace tool-specific database installer procedures with `bioenv db import` for prepared
  directories and files; copies use a temporary path and atomic rename.
- Add catalog `type: file` for single-file databases; directories remain the default.
- Reject symbolic links in import sources and document the Kraken2 prepare/import/check flow.


## 0.4.0

- Add optional database environment variables to tool database entries, while retaining
  legacy absolute database paths.
- Resolve relative database paths beneath `settings.databases_root`, reject path escapes, and
  export configured database variables in generated launchers.
- Add `bioenv db check` and configurable database paths.

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

# Changelog

## Unreleased

- Stabilize v0.3.0: treat a successful `update --latest` with no main-package version change
  as success, report old and new versions, and still verify executables and synchronize launchers.
- Allow safe recovery of managed launchers left after an environment has already been removed
  by using `remove TOOL --launchers-only`.
- Map the public SPAdes launcher to the Conda executable `spades.py` in the example catalog;
  list-based command catalogs remain supported.
- Coordinate standalone `publish` with `install`, `update` and `remove` using the existing
  per-tool file lock. External Micromamba commands remain outside bioenv's lock.

## 0.3.0

- Add in-place `bioenv update TOOL` using the catalog package and optional version pin.
- Add `bioenv update TOOL --latest` to omit the catalog package version pin and let the
  configured Micromamba solver choose the newest compatible version.
- Add solver-backed `update --dry-run`, existing-environment checks, executable
  verification, launcher synchronization, and per-environment mutation locks.
- Add `bioenv remove TOOL` with confirmation, `--yes`, `--dry-run`, and `--launchers-only`.
  Removal uses catalog authority, Micromamba, and validation of unchanged managed launchers;
  it does not delete environment directories recursively or attempt automatic rollback.

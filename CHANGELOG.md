# Changelog

## Unreleased

- Add `bioenv remove TOOL` with confirmation, `--yes`, `--dry-run`, and `--launchers-only`.
- Remove declared Conda prefixes through Micromamba, then clean up only unchanged launchers
  carrying the matching tool marker. Catalog declarations are the administrative authority,
  including for environments created outside bioenv.
- Report launcher conflicts and partial cleanup failures without manually deleting environment
  directories or attempting automatic rollback.

## 0.3.0

- Add in-place `bioenv update TOOL` using the catalog package and optional version pin.
- Add `bioenv update TOOL --latest` to omit the catalog package version pin and let the
  configured Micromamba solver choose the newest compatible version.
- Add solver-backed `update --dry-run`, existing-environment checks, executable
  verification, launcher synchronization, and per-environment mutation locks.

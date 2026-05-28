# Changelog

All notable changes to this collection are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
collection adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0]

Initial release.

### Added

- Collection scaffold (`mykola_kharchenko.mssql_cdc`) targeting
  `ansible-core >= 2.16`. The diff / apply / safe-recreate engine is vendored
  under `plugins/module_utils/_engine/`; the only runtime dependency outside
  Ansible is `pyodbc`.
- `cdc_db` module — enable/disable database-level CDC (idempotent,
  `check_mode` + `diff_mode`).
- `cdc_table` module — declarative capture-instance management with
  `state=present|absent`, safe `_vN` recreate (default) or in-place unsafe
  recreate, `check_mode` and `diff_mode`.
- `cdc_facts` module — read-only; registers `ansible_facts.mssql_cdc` with the
  live CDC inventory of a database.
- `cdc` role — opinionated wrapper that converges database-level state plus a
  list of capture instances and a removal list.
- Sample playbooks: `apply.yml` (fleet reconcile via role), `drift.yml`
  (cron-friendly drift detector), `enable.yml` (single-database first touch).
- CI: `ansible-test sanity` matrix over ansible-core 2.16/2.17/2.18 × Python
  3.11/3.12; PR-gated `pytest` integration suite that spins up SQL Server 2022
  via testcontainers and drives the modules through real `ansible-playbook`
  runs.
- Publishing workflow — Galaxy publish on `v*` tag via the `ansible-galaxy`
  environment and `ANSIBLE_GALAXY_API_KEY` secret (see
  [RELEASING.md](RELEASING.md)).

[Unreleased]: https://github.com/mykola-kharchenko/ansible-collection-mssql-cdc/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mykola-kharchenko/ansible-collection-mssql-cdc/releases/tag/v0.1.0

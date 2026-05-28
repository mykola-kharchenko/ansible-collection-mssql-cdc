# Changelog

All notable changes to this collection are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
collection adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0]

### Added

- `cdc_facts` now gathers source-table schema (column types, primary keys,
  identity columns, foreign keys, `MS_Description` comments) for every
  captured table, exposed under each table's `schema` key. Controlled by a
  new `gather_schema_details` option (default `true`).
- `playbooks/generate-docs.yml` + `playbooks/templates/cdc.dbml.j2` produce
  per-host per-database DBML at `docs/<inventory_hostname>/<database>.dbml`,
  driven entirely by the enriched facts (no new module required).
- New `plugins/module_utils/_engine/schema.py` housing the catalog queries
  (INFORMATION_SCHEMA.COLUMNS, sys.indexes, sys.foreign_key_columns,
  sys.extended_properties) and a pure `build_schema` helper.
- Publish workflow now gates on `ansible-test sanity` + `ansible-test units`
  before building/uploading the Galaxy tarball.

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

[Unreleased]: https://github.com/mykola-kharchenko/ansible-collection-mssql-cdc/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/mykola-kharchenko/ansible-collection-mssql-cdc/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mykola-kharchenko/ansible-collection-mssql-cdc/releases/tag/v0.1.0

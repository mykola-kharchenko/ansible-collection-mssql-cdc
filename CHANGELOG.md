# Changelog

All notable changes to this collection are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
collection adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `cdc_table` gains `captured_columns`, a **merge-based** per-column option. Each
  entry is a column name or `{name, state}` (`state` defaults to `present`):
  `present` ensures a column is captured, `absent` stops capturing it, and a
  column you do not list is left exactly as it is. Omit the option to capture all
  columns. On a brand-new table the listed `present` columns define the capture
  set.
- `cdc_table` gains `allow_partition_switch` (default `true`), exposing the last
  `sp_cdc_enable_table` parameter that was previously hardcoded. It is applied
  only on (re)create and is not a drift dimension.
- Role: each `mssql_cdc_tables` entry now accepts `state: present|absent`
  (default `present`) and `allow_partition_switch`; new
  `mssql_cdc_default_allow_partition_switch` default. Tables not listed are left
  untouched.
- `cdc_table` now runs a preflight before enabling or recreating a capture
  instance and fails with a readable message — in check mode too — instead of
  surfacing a terse `sp_cdc_enable_table` error. It checks that the source table
  exists and is a base table, is not memory-optimized, that named
  `captured_columns` exist, and that `supports_net_changes` has a primary key or
  an explicit unique `index_name` (and that a given `index_name` exists and is
  unique).

### Changed

- Capture column reconciliation is now **non-destructive**: omitting a column no
  longer removes it. This affects the deprecated `columns` option too — it is
  now treated as `captured_columns` with every column `present`. To stop
  capturing a column, mark it `state: absent`.
- A table with `supports_net_changes: false` no longer perpetually recreates
  over its `index_name` (the index only exists to support net changes).

### Deprecated

- `cdc_table`'s `columns` option — use `captured_columns`. Supplying both fails.
- Role variable `mssql_cdc_remove` — use a `state: absent` entry in
  `mssql_cdc_tables`.

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

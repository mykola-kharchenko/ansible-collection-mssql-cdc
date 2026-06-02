"""Typed configuration dataclasses for the vendored engine.

The collection's modules build :class:`Config` / :class:`Table` objects directly
from their ``argument_spec`` — there is no YAML loading or JSON-schema
validation here (that surface lived in the original CLI). :func:`merge_defaults`
is the only function modules call, and it applies the per-database ``defaults``
block to every table that did not set the inheritable fields explicitly.
"""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

from dataclasses import dataclass, field, replace
from typing import Any

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.errors import (
    ConfigError,
)

# Fields a table may inherit from the config-level ``defaults`` block when it
# does not set them explicitly.
_INHERITABLE = ("role_name", "supports_net_changes", "index_name", "filegroup_name")

_COLUMN_STATES = ("present", "absent")


@dataclass(frozen=True)
class ColumnDirective:
    """A single ``captured_columns`` entry: a column and the state desired for it.

    ``present`` means "ensure this column is captured"; ``absent`` means "ensure
    it is not". Columns not named in any directive are left exactly as they are
    (see :func:`...diff._resolve_desired_columns` for the merge rules).
    """

    name: str
    state: str = "present"


def normalize_captured_columns(raw: Any) -> list[ColumnDirective] | None:
    """Normalize the module's ``captured_columns`` input into directives.

    Accepts ``None`` (capture all — passed through unchanged), or a list whose
    items are bare strings (treated as ``state: present``) or mappings with a
    required ``name`` and an optional ``state`` (default ``present``). Raises
    :class:`ConfigError` on anything malformed so the module can surface a clean
    message rather than failing deep in the diff.
    """
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        raise ConfigError("captured_columns must be a list")

    directives: list[ColumnDirective] = []
    for item in raw:
        if isinstance(item, str):
            name, state = item, "present"
        elif isinstance(item, dict):
            name = item.get("name")
            state = item.get("state") or "present"
            extra = set(item) - {"name", "state"}
            if extra:
                raise ConfigError(
                    "captured_columns entry has unknown keys "
                    f"{sorted(extra)} (allowed: name, state)"
                )
        else:
            raise ConfigError(
                "captured_columns entries must be a string or a {name, state} mapping, "
                f"got {type(item).__name__}"
            )
        if not name or not isinstance(name, str):
            raise ConfigError("captured_columns entry is missing a 'name'")
        if state not in _COLUMN_STATES:
            raise ConfigError(
                f"captured_columns '{name}': state must be one of {list(_COLUMN_STATES)}, "
                f"got {state!r}"
            )
        directives.append(ColumnDirective(name=name, state=state))
    return directives


@dataclass
class Connection:
    """Driver-level connection options (distinct from credentials/auth)."""

    encrypt: bool = True
    trust_server_certificate: bool = False
    connect_timeout: int = 30


@dataclass
class Defaults:
    """Per-table settings inherited by tables that do not override them."""

    role_name: str | None = None
    supports_net_changes: bool = True
    index_name: str | None = None
    filegroup_name: str | None = None


@dataclass
class Table:
    """Desired CDC settings for a single source table.

    Two distinct column fields:

    - ``captured_columns`` is the caller's *intent* — a list of
      :class:`ColumnDirective` (or ``None`` for "capture all"). The diff engine
      merges these against the live captured set (present-listed kept, absent
      removed, unmentioned left alone) to compute the concrete set.
    - ``resolved_columns`` is the *resolved* explicit list (or ``None`` = all)
      that apply passes to ``sp_cdc_enable_table``. The diff stamps it onto each
      action's table, so apply never sees directives.

    An empty ``resolved_columns`` would mean "capture nothing", which CDC does
    not allow. ``_explicit`` records which fields the caller actually set, so
    :func:`merge_defaults` knows what to inherit from the per-database
    :class:`Defaults` block.
    """

    schema_name: str
    table_name: str
    capture_instance: str | None = None
    resolved_columns: list[str] | None = None
    captured_columns: list[ColumnDirective] | None = None
    role_name: str | None = None
    supports_net_changes: bool | None = None
    index_name: str | None = None
    filegroup_name: str | None = None
    allow_partition_switch: bool = True
    _explicit: frozenset[str] = field(default_factory=frozenset, repr=False, compare=False)

    @property
    def key(self) -> str:
        """The ``schema.table`` identifier used as the config map key."""
        return f"{self.schema_name}.{self.table_name}"


@dataclass
class Config:
    """The merged desired CDC state of one database."""

    version: int
    database: str
    host: str
    tables: dict[str, Table]
    port: int | None = None
    profile: str | None = None
    connection: Connection = field(default_factory=Connection)
    defaults: Defaults = field(default_factory=Defaults)


def merge_defaults(config: Config) -> Config:
    """Return a copy of ``config`` with the ``defaults`` block applied to tables.

    For every table, any inheritable field the user did not set explicitly takes
    the value from ``config.defaults``. A missing ``capture_instance`` is filled
    with SQL Server's own default of ``<schema>_<table>``.
    """
    defaults = config.defaults
    merged: dict[str, Table] = {}
    for key, table in config.tables.items():
        resolved: dict[str, Any] = {}
        for name in _INHERITABLE:
            if name in table._explicit:
                resolved[name] = getattr(table, name)
            else:
                resolved[name] = getattr(defaults, name)
        merged[key] = replace(
            table,
            capture_instance=table.capture_instance
            or f"{table.schema_name}_{table.table_name}",
            **resolved,
        )
    return replace(config, tables=merged)

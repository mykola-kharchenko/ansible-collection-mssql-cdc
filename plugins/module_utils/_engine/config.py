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

# Fields a table may inherit from the config-level ``defaults`` block when it
# does not set them explicitly.
_INHERITABLE = ("role_name", "supports_net_changes", "index_name", "filegroup_name")


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

    ``columns is None`` means "capture all columns"; an empty list would mean
    "capture nothing", which CDC does not allow. ``_explicit`` records which
    fields the caller actually set, so :func:`merge_defaults` knows what to
    inherit from the per-database :class:`Defaults` block.
    """

    schema_name: str
    table_name: str
    capture_instance: str | None = None
    columns: list[str] | None = None
    role_name: str | None = None
    supports_net_changes: bool | None = None
    index_name: str | None = None
    filegroup_name: str | None = None
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

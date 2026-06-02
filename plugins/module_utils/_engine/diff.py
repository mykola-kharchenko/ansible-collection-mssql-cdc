"""Pure reconciliation: desired :class:`Config` vs live :class:`ActualState`.

:func:`compute_diff` returns a :class:`Plan` describing what must change. It does
no I/O and is fully unit-testable. The guiding rule (see the plan doc): when in
doubt, recreate — a false recreate is merely annoying, a false "no change" is
silent data loss. Every meaningful CDC setting in SQL Server requires
disable+enable to change, so any difference becomes a recreate with a reason.
"""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

import re
from dataclasses import dataclass, field, replace

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.config import Config, Table
from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils._engine.state import ActualState, CaptureInstance


def variant_version(base: str | None, name: str) -> int | None:
    """Rank ``name`` as a version of capture-instance base ``base``.

    Safe-mode recreate adds a ``_vN`` suffix to a capture instance, so the live
    name may be ``dbo_orders`` (rank 1) or ``dbo_orders_v2`` (rank 2), ... and
    all are the *same* logical instance for diff purposes. Returns the rank, or
    ``None`` when ``name`` is unrelated to ``base`` (a genuine rename).
    """
    if base is None:
        return None
    if name == base:
        return 1
    match = re.fullmatch(re.escape(base) + r"_v(\d+)", name)
    return int(match.group(1)) if match else None


@dataclass
class CreateAction:
    """A desired table that is not yet captured — enable CDC on it."""

    table: Table

    @property
    def source(self) -> str:
        return self.table.key


@dataclass
class RecreateAction:
    """A captured table whose settings differ from the desired config."""

    table: Table
    instance: CaptureInstance
    reasons: list[str]

    @property
    def source(self) -> str:
        return self.table.key


@dataclass
class DropAction:
    """A captured table that is no longer in the config — disable CDC on it."""

    schema_name: str
    table_name: str
    instances: list[CaptureInstance]

    @property
    def source(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


@dataclass
class UnchangedAction:
    """A captured table already matching the desired config."""

    table: Table
    instance: CaptureInstance

    @property
    def source(self) -> str:
        return self.table.key


@dataclass
class Plan:
    """The full set of reconciliation actions for one database."""

    database: str
    enable_db: bool = False
    create: list[CreateAction] = field(default_factory=list)
    recreate: list[RecreateAction] = field(default_factory=list)
    drop: list[DropAction] = field(default_factory=list)
    unchanged: list[UnchangedAction] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """True if applying this plan would mutate the database."""
        return self.enable_db or bool(self.create or self.recreate or self.drop)

    def counts(self) -> dict[str, int]:
        """Action counts for summary lines and aggregated reporting."""
        return {
            "add": len(self.create),
            "recreate": len(self.recreate),
            "remove": len(self.drop),
            "unchanged": len(self.unchanged),
        }


def compute_diff(
    desired: Config,
    actual: ActualState,
    source_columns: dict[str, list[str]] | None = None,
) -> Plan:
    """Compute the reconciliation :class:`Plan` from desired vs actual state.

    Args:
        desired: A config with :func:`merge_defaults` already
            applied, so every table carries concrete settings.
        actual: The live state read from the database.
        source_columns: Optional map of ``schema.table`` -> full source column
            list, used to resolve ``captured_columns`` directives for a table
            that is not captured yet (the new-table merge baseline). Not needed
            when every desired column intent is a bare ``present`` list.

    Returns:
        A :class:`Plan` partitioning every table into create / recreate / drop /
        unchanged, plus whether database-level CDC must be enabled first. Every
        action's :class:`Table` carries a *resolved* ``columns`` list (or
        ``None`` for "capture all") — directives never reach apply.
    """
    plan = Plan(database=desired.database)

    by_source = actual.by_source()
    desired_keys = set(desired.tables)
    source_columns = source_columns or {}

    # Database-level CDC must be on before any table can be enabled.
    plan.enable_db = bool(desired.tables) and not actual.cdc_enabled

    for key, table in desired.tables.items():
        instances = by_source.get(key, [])
        if not instances:
            resolved = _resolve_desired_columns(table, None, source_columns.get(key))
            plan.create.append(CreateAction(table=_with_columns(table, resolved)))
            continue

        instance = _instance_to_compare(table, instances)
        resolved = _resolve_desired_columns(table, instance, source_columns.get(key))
        resolved_table = _with_columns(table, resolved)
        reasons = _diff_settings(resolved_table, instance)
        if reasons:
            plan.recreate.append(
                RecreateAction(table=resolved_table, instance=instance, reasons=reasons)
            )
        else:
            plan.unchanged.append(UnchangedAction(table=resolved_table, instance=instance))

    for source, instances in by_source.items():
        if source not in desired_keys:
            first = instances[0]
            plan.drop.append(
                DropAction(
                    schema_name=first.schema_name,
                    table_name=first.table_name,
                    instances=instances,
                )
            )

    return plan


def _resolve_desired_columns(
    table: Table,
    instance: CaptureInstance | None,
    source_columns: list[str] | None,
) -> set[str] | None:
    """Resolve ``captured_columns`` directives into a concrete desired set.

    Returns ``None`` for "capture all columns", otherwise the explicit set to
    capture. Merge rules (the per-column ``state`` model):

    - No directives (``captured_columns is None``): fall back to the legacy
      explicit ``columns`` list (``None`` = all). This keeps the engine's
      original exact-set behavior for callers that set ``columns`` directly.
    - Already-captured table: start from the live captured set, add the
      ``present`` columns, drop the ``absent`` ones; unmentioned columns stay.
    - Not-yet-captured table: if any ``present`` columns are listed they define
      the new capture (option ii); otherwise the baseline is every source
      column (``source_columns``), minus any ``absent``.
    """
    directives = table.captured_columns
    if directives is None:
        return set(table.columns) if table.columns is not None else None

    present = {d.name for d in directives if d.state == "present"}
    absent = {d.name for d in directives if d.state == "absent"}

    if instance is not None:
        baseline = set(instance.columns)
    elif present:
        baseline = set(present)
    else:
        baseline = set(source_columns or [])

    return (baseline | present) - absent


def _with_columns(table: Table, resolved: set[str] | None) -> Table:
    """Return ``table`` with its resolved capture list stamped onto ``columns``.

    Apply only ever reads ``columns``; sorting keeps output deterministic (CDC
    captures in source order regardless, so the list order is cosmetic).
    """
    return replace(table, columns=None if resolved is None else sorted(resolved))


def _instance_to_compare(table: Table, instances: list[CaptureInstance]) -> CaptureInstance:
    """Pick the actual instance to compare against the desired table.

    Prefer a variant of the desired ``capture_instance`` (highest version wins
    when a safe recreate left two); otherwise the first instance.
    """
    best = instances[0]
    best_rank = -1
    for inst in instances:
        rank = variant_version(table.capture_instance, inst.capture_instance)
        if rank is not None and rank > best_rank:
            best, best_rank = inst, rank
    return best


def _diff_settings(table: Table, instance: CaptureInstance) -> list[str]:
    """Return human-readable reasons the table differs from the instance (empty if none)."""
    reasons: list[str] = []

    if variant_version(table.capture_instance, instance.capture_instance) is None:
        reasons.append(
            f"capture instance renamed ({instance.capture_instance} -> {table.capture_instance})"
        )
    if bool(table.supports_net_changes) != instance.supports_net_changes:
        reasons.append(
            f"supports_net_changes changed ({instance.supports_net_changes} -> "
            f"{bool(table.supports_net_changes)})"
        )
    if table.role_name != instance.role_name:
        reasons.append(f"role_name changed ({instance.role_name} -> {table.role_name})")
    # index_name/filegroup_name are auto-selected by CDC (e.g. the PK) when not
    # specified, so a desired None means "accept whatever CDC chose" — only a
    # value the user explicitly set and that differs is a real change. The index
    # only exists to support net changes, so when those are off the live value
    # is meaningless and comparing it would cause a perpetual recreate.
    if (
        table.supports_net_changes
        and table.index_name is not None
        and table.index_name != instance.index_name
    ):
        reasons.append(f"index_name changed ({instance.index_name} -> {table.index_name})")
    if table.filegroup_name is not None and table.filegroup_name != instance.filegroup_name:
        reasons.append(
            f"filegroup_name changed ({instance.filegroup_name} -> {table.filegroup_name})"
        )

    column_reason = _diff_columns(table, instance)
    if column_reason:
        reasons.append(column_reason)

    return reasons


def _diff_columns(table: Table, instance: CaptureInstance) -> str | None:
    """Reason string if the captured column set differs, else None.

    A desired ``columns`` of ``None`` means "capture all columns", compared
    against the source table's full column set. If that set is unknown (it was
    not read), the comparison is skipped rather than risk a false recreate.
    """
    if table.columns is None:
        if not instance.source_columns:
            return None
        desired_set = set(instance.source_columns)
    else:
        desired_set = set(table.columns)

    actual_set = set(instance.columns)
    if desired_set == actual_set:
        return None

    added = sorted(desired_set - actual_set)
    removed = sorted(actual_set - desired_set)
    parts = []
    if added:
        parts.append("added " + ", ".join(added))
    if removed:
        parts.append("removed " + ", ".join(removed))
    return "columns changed (" + "; ".join(parts) + ")"

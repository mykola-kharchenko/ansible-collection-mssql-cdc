"""Tests for module-level helpers in cdc_table.py."""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.modules.cdc_table import (
    _action,
)


class _FakePlan:
    """Minimal stand-in: _action only inspects the create/recreate/drop lists."""

    def __init__(self, create=(), recreate=(), drop=()):
        self.create = list(create)
        self.recreate = list(recreate)
        self.drop = list(drop)


def test_action_created():
    assert _action(_FakePlan(create=["x"])) == "created"


def test_action_recreated():
    assert _action(_FakePlan(recreate=["x"])) == "recreated"


def test_action_dropped():
    assert _action(_FakePlan(drop=["x"])) == "dropped"


def test_action_unchanged():
    assert _action(_FakePlan()) == "unchanged"


def test_action_create_takes_precedence_over_recreate():
    # A single source-table plan never mixes these, but the order is defined.
    assert _action(_FakePlan(create=["x"], recreate=["y"])) == "created"

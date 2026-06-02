"""Tests for the shared module helpers in cdc.py."""

from __future__ import absolute_import, division, print_function
from __future__ import annotations

__metaclass__ = type

from ansible_collections.mykola_kharchenko.mssql_cdc.plugins.module_utils.cdc import (
    make_diff,
)


def test_make_diff_renders_cyrillic_unescaped():
    # Cyrillic schema/column names must show as readable characters in --diff,
    # not as \uXXXX escapes (ensure_ascii=False).
    diff = make_diff(
        {"captured": False},
        {"captured": True, "columns": ["Имя", "Адрес"]},
    )
    assert "Имя" in diff["after"]
    assert "Адрес" in diff["after"]
    assert "\\u" not in diff["after"]


def test_make_diff_is_deterministic_and_newline_terminated():
    before = {"b": 1, "a": 2}
    rendered = make_diff(before, before)["before"]
    # sort_keys -> 'a' before 'b'; trailing newline for clean diff rendering.
    assert rendered.index('"a"') < rendered.index('"b"')
    assert rendered.endswith("\n")

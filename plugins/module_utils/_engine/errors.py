"""Typed exceptions and the CLI exit-code contract.

Every failure mode the tool surfaces maps to one of these exceptions, and each
exception carries the process exit code the CLI should return. Keeping the codes
on the exceptions (rather than scattered through ``cli.py``) means a command can
simply let the exception propagate and a single top-level handler translates it.
"""

from __future__ import absolute_import, annotations, division, print_function

__metaclass__ = type


class MssqlCdcMgrError(Exception):
    """Base class for all errors raised by the tool.

    Attributes:
        exit_code: Process exit code the CLI returns when this propagates.
    """

    exit_code: int = 1


class ConfigError(MssqlCdcMgrError):
    """A configuration or credentials file is invalid (schema or semantics)."""

    exit_code = 2


class InterpolationError(ConfigError):
    """A ``${...}`` credential reference could not be resolved."""

    exit_code = 2


class DatabaseError(MssqlCdcMgrError):
    """A database connection or runtime failure (connect, query, proc)."""

    exit_code = 3


class DriftError(MssqlCdcMgrError):
    """Drift was detected by ``diff`` — not a failure, but a signal for CI."""

    exit_code = 1

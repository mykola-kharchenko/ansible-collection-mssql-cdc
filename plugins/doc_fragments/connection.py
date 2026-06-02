# -*- coding: utf-8 -*-
#
# (c) 2026, Mykola Kharchenko <mykola.kharchenko@outlook.com>
# MIT License

from __future__ import absolute_import, division, print_function

__metaclass__ = type


class ModuleDocFragment(object):
    # Connection options shared by every module in this collection. The matching
    # runtime arguments live in COMMON_ARGUMENT_SPEC (plugins/module_utils/cdc.py);
    # keep the two in sync. Options whose meaning differs per module — login_user
    # (manage vs. read-only) and database (which database, and why) — are
    # intentionally documented in each module instead of here.
    DOCUMENTATION = r"""
options:
  host:
    description: SQL Server host name or address.
    type: str
    required: true
    aliases: [login_host, server]
  port:
    description: TCP port the SQL Server listens on.
    type: int
    default: 1433
  login_password:
    description: Password for I(login_user). Use Ansible Vault.
    type: str
    required: true
  encrypt:
    description: Enable TLS on the connection.
    type: bool
    default: true
  trust_server_certificate:
    description: Trust a self-signed or otherwise unverifiable server certificate.
    type: bool
    default: false
  connect_timeout:
    description: Connection timeout in seconds.
    type: int
    default: 30
"""

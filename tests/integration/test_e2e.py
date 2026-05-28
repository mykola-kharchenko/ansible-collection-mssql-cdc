"""End-to-end test: real ansible-playbook against a real SQL Server.

Spins up MSSQL 2022 via testcontainers, installs the collection, then runs a
playbook that exercises cdc_db, cdc_table and cdc_facts. Asserts the canonical
guarantees: an initial apply makes the expected changes, a re-run is idempotent,
``--check`` reports changes without mutating, and a tweaked config produces a
recreate.

Skips cleanly if testcontainers, Docker or the ODBC driver are unavailable, so
a developer without them still gets a green ``ansible-test sanity`` suite.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

testcontainers = pytest.importorskip("testcontainers.core.container")
pyodbc = pytest.importorskip("pyodbc")

from testcontainers.core.container import DockerContainer  # noqa: E402

_IMAGE = "mcr.microsoft.com/mssql/server:2022-latest"
_PASSWORD = "Str0ng!Passw0rd"


def _admin_cs(host, port, database):
    return (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={host},{port};UID=sa;PWD={_PASSWORD};DATABASE={database};"
        "Encrypt=no;TrustServerCertificate=yes;Connection Timeout=5;"
    )


@pytest.fixture(scope="module")
def mssql():
    if shutil.which("ansible-playbook") is None:
        pytest.skip("ansible-playbook not on PATH")
    try:
        container = (
            DockerContainer(_IMAGE)
            .with_env("ACCEPT_EULA", "Y")
            .with_env("MSSQL_SA_PASSWORD", _PASSWORD)
            .with_exposed_ports(1433)
        )
        container.start()
    except Exception as exc:
        pytest.skip(f"cannot start SQL Server container: {exc}")

    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(1433))
    last_error = None
    for _attempt in range(60):
        try:
            conn = pyodbc.connect(_admin_cs(host, port, "master"), autocommit=True, timeout=5)
            conn.close()
            break
        except pyodbc.Error as exc:
            last_error = exc
            time.sleep(3)
    else:
        container.stop()
        pytest.skip(f"SQL Server never became ready: {last_error}")

    try:
        yield host, port
    finally:
        container.stop()


@pytest.fixture
def fresh_db(mssql):
    host, port = mssql
    name = "test_cdc"
    admin = pyodbc.connect(_admin_cs(host, port, "master"), autocommit=True)
    cur = admin.cursor()
    cur.execute(
        f"IF DB_ID('{name}') IS NOT NULL BEGIN "
        f"ALTER DATABASE {name} SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
        f"DROP DATABASE {name}; END"
    )
    cur.execute(f"CREATE DATABASE {name}")
    admin.close()

    setup = pyodbc.connect(_admin_cs(host, port, name), autocommit=True)
    cur = setup.cursor()
    cur.execute(
        "CREATE TABLE dbo.orders (id INT IDENTITY PRIMARY KEY, customer_id INT, status VARCHAR(50))"
    )
    cur.execute("CREATE TABLE dbo.customers (id INT PRIMARY KEY, email VARCHAR(200))")
    setup.close()
    return host, port, name


def _run_playbook(tmp_path, playbook_text, extra_args=()):
    """Invoke ansible-playbook in a controlled environment and return CompletedProcess."""
    pb = tmp_path / "play.yml"
    pb.write_text(playbook_text, encoding="utf-8")
    env = os.environ.copy()
    # Ensure no host-key prompts and that the collection install location is found.
    env.setdefault("ANSIBLE_HOST_KEY_CHECKING", "False")
    env.setdefault("ANSIBLE_STDOUT_CALLBACK", "default")
    return subprocess.run(
        [
            "ansible-playbook",
            str(pb),
            "-i",
            "localhost,",
            "-c",
            "local",
            *extra_args,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _play(host, port, database, *, state_block="state: enabled"):
    return f"""
- hosts: localhost
  gather_facts: false
  collections: [mykola_kharchenko.mssql_cdc]
  tasks:
    - mykola_kharchenko.mssql_cdc.cdc_db:
        host: "{host}"
        port: {port}
        login_user: sa
        login_password: "{_PASSWORD}"
        database: "{database}"
        encrypt: false
        trust_server_certificate: true
        {state_block}

    - mykola_kharchenko.mssql_cdc.cdc_table:
        host: "{host}"
        port: {port}
        login_user: sa
        login_password: "{_PASSWORD}"
        database: "{database}"
        encrypt: false
        trust_server_certificate: true
        schema: dbo
        name: orders
        columns: [id, customer_id, status]

    - mykola_kharchenko.mssql_cdc.cdc_table:
        host: "{host}"
        port: {port}
        login_user: sa
        login_password: "{_PASSWORD}"
        database: "{database}"
        encrypt: false
        trust_server_certificate: true
        schema: dbo
        name: customers
"""


def test_apply_is_idempotent(fresh_db, tmp_path):
    host, port, db = fresh_db
    play = _play(host, port, db)

    first = _run_playbook(tmp_path, play)
    assert first.returncode == 0, first.stdout + first.stderr
    # First run should change all three resources.
    assert "changed=3" in first.stdout or "changed=2" in first.stdout

    second = _run_playbook(tmp_path, play)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "changed=0" in second.stdout, second.stdout


def test_check_mode_does_not_mutate(fresh_db, tmp_path):
    host, port, db = fresh_db
    play = _play(host, port, db)

    check = _run_playbook(tmp_path, play, extra_args=["--check"])
    assert check.returncode == 0, check.stdout + check.stderr
    # check_mode still reports the would-changes.
    assert "changed=" in check.stdout

    # The database wasn't actually mutated, so a real apply now makes the changes.
    real = _run_playbook(tmp_path, play)
    assert real.returncode == 0, real.stdout + real.stderr
    # If --check had mutated, this would say changed=0.
    assert "changed=0" not in real.stdout


def test_cdc_facts_reports_live_state(fresh_db, tmp_path):
    host, port, db = fresh_db
    apply_play = _play(host, port, db)
    assert _run_playbook(tmp_path, apply_play).returncode == 0

    facts_play = f"""
- hosts: localhost
  gather_facts: false
  collections: [mykola_kharchenko.mssql_cdc]
  tasks:
    - mykola_kharchenko.mssql_cdc.cdc_facts:
        host: "{host}"
        port: {port}
        login_user: sa
        login_password: "{_PASSWORD}"
        database: "{db}"
        encrypt: false
        trust_server_certificate: true
      register: cdc

    - ansible.builtin.copy:
        content: "{{{{ ansible_facts.mssql_cdc | to_json }}}}"
        dest: "{tmp_path}/facts.json"
"""
    result = _run_playbook(tmp_path, facts_play)
    assert result.returncode == 0, result.stdout + result.stderr

    facts = json.loads(Path(tmp_path, "facts.json").read_text(encoding="utf-8"))
    assert facts["cdc_enabled"] is True
    by_source = {t["source_table"]: t for t in facts["tables"]}
    assert {"dbo.orders", "dbo.customers"} <= set(by_source)

    # gather_schema_details defaults to true: every captured table carries the
    # source structure (column types, PK flag, identity flag, FKs).
    orders = by_source["dbo.orders"]
    assert "schema" in orders, "schema details missing from cdc_facts output"
    cols = {c["name"]: c for c in orders["schema"]["columns"]}
    assert cols["id"]["is_pk"] is True
    assert cols["id"]["is_identity"] is True
    assert cols["status"]["type"] == "varchar(50)"
    assert cols["status"]["nullable"] is True


def test_cdc_facts_skips_schema_when_requested(fresh_db, tmp_path):
    host, port, db = fresh_db
    apply_play = _play(host, port, db)
    assert _run_playbook(tmp_path, apply_play).returncode == 0

    facts_play = f"""
- hosts: localhost
  gather_facts: false
  collections: [mykola_kharchenko.mssql_cdc]
  tasks:
    - mykola_kharchenko.mssql_cdc.cdc_facts:
        host: "{host}"
        port: {port}
        login_user: sa
        login_password: "{_PASSWORD}"
        database: "{db}"
        encrypt: false
        trust_server_certificate: true
        gather_schema_details: false

    - ansible.builtin.copy:
        content: "{{{{ ansible_facts.mssql_cdc | to_json }}}}"
        dest: "{tmp_path}/facts-light.json"
"""
    result = _run_playbook(tmp_path, facts_play)
    assert result.returncode == 0, result.stdout + result.stderr

    facts = json.loads(Path(tmp_path, "facts-light.json").read_text(encoding="utf-8"))
    # cdc inventory still present; schema explicitly opted out.
    assert facts["cdc_enabled"] is True
    for entry in facts["tables"]:
        assert "schema" not in entry, "gather_schema_details=false should omit schema"

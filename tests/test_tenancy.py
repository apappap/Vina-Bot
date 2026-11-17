import sqlite3
import threading
from contextlib import closing

import pytest

from nl2sql_demo.tenancy import NoTenant, connect, current, submit, tenant_scope


def test_no_tenant_means_no_database():
    with pytest.raises(NoTenant):
        connect()


def test_each_scope_sees_only_its_own_database(tenants):
    counts = {}
    for code in ("acme", "globex"):
        with tenant_scope(tenants[code]), closing(connect()) as con:
            counts[code] = con.execute("SELECT COUNT(*) FROM parcels").fetchone()[0]
    assert counts == {"acme": 3000, "globex": 600}


def test_connections_are_read_only(tenants):
    with tenant_scope(tenants["acme"]), closing(connect()) as con:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("DELETE FROM parcels")


def test_a_plain_thread_loses_the_tenant_and_fails_closed(tenants):
    seen = []

    def work():
        try:
            seen.append(current().code)
        except NoTenant:
            seen.append("refused")

    with tenant_scope(tenants["acme"]):
        worker = threading.Thread(target=work)
        worker.start()
        worker.join()
    assert seen == ["refused"]  # it did not quietly fall back to some default database


def test_submit_carries_the_tenant_into_the_worker(tenants):
    with tenant_scope(tenants["globex"]):
        assert submit(lambda: current().code).result(timeout=5) == "globex"

import pytest

from nl2sql_demo.guard import check

ALLOWED = {"parcels", "warehouses", "loads", "load_items"}
COLUMNS = {"parcels": {"tracking_no", "status"}, "app_users": {"id", "name", "password_hash"}}


def guard(sql, **kwargs):
    kwargs.setdefault("allowed_tables", ALLOWED)
    kwargs.setdefault("table_columns", COLUMNS)
    return check(sql, **kwargs)


def test_a_plain_select_passes_and_gets_a_row_limit():
    r = guard("SELECT tracking_no FROM parcels WHERE status = 'D'", max_rows=100)
    assert r.ok, r.reason
    assert r.sql.endswith("LIMIT 100")
    assert "LIMIT" not in r.sql_unlimited
    assert r.tables == ["parcels"]


def test_the_row_limit_follows_the_target_dialect():
    r = guard("SELECT tracking_no FROM parcels ORDER BY tracking_no", max_rows=50, dialect="tsql")
    assert r.ok and r.sql.startswith("SELECT TOP 50")


def test_a_smaller_limit_is_kept_and_a_larger_one_is_capped():
    assert guard("SELECT tracking_no FROM parcels LIMIT 5", max_rows=100).sql.endswith("LIMIT 5")
    assert guard("SELECT tracking_no FROM parcels LIMIT 5000", max_rows=100).sql.endswith("LIMIT 100")


def test_a_union_is_limited_as_a_whole():
    r = guard("SELECT tracking_no FROM parcels UNION SELECT tracking_no FROM load_items", max_rows=10)
    assert r.ok and r.sql.endswith("LIMIT 10")


def test_cte_names_are_not_mistaken_for_tables():
    r = guard("WITH d AS (SELECT tracking_no FROM parcels WHERE status = 'D') SELECT COUNT(*) FROM d")
    assert r.ok, r.reason


@pytest.mark.parametrize(
    "sql, dialect, reason",
    [
        ("DELETE FROM parcels", "sqlite", "only SELECT"),
        ("UPDATE parcels SET status = 'D'", "sqlite", "only SELECT"),
        ("SELECT 1; DELETE FROM parcels", "sqlite", "exactly one statement"),
        ("SELECT 1 -- looks harmless\n; DROP TABLE parcels", "sqlite", "exactly one statement"),
        ("WITH x AS (DELETE FROM parcels RETURNING *) SELECT * FROM x", "postgres", "DELETE"),
        ("SELECT * INTO backup FROM parcels", "tsql", "INTO"),
        ("SELECT * FROM other_tenant.parcels", "mysql", "cross-database"),
        ("SELECT p.tracking_no FROM parcels p STRAIGHT_JOIN other_tenant.parcels q ON 1 = 1", "mysql", "cross-database"),
        ("SELECT /*!50000 tracking_no */ FROM parcels", "mysql", "executable comments"),
        ("SELECT * FROM app_users", "sqlite", "not available"),
        ("SELECT name FROM sqlite_master", "sqlite", "not available"),
        ("SELECT tracking_no FROM parcels WHERE sleep(5) = 0", "mysql", "function sleep"),
        ("SELECT password_hash FROM parcels", "sqlite", "sensitive"),
    ],
)
def test_refused(sql, dialect, reason):
    r = guard(sql, dialect=dialect)
    assert not r.ok
    assert reason in r.reason


def test_select_star_cannot_expose_sensitive_columns():
    r = check("SELECT * FROM app_users", allowed_tables={"app_users"}, table_columns=COLUMNS)
    assert not r.ok and "sensitive" in r.reason


def test_garbage_is_refused():
    assert not guard("SELECT (((").ok

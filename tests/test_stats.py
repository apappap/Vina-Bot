import sqlite3

from nl2sql_demo.stats import full_stats


def test_statistics_cover_every_row_not_just_the_fetched_ones():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (wh TEXT, qty INTEGER, day TEXT)")
    con.executemany(
        "INSERT INTO t VALUES (?, ?, ?)",
        [("A" if i < 20 else "B", i, f"2026-10-{i + 1:02d}") for i in range(25)],
    )
    sql = "SELECT wh, qty, day FROM t ORDER BY wh"
    sample = con.execute(sql + " LIMIT 10").fetchall()
    assert {row[0] for row in sample} == {"A"}  # the fetched rows alone would say "only warehouse A"

    stats = full_stats(con, sql, sample, ["wh", "qty", "day"])

    assert stats["total_rows"] == 25
    assert stats["columns"]["wh"]["distribution"] == {"A": 20, "B": 5}
    assert stats["columns"]["qty"]["sum"] == sum(range(25))
    assert (stats["columns"]["day"]["min"], stats["columns"]["day"]["max"]) == ("2026-10-01", "2026-10-25")

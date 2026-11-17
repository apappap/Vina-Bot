"""Full-result statistics, computed by the database.

A result is fetched with a row limit so a careless query cannot drag millions
of rows into memory. The trap is to then summarise only the rows that came
back: if the limit is 10,000 and the query matches 25,000 rows, totals and
distributions computed in Python are simply wrong.

When the limit is hit, the original query (without the limit) is wrapped as a
subquery and the database computes the totals over every row.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def full_stats(
    con: sqlite3.Connection,
    sql_unlimited: str,
    sample_rows: list[tuple],
    columns: list[str],
    *,
    max_columns: int = 60,
    distribution_max: int = 15,
) -> dict[str, Any]:
    src = f"({sql_unlimited}) AS q"
    total = con.execute(f"SELECT COUNT(*) FROM {src}").fetchone()[0]
    out: dict[str, Any] = {"total_rows": total, "columns": {}}
    for i, col in enumerate(columns[:max_columns]):
        qc = _quote(col)
        values = [row[i] for row in sample_rows if row[i] is not None]
        if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            s, lo, hi, avg = con.execute(f"SELECT SUM({qc}), MIN({qc}), MAX({qc}), AVG({qc}) FROM {src}").fetchone()
            out["columns"][col] = {"type": "number", "sum": s, "min": lo, "max": hi,
                                   "avg": round(avg, 2) if avg is not None else None}
        elif values and all(isinstance(v, str) and DATE.match(v) for v in values):
            lo, hi = con.execute(f"SELECT MIN({qc}), MAX({qc}) FROM {src}").fetchone()
            out["columns"][col] = {"type": "date", "min": lo, "max": hi}
        else:
            distinct = con.execute(f"SELECT COUNT(DISTINCT {qc}) FROM {src}").fetchone()[0]
            info: dict[str, Any] = {"type": "text", "distinct": distinct}
            if distinct == 1 and values:
                info["value"] = values[0]
            elif 1 < distinct <= distribution_max:
                rows = con.execute(
                    f"SELECT {qc}, COUNT(*) AS n FROM {src} GROUP BY {qc} ORDER BY n DESC, {qc}"
                ).fetchall()
                info["distribution"] = {str(k): n for k, n in rows}
            out["columns"][col] = info
    return out

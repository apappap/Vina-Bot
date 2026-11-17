"""Ask every golden question and grade the answer against SQL whose result is known to be right.

    python evals/run_golden.py                 # offline model
    python evals/run_golden.py --llm claude    # real model: this is how a prompt or model change is judged

A case passes when every value the truth query returns also appears in the answer's rows
(column names and order do not matter), or, for "expect": "blocked", when the guard refused it.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nl2sql_demo.__main__ import DEMO_TODAY, make_pipeline  # noqa: E402
from nl2sql_demo.pipeline import Pipeline  # noqa: E402
from nl2sql_demo.tenancy import connect, tenant_scope  # noqa: E402


def _values(rows: list[tuple]) -> Counter:
    return Counter(str(v) for row in rows for v in row)


def grade(pipeline: Pipeline, case: dict) -> tuple[bool, str]:
    with tenant_scope(pipeline.tenants[case["tenant"]]):
        ans = pipeline.answer(case["question"], record_failures=False)
        if case.get("expect") == "blocked":
            ok = bool(ans.error and ans.error.startswith("blocked"))
            return ok, ans.error or "answered, but should have been blocked"
        if ans.error:
            return False, ans.error
        con = connect()
        try:
            truth = con.execute(case["truth_sql"]).fetchall()
        except sqlite3.Error as e:
            return False, f"truth SQL failed: {e}"
        finally:
            con.close()
        missing = _values(truth) - _values(ans.rows)
        return (not missing), ("ok" if not missing else f"missing values: {list(missing)[:5]}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", choices=["fake", "claude"], default="fake")
    parser.add_argument("--data", type=Path, default=ROOT / "demo_data")
    parser.add_argument("--cases", type=Path, default=ROOT / "evals" / "golden.json")
    args = parser.parse_args(argv)

    pipeline = make_pipeline(args.data, args.llm, DEMO_TODAY, 10_000)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    passed = 0
    for case in cases:
        ok, why = grade(pipeline, case)
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']}  {case['question']}  {'' if ok else why}")
    print(f"\n{passed}/{len(cases)} passed")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""One question, start to finish.

1. The tenant comes from the request scope (``tenancy``); every step uses only that tenant's
   database, knowledge and templates.
2. A verified template for this exact question? Run it, no model call.
3. Otherwise two-stage retrieval builds a small, precise context and the model writes SQL.
4. The guard checks the SQL on its syntax tree and adds a row limit.
5. Run it. If the database reports a fixable error (unknown column, syntax), hand the real
   error back to the model once.
6. If the row limit was hit, the database computes full-result statistics.
7. Anything that could not be answered goes to the feedback queue.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .feedback import FeedbackQueue
from .guard import GuardResult, check
from .knowledge import TableDoc, build_context, default_knowledge, retrieve
from .llm import CANNOT, LLM
from .stats import full_stats
from .templates import TemplateStore
from .tenancy import Tenant, connect, current

REPAIRABLE = ("no such column", "no such table", "syntax error", "ambiguous column", "misuse of aggregate")


@dataclass
class Answer:
    question: str
    source: str = "none"  # template | llm | none
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    truncated: bool = False
    stats: dict[str, Any] | None = None
    repaired: bool = False
    error: str | None = None


def _run(con: sqlite3.Connection, sql: str) -> tuple[list[str], list[tuple]]:
    cur = con.execute(sql)
    return [d[0] for d in cur.description], cur.fetchall()


class Pipeline:
    def __init__(
        self,
        tenants: dict[str, Tenant],
        llm: LLM,
        *,
        knowledge: dict[str, list[TableDoc]] | None = None,
        max_rows: int = 10_000,
        today: date | None = None,
        queue: FeedbackQueue | None = None,
    ) -> None:
        self.tenants = tenants
        self.llm = llm
        self.knowledge = knowledge or {code: default_knowledge(code) for code in tenants}
        self.templates = {code: TemplateStore() for code in tenants}
        self.max_rows = max_rows
        self.today = today or date.today()
        self.queue = queue or FeedbackQueue()

    def templates_for(self, tenant_code: str) -> TemplateStore:
        return self.templates[tenant_code]

    def guard(self, sql: str, con: sqlite3.Connection) -> GuardResult:
        tenant = current()
        allowed = {doc.name for doc in self.knowledge[tenant.code]}  # only documented tables
        columns = {
            name: {row[1] for row in con.execute(f'PRAGMA table_info("{name}")')}
            for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        return check(sql, allowed_tables=allowed, table_columns=columns, max_rows=self.max_rows, dialect=tenant.dialect)

    def _fail(self, ans: Answer, reason: str, detail: str, user: str, record: bool) -> Answer:
        ans.error = f"{reason}: {detail}"
        if record:
            self.queue.report(current().code, ans.question, reason, detail, user)
        return ans

    def answer(self, question: str, *, user: str = "anonymous", record_failures: bool = True) -> Answer:
        tenant = current()
        ans = Answer(question)
        con = connect()
        try:
            context = ""
            sql = self.templates[tenant.code].get(question)
            if sql:
                ans.source = "template"
            else:
                docs = retrieve(question, self.knowledge[tenant.code])
                ans.tables = [d.name for d in docs]
                context = build_context(con, docs, question)
                try:
                    sql = self.llm.generate_sql(question, context, tenant.dialect, self.today)
                except Exception as e:  # noqa: BLE001 - any model failure means "could not answer"
                    return self._fail(ans, "model_unavailable", str(e), user, record_failures)
                ans.source = "llm"
                if not sql or sql.strip().startswith(CANNOT):
                    return self._fail(ans, "cannot_generate", "the model could not write SQL for this", user, record_failures)

            guarded = self.guard(sql, con)
            if not guarded.ok:
                ans.sql = sql
                return self._fail(ans, "blocked", guarded.reason, user, record_failures)
            try:
                columns, rows = _run(con, guarded.sql)
            except sqlite3.Error as e:
                if ans.source != "llm" or not any(k in str(e).lower() for k in REPAIRABLE):
                    return self._fail(ans, "sql_error", str(e), user, record_failures)
                fixed = self.llm.fix_sql(question, context, tenant.dialect, self.today, guarded.sql_unlimited, str(e))
                if not fixed or fixed.strip().startswith(CANNOT):
                    return self._fail(ans, "sql_error", str(e), user, record_failures)
                guarded = self.guard(fixed, con)
                if not guarded.ok:
                    return self._fail(ans, "blocked", guarded.reason, user, record_failures)
                try:
                    columns, rows = _run(con, guarded.sql)
                except sqlite3.Error as e2:
                    return self._fail(ans, "sql_error", str(e2), user, record_failures)
                ans.repaired = True

            ans.sql, ans.columns, ans.rows = guarded.sql, columns, rows
            if not ans.tables:
                ans.tables = guarded.tables
            if len(rows) >= self.max_rows:
                ans.stats = full_stats(con, guarded.sql_unlimited, rows, columns)
                ans.truncated = ans.stats["total_rows"] > len(rows)
            return ans
        finally:
            con.close()

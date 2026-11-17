"""The model that turns a question into SQL.

``FakeLLM`` is the default: a deterministic stand-in that knows the demo
questions, so the whole project (and its tests) runs offline. ``ClaudeLLM``
calls the real model and is opt-in (``--llm claude``).

Whichever model is used, its output is only a proposal: the guard checks it
before anything runs.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Protocol

CANNOT = "-- CANNOT_ANSWER"


class LLM(Protocol):
    def generate_sql(self, question: str, context: str, dialect: str, today: date) -> str: ...

    def fix_sql(self, question: str, context: str, dialect: str, today: date, bad_sql: str, error: str) -> str: ...


class FakeLLM:
    """Recognises the demo questions. A few answers are deliberately bad, to show the guard,
    the one-shot repair and the feedback queue at work."""

    def generate_sql(self, question: str, context: str, dialect: str, today: date) -> str:
        q = question.lower()
        if "删" in q or "delete" in q:
            return "DELETE FROM parcels WHERE status = 'X'"
        if "密码" in q or "password" in q:
            return "SELECT name, password_hash FROM app_users"
        if "globex" in q:
            return "SELECT * FROM globex.parcels"
        if "上个月" in q and "送达" in q:
            first = today.replace(day=1)
            prev_first = (first - timedelta(days=1)).replace(day=1)
            return (
                "SELECT COUNT(*) AS delivered FROM parcels WHERE status = 'D' "
                f"AND delivered_at >= '{prev_first.isoformat()}' AND delivered_at < '{first.isoformat()}'"
            )
        if "在途" in q:
            return (
                "SELECT warehouse_code, COUNT(*) AS in_transit FROM parcels "
                "WHERE status IN ('R', 'L') GROUP BY warehouse_code ORDER BY in_transit DESC"
            )
        load = re.search(r"ld\d{4}", q)
        if load:
            return (
                "SELECT p.tracking_no, p.status, p.cartons FROM load_items AS li "
                "JOIN parcels AS p ON p.tracking_no = li.tracking_no "
                f"WHERE li.load_no = '{load.group(0).upper()}' ORDER BY p.tracking_no"
            )
        if "城市" in q:
            # Wrong column on purpose (the table has `city`): shows the one-shot repair.
            return (
                "SELECT w.city_name, COUNT(*) AS parcels FROM parcels AS p "
                "JOIN warehouses AS w ON w.code = p.warehouse_code GROUP BY w.city_name"
            )
        if "已送达" in q and ("列出" in q or "所有" in q):
            return "SELECT tracking_no, warehouse_code, cartons, delivered_at FROM parcels WHERE status = 'D'"
        return CANNOT

    def fix_sql(self, question: str, context: str, dialect: str, today: date, bad_sql: str, error: str) -> str:
        if "city_name" in bad_sql and "no such column" in error.lower():
            return bad_sql.replace("city_name", "city")
        return CANNOT


SYSTEM_PROMPT = (
    "You translate a warehouse question into one read-only SQL query for the database described "
    "in the user message. Use only the tables and columns listed there. Use the code meanings "
    "listed (for example status codes); never guess a code that is not listed. Reply with the SQL "
    "inside a ```sql block and nothing else. If the listed tables cannot answer the question, "
    f"reply with exactly: {CANNOT}"
)


def extract_sql(text: str) -> str:
    if CANNOT in text:
        return CANNOT
    fenced = re.search(r"```(?:sql)?\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    return (fenced.group(1) if fenced else text).strip()


class ClaudeLLM:
    """Calls Claude through the official Anthropic SDK (``pip install anthropic``).

    Credentials come from the environment: ``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile.
    Server-side refusal fallbacks are turned on: if the model declines, the API re-runs the
    request on a fallback model inside the same call.
    """

    def __init__(self, model: str = "claude-opus-5-5") -> None:
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model

    def _ask(self, prompt: str) -> str:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            return CANNOT
        return extract_sql("".join(block.text for block in response.content if block.type == "text"))

    def generate_sql(self, question: str, context: str, dialect: str, today: date) -> str:
        return self._ask(f"Database type: {dialect}\nToday is {today.isoformat()}.\n\n{context}\n\nQuestion: {question}")

    def fix_sql(self, question: str, context: str, dialect: str, today: date, bad_sql: str, error: str) -> str:
        return self._ask(
            f"Database type: {dialect}\nToday is {today.isoformat()}.\n\n{context}\n\nQuestion: {question}\n\n"
            f"This query failed:\n{bad_sql}\n\nThe database said:\n{error}\n\nReturn a corrected query."
        )

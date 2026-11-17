"""Failure queue and the training loop.

Questions the assistant could not answer (or a user marked wrong) land in a
queue. The same question asked by several people is one item, so the queue
can be sorted by how many people are affected.

Training an item follows a strict order:

1. Run the engineer's SQL first. If it does not run, nothing is written.
2. Snapshot the templates, then write the new one.
3. Ask the question again exactly the way the user did (no model involved):
   it must now come back from the template. If not, roll back to the snapshot.
4. Only then mark the item resolved and return the people to notify.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .templates import normalize
from .tenancy import connect, tenant_scope

if TYPE_CHECKING:
    from .pipeline import Pipeline


@dataclass
class FeedbackItem:
    id: int
    tenant: str
    question: str
    reason: str
    detail: str
    users: set[str] = field(default_factory=set)
    status: str = "open"
    fingerprint: str = ""


class FeedbackQueue:
    def __init__(self) -> None:
        self._items: dict[int, FeedbackItem] = {}
        self._next_id = 1

    @staticmethod
    def fingerprint(tenant: str, question: str) -> str:
        return hashlib.sha1(f"{tenant}|{normalize(question)}".encode("utf-8")).hexdigest()[:12]

    def report(self, tenant: str, question: str, reason: str, detail: str = "", user: str = "anonymous") -> FeedbackItem:
        fp = self.fingerprint(tenant, question)
        for item in self._items.values():
            if item.status == "open" and item.fingerprint == fp:
                item.users.add(user)
                item.reason, item.detail = reason, detail
                return item
        item = FeedbackItem(self._next_id, tenant, question, reason, detail, {user}, fingerprint=fp)
        self._items[item.id] = item
        self._next_id += 1
        return item

    def open_items(self) -> list[FeedbackItem]:
        items = [i for i in self._items.values() if i.status == "open"]
        return sorted(items, key=lambda i: (-len(i.users), i.id))

    def get(self, item_id: int) -> FeedbackItem:
        return self._items[item_id]


@dataclass
class TrainResult:
    ok: bool
    message: str
    notify: list[str] = field(default_factory=list)


def train(pipeline: "Pipeline", item_id: int, sql: str) -> TrainResult:
    item = pipeline.queue.get(item_id)
    if item.status != "open":
        return TrainResult(False, "this item is already closed")
    with tenant_scope(pipeline.tenants[item.tenant]):
        con = connect()
        try:
            guarded = pipeline.guard(sql, con)
            if not guarded.ok:
                return TrainResult(False, f"refused by the guard: {guarded.reason}")
            try:
                con.execute(guarded.sql).fetchall()
            except sqlite3.Error as e:
                return TrainResult(False, f"the SQL does not run: {e}")
        finally:
            con.close()

        store = pipeline.templates_for(item.tenant)
        snapshot = store.snapshot()
        store.add(item.question, guarded.sql_unlimited)

        again = pipeline.answer(item.question, record_failures=False)
        if again.error or again.source != "template":
            store.restore(snapshot)
            return TrainResult(False, "re-asking the question did not use the new template; rolled back")

    item.status = "resolved"
    return TrainResult(True, "resolved", notify=sorted(item.users))

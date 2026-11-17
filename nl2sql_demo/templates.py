"""Verified SQL templates: questions an engineer has already answered correctly.

A question that matches a template (after normalising spaces and punctuation)
runs the stored SQL directly, without calling the model. That makes common
questions fast, free and identical every time.
"""
from __future__ import annotations

import re


def normalize(question: str) -> str:
    return re.sub(r"[\W_]+", "", question.lower())


class TemplateStore:
    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    def get(self, question: str) -> str | None:
        return self._items.get(normalize(question))

    def add(self, question: str, sql: str) -> None:
        self._items[normalize(question)] = sql

    def snapshot(self) -> dict[str, str]:
        return dict(self._items)

    def restore(self, snapshot: dict[str, str]) -> None:
        self._items = dict(snapshot)

    def __len__(self) -> int:
        return len(self._items)

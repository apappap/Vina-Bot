"""Per-tenant knowledge about the tables, and two-stage retrieval over it.

Stage 1 finds the few tables that matter for a question. Stage 2 sends only
those tables' exact DDL, plus what their columns and codes mean, to the model.

A production system would use an embedding model for stage 1. This demo uses a
bag-of-words cosine over English words and Chinese character pairs, so it runs
without any model; the interface is the same.
"""
from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class TableDoc:
    name: str
    description: str
    columns: dict[str, str]
    codes: dict[str, dict[str, str]] = field(default_factory=dict)
    joins: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [self.name, self.description, *self.columns, *self.columns.values(), *self.rules, *self.keywords]
        for codes in self.codes.values():
            parts += codes.values()
        return " ".join(parts)


def tokens(text: str) -> Counter:
    text = text.lower()
    words = re.findall(r"[a-z0-9_]+", text)
    pairs: list[str] = []
    for run in re.findall(r"[一-鿿]+", text):
        pairs += [run[i:i + 2] for i in range(len(run) - 1)] or [run]
    return Counter(words + pairs)


def cosine(a: Counter, b: Counter) -> float:
    dot = sum(a[t] * b[t] for t in set(a) & set(b))
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


def retrieve(question: str, docs: list[TableDoc], k: int = 3) -> list[TableDoc]:
    """Stage 1: rank tables by similarity to the question. A keyword hit forces a core table in,
    because pure similarity sometimes ranks a look-alike table above the one that matters."""
    q = tokens(question)
    low = question.lower()
    scored = []
    for doc in docs:
        score = cosine(q, tokens(doc.text()))
        if any(kw.lower() in low for kw in doc.keywords):
            score += 1.0
        if score > 0:
            scored.append((score, doc.name, doc))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [doc for _, _, doc in scored[:k]]


def build_context(con: sqlite3.Connection, docs: list[TableDoc], question: str, budget: int = 6000) -> str:
    """Stage 2: exact DDL of the chosen tables, then column meanings (most relevant first),
    code meanings, joins and business rules, cut to a character budget."""
    q = tokens(question)
    blocks: list[str] = []
    used = 0
    for doc in docs:
        row = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (doc.name,)).fetchone()
        if row is None:
            continue
        lines = [f"-- table {doc.name}: {doc.description}", row[0].strip()]
        ranked = sorted(doc.columns.items(), key=lambda kv: -cosine(q, tokens(f"{kv[0]} {kv[1]}")))
        lines += [f"--   {col}: {meaning}" for col, meaning in ranked]
        for col, codes in doc.codes.items():
            lines.append(f"--   {col} codes: " + "; ".join(f"{c} = {m}" for c, m in codes.items()))
        lines += [f"--   join: {j}" for j in doc.joins]
        lines += [f"--   rule: {r}" for r in doc.rules]
        block = "\n".join(lines)
        if used + len(block) > budget:
            block = block[: max(0, budget - used)]
        if not block:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def default_knowledge(tenant: str) -> list[TableDoc]:
    """What the team wrote down about each table. Every tenant gets its own copy: one tenant's
    codes and rules must never end up in another tenant's prompt. (In this demo the two tenants
    even disagree on what status ``X`` means.)"""
    x_meaning = "退回仓库" if tenant == "globex" else "已取消"
    return [
        TableDoc(
            name="parcels",
            description="包裹：每行一个包裹，从导入、收货、装车到送达",
            columns={
                "tracking_no": "包裹号（运单号）",
                "customer_code": "货主代码",
                "warehouse_code": "所在仓库代码",
                "status": "包裹状态，见状态码",
                "cartons": "箱数",
                "created_at": "导入日期",
                "delivered_at": "送达日期，未送达为空",
            },
            codes={"status": {"N": "已导入", "R": "已收货（在仓）", "L": "已装车（在途）", "D": "已送达", "X": x_meaning}},
            joins=["parcels.warehouse_code = warehouses.code", "parcels.tracking_no = load_items.tracking_no"],
            rules=["“在途”指状态 R 或 L", "统计送达数量按 delivered_at 的日期范围"],
            keywords=["包裹", "parcel", "送达", "tracking"],
        ),
        TableDoc(
            name="warehouses",
            description="仓库：仓库代码、名称和所在城市",
            columns={"code": "仓库代码", "name": "仓库名称", "city": "所在城市"},
            keywords=["仓库", "城市", "warehouse"],
        ),
        TableDoc(
            name="loads",
            description="装车单：一辆车的一次发车",
            columns={
                "load_no": "装车单号，格式 LD 加 4 位数字",
                "truck_no": "车牌号",
                "warehouse_code": "发车仓库",
                "departed_at": "发车日期",
            },
            joins=["loads.load_no = load_items.load_no"],
            keywords=["装车", "load"],
        ),
        TableDoc(
            name="load_items",
            description="装车明细：哪个包裹在哪张装车单上",
            columns={"load_no": "装车单号", "tracking_no": "包裹号"},
            joins=["load_items.tracking_no = parcels.tracking_no", "load_items.load_no = loads.load_no"],
            keywords=["装车单"],
        ),
    ]

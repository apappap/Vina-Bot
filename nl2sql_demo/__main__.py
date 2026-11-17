"""Command line.

    python -m nl2sql_demo tour                         # scripted walk-through of every feature
    python -m nl2sql_demo ask "上个月送达了多少包裹？"   # one question (tenant acme, offline model)
    python -m nl2sql_demo ask --tenant globex "上个月送达了多少包裹？"
    python -m nl2sql_demo ask --llm claude "哪个仓库的包裹最多？"   # real model, needs `pip install anthropic`
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from . import data
from .feedback import train
from .llm import ClaudeLLM, FakeLLM
from .pipeline import Answer, Pipeline
from .tenancy import Tenant, tenant_scope

# The demo data ends on data.ANCHOR, so "last month" is computed from the day after it.
DEMO_TODAY = data.ANCHOR + timedelta(days=1)

SOURCE = {"template": "已验证模板（没有调用模型）", "llm": "模型生成", "none": "—"}


def render(ans: Answer, preview: int = 5) -> str:
    lines = [f"问题：{ans.question}"]
    if ans.tables:
        lines.append("检索到的表：" + "、".join(ans.tables))
    lines.append("SQL 来源：" + SOURCE[ans.source])
    if ans.sql:
        lines.append("SQL：" + ans.sql)
    if ans.repaired:
        lines.append("（第一次执行报错，已把数据库的报错交给模型修正了一次）")
    if ans.error:
        lines.append("结果：没有回答 —— " + ans.error)
        return "\n".join(lines)
    if ans.truncated and ans.stats:
        lines.append(f"结果超过 {len(ans.rows):,} 行的取数上限，由数据库对全部 {ans.stats['total_rows']:,} 行做了统计：")
        for col, info in ans.stats["columns"].items():
            if "distribution" in info:
                lines.append(f"  {col} 分布：{info['distribution']}")
            elif info["type"] == "number":
                lines.append(f"  {col}：合计 {info['sum']:,}，平均 {info['avg']}")
            elif info["type"] == "date":
                lines.append(f"  {col}：{info['min']} 至 {info['max']}")
        return "\n".join(lines)
    lines.append(f"结果：{len(ans.rows)} 行  {ans.columns}")
    lines += [f"  {row}" for row in ans.rows[:preview]]
    if len(ans.rows) > preview:
        lines.append(f"  …… 另外 {len(ans.rows) - preview} 行")
    return "\n".join(lines)


def make_pipeline(data_dir: Path, llm_name: str, today: date, max_rows: int) -> Pipeline:
    if not all((data_dir / f"{t}.db").exists() for t in data.TENANTS):
        data.build_all(data_dir)
    tenants = {code: Tenant(code, data_dir / f"{code}.db") for code in data.TENANTS}
    llm = ClaudeLLM() if llm_name == "claude" else FakeLLM()
    return Pipeline(tenants, llm, today=today, max_rows=max_rows)


TOUR = [
    ("acme", "上个月送达了多少包裹？", "普通问题：两阶段检索 → 模型写 SQL → 安全检查 → 执行"),
    ("acme", "每个仓库有多少在途包裹？", "用到知识库里的业务规则：在途 = 状态 R 或 L"),
    ("acme", "装车单 LD0003 上有哪些包裹？", "跨表关联：装车明细 + 包裹"),
    ("acme", "各城市的包裹数", "模型第一次写错列名，把数据库报错交回去修一次"),
    ("acme", "列出所有已送达的包裹", "结果超过取数上限：由数据库算全量统计，不拿前 N 行下结论"),
    ("acme", "把取消的包裹都删掉", "模型写出 DELETE：被安全检查拦下"),
    ("acme", "看看 globex 的包裹", "试图读另一个租户的库：被拦下"),
    ("acme", "列出用户和密码", "试图读不在知识库里的表、敏感列：被拦下"),
    ("acme", "哪些货主的包裹最多？", "模型答不了：进入待修清单"),
]


def tour(pipeline: Pipeline) -> None:
    for i, (tenant, question, note) in enumerate(TOUR, 1):
        print(f"\n=== {i}. {note}")
        with tenant_scope(pipeline.tenants[tenant]):
            print(render(pipeline.answer(question, user="alice")))

    print("\n=== 同一个问题，换一个租户问：数据、知识都是 globex 自己的")
    with tenant_scope(pipeline.tenants["globex"]):
        print(render(pipeline.answer("上个月送达了多少包裹？")))

    print("\n=== 另一个用户也问了答不了的那个问题：待修清单按问题合并，按影响人数排序")
    with tenant_scope(pipeline.tenants["acme"]):
        pipeline.answer("哪些货主的包裹最多", user="bob")
    for item in pipeline.queue.open_items():
        print(f"  #{item.id} [{item.reason}] {item.question}  影响 {len(item.users)} 人")

    target = next(i for i in pipeline.queue.open_items() if i.reason == "cannot_generate")
    print(f"\n=== 工程师训练 #{target.id}：先跑通 → 写入 → 按用户原来的路线重问 → 通过才结案")
    result = train(
        pipeline,
        target.id,
        "SELECT customer_code, COUNT(*) AS parcels FROM parcels GROUP BY customer_code ORDER BY parcels DESC",
    )
    print(f"  {result.message}；通知：{', '.join(result.notify) or '—'}")

    print("\n=== 再问一次：直接命中模板，不调用模型")
    with tenant_scope(pipeline.tenants["acme"]):
        print(render(pipeline.answer("哪些货主的包裹最多？")))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m nl2sql_demo")
    parser.add_argument("--data", type=Path, default=Path("demo_data"), help="where the demo databases live")
    parser.add_argument("--llm", choices=["fake", "claude"], default="fake")
    parser.add_argument("--today", type=date.fromisoformat, default=DEMO_TODAY)
    parser.add_argument("--max-rows", type=int, default=10_000)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tour", help="walk through every feature with the demo questions")
    ask = sub.add_parser("ask", help="ask one question")
    ask.add_argument("--tenant", choices=sorted(data.TENANTS), default="acme")
    ask.add_argument("question")
    args = parser.parse_args(argv)

    pipeline = make_pipeline(args.data, args.llm, args.today, args.max_rows)
    if args.command == "tour":
        tour(pipeline)
    else:
        with tenant_scope(pipeline.tenants[args.tenant]):
            print(render(pipeline.answer(args.question)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

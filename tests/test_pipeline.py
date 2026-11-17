from contextlib import closing

from nl2sql_demo.knowledge import build_context, retrieve
from nl2sql_demo.tenancy import connect, tenant_scope


def test_a_question_goes_through_retrieval_the_model_and_the_guard(ask):
    ans = ask("上个月送达了多少包裹？")
    assert ans.error is None and ans.source == "llm"
    assert ans.tables[0] == "parcels"
    assert "LIMIT" in ans.sql
    assert ans.rows[0][0] > 0


def test_the_context_holds_exact_ddl_and_code_meanings(pipeline, tenants):
    question = "装车单 LD0003 上有哪些包裹？"
    docs = retrieve(question, pipeline.knowledge["acme"])
    with tenant_scope(tenants["acme"]), closing(connect()) as con:
        context = build_context(con, docs, question)
    assert {"load_items", "parcels"} <= {d.name for d in docs}
    assert "CREATE TABLE parcels" in context
    assert "D = 已送达" in context


def test_knowledge_is_kept_per_tenant(pipeline):
    meaning = {
        code: next(d for d in docs if d.name == "parcels").codes["status"]["X"]
        for code, docs in pipeline.knowledge.items()
    }
    assert meaning["acme"] != meaning["globex"]


def test_a_fixable_database_error_is_repaired_once(ask):
    ans = ask("各城市的包裹数")
    assert ans.error is None and ans.repaired
    assert "city_name" not in ans.sql


def test_a_result_over_the_limit_gets_statistics_over_every_row(ask, tenants):
    ans = ask("列出所有已送达的包裹")
    assert ans.truncated and len(ans.rows) == 500
    with tenant_scope(tenants["acme"]), closing(connect()) as con:
        total, cartons = con.execute("SELECT COUNT(*), SUM(cartons) FROM parcels WHERE status = 'D'").fetchone()
    assert ans.stats["total_rows"] == total > 500
    assert ans.stats["columns"]["cartons"]["sum"] == cartons


def test_dangerous_sql_is_blocked_and_queued(ask, pipeline):
    for question in ("把取消的包裹都删掉", "看看 globex 的包裹", "列出用户和密码"):
        ans = ask(question)
        assert ans.error and ans.error.startswith("blocked"), question
    queued = {item.question: item.reason for item in pipeline.queue.open_items()}
    assert queued["把取消的包裹都删掉"] == "blocked"


def test_an_unanswerable_question_is_queued(ask, pipeline):
    ans = ask("哪些货主的包裹最多？")
    assert ans.error.startswith("cannot_generate")
    assert [i.question for i in pipeline.queue.open_items()] == ["哪些货主的包裹最多？"]


def test_the_golden_questions_pass_with_the_offline_model(pipeline):
    import json
    from pathlib import Path

    from evals.run_golden import grade

    cases = json.loads((Path(__file__).resolve().parents[1] / "evals" / "golden.json").read_text(encoding="utf-8"))
    results = {case["id"]: grade(pipeline, case) for case in cases}
    assert all(ok for ok, _ in results.values()), results

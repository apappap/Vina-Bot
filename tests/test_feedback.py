from nl2sql_demo.feedback import train

QUESTION = "哪些货主的包裹最多？"
GOOD_SQL = "SELECT customer_code, COUNT(*) AS parcels FROM parcels GROUP BY customer_code ORDER BY parcels DESC"


def test_the_same_question_from_two_people_is_one_item(ask, pipeline):
    ask(QUESTION, user="alice")
    ask("哪些货主的包裹最多", user="bob")  # punctuation differs, same question
    items = pipeline.queue.open_items()
    assert len(items) == 1 and items[0].users == {"alice", "bob"}


def test_training_runs_first_writes_retests_then_notifies(ask, pipeline):
    ask(QUESTION, user="alice")
    ask(QUESTION, user="bob")
    item = pipeline.queue.open_items()[0]

    result = train(pipeline, item.id, GOOD_SQL)

    assert result.ok and result.notify == ["alice", "bob"]
    again = ask(QUESTION)
    assert again.source == "template" and again.error is None
    assert pipeline.queue.open_items() == []


def test_sql_that_does_not_run_is_never_written(ask, pipeline):
    ask(QUESTION)
    item = pipeline.queue.open_items()[0]

    result = train(pipeline, item.id, "SELECT owner FROM parcels")

    assert not result.ok and "does not run" in result.message
    assert len(pipeline.templates_for("acme")) == 0


def test_a_failed_retest_rolls_back(ask, pipeline, monkeypatch):
    ask(QUESTION)
    item = pipeline.queue.open_items()[0]
    store = pipeline.templates_for("acme")
    monkeypatch.setattr(store, "get", lambda question: None)  # the user's path does not reach the template

    result = train(pipeline, item.id, GOOD_SQL)

    assert not result.ok and "rolled back" in result.message
    assert len(store) == 0
    assert pipeline.queue.get(item.id).status == "open"

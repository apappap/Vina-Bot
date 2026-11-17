from __future__ import annotations

import pytest

from nl2sql_demo import data
from nl2sql_demo.__main__ import DEMO_TODAY
from nl2sql_demo.llm import FakeLLM
from nl2sql_demo.pipeline import Pipeline
from nl2sql_demo.tenancy import Tenant, tenant_scope


@pytest.fixture(scope="session")
def dbs(tmp_path_factory):
    folder = tmp_path_factory.mktemp("data")
    return {
        "acme": data.build("acme", folder / "acme.db", parcels=3000),
        "globex": data.build("globex", folder / "globex.db"),
    }


@pytest.fixture
def tenants(dbs):
    return {code: Tenant(code, path) for code, path in dbs.items()}


@pytest.fixture
def pipeline(tenants):
    # A small row limit so "more rows than the limit" is easy to reach in tests.
    return Pipeline(tenants, FakeLLM(), today=DEMO_TODAY, max_rows=500)


@pytest.fixture
def ask(pipeline, tenants):
    def _ask(question: str, tenant: str = "acme", **kwargs):
        with tenant_scope(tenants[tenant]):
            return pipeline.answer(question, **kwargs)

    return _ask

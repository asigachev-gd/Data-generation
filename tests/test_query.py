"""Unit tests for the version-scoped AST query policy."""

from __future__ import annotations

import pytest

from app.query import (
    QueryError,
    QueryPlan,
    parse_query_plan,
    request_query_plan,
    validate_query_plan,
)


def _validate(sql: str):  # type: ignore[no-untyped-def]
    return validate_query_plan(
        QueryPlan(sql=sql, explanation="A safe answer."),
        allowed_tables={"customers", "orders"},
        storage_schema="dg_selected_v1",
    )


def test_valid_select_and_cte_are_capped() -> None:
    result = _validate(
        "WITH totals AS (SELECT customer_id, count(*) AS total FROM orders GROUP BY customer_id) "
        "SELECT customer_id, total FROM totals"
    )
    assert "LIMIT 500" in result.sql
    assert "orders" in result.sql


@pytest.mark.parametrize(
    "sql, message",
    [
        ("SELECT * FROM pg_catalog.pg_tables", "Schema-qualified"),
        ("SELECT * FROM other_table", "outside the selected"),
        ("SELECT pg_sleep(1)", "not allowed"),
        ("SELECT * FROM customers; DELETE FROM customers", "Only one"),
        (
            "WITH changed AS (DELETE FROM customers RETURNING *) SELECT * FROM changed",
            "Data-changing",
        ),
    ],
)
def test_query_policy_rejects_escapes_and_mutations(sql: str, message: str) -> None:
    with pytest.raises(QueryError, match=message):
        _validate(sql)


def test_structured_query_plan_rejects_extra_or_invalid_model_fields() -> None:
    with pytest.raises(QueryError, match="structured query plan"):
        parse_query_plan({"sql": "SELECT 1", "explanation": "x", "unsafe": True})


def test_deterministic_test_double_proposes_a_safe_query() -> None:
    plan, model, metadata = request_query_plan(
        question="List customers.",
        table_mapping={"customers": ["id", "name"]},
        settings=type("Settings", (), {"deterministic_test_mode": True})(),
    )

    assert plan.sql == 'SELECT * FROM "customers" ORDER BY "id"'
    assert model == "deterministic-test-double"
    assert metadata["test_double"] is True
    validate_query_plan(plan, allowed_tables={"customers"}, storage_schema="dg_selected_v1")

"""Unit coverage for bounded edit-plan validation and row regeneration."""

from __future__ import annotations

import pytest

from app.ddl import parse_schema
from app.edits import EditError, apply_edit, parse_edit_plan
from app.generation import generate_dataset, local_generation_profile


def _schema():  # type: ignore[no-untyped-def]
    return parse_schema(
        "CREATE TABLE parent (id integer PRIMARY KEY, label text NOT NULL);"
        "CREATE TABLE child (id integer PRIMARY KEY, parent_id integer NOT NULL "
        "REFERENCES parent(id), note text NOT NULL);"
    )


def test_edit_plan_rejects_key_foreign_key_and_unknown_targets() -> None:
    schema = _schema()
    with pytest.raises(EditError, match="Key and foreign-key"):
        parse_edit_plan(
            schema,
            {
                "target_table": "child",
                "target_columns": ["parent_id"],
                "operation": "regenerate_matching_columns",
                "expected_row_count_effect": 0,
                "explanation": "Change parent links",
            },
            target_table="child",
        )
    with pytest.raises(EditError, match="outside the selected table"):
        parse_edit_plan(
            schema,
            {
                "target_table": "child",
                "target_columns": ["missing"],
                "operation": "regenerate_matching_columns",
                "expected_row_count_effect": 0,
                "explanation": "Change values",
            },
            target_table="child",
        )


def test_edit_plan_rejects_invalid_structured_output_and_row_count_changes() -> None:
    schema = _schema()
    with pytest.raises(EditError, match="structured output"):
        parse_edit_plan(schema, "not JSON", target_table="child")
    with pytest.raises(EditError, match="must not change row counts"):
        parse_edit_plan(
            schema,
            {
                "target_table": "child",
                "target_columns": ["note"],
                "operation": "regenerate_matching_columns",
                "expected_row_count_effect": 1,
                "explanation": "Add a row",
            },
            target_table="child",
        )


def test_scoped_edit_generates_a_valid_new_dataset_without_changing_foreign_keys() -> None:
    schema = _schema()
    original = generate_dataset(
        schema,
        row_counts={"parent": 2, "child": 3},
        seed=9,
        profile=local_generation_profile(schema),
    )
    plan = parse_edit_plan(
        schema,
        {
            "target_table": "child",
            "target_columns": ["note"],
            "operation": "regenerate_matching_columns",
            "scope": {"id": original.rows["child"][0]["id"]},
            "expected_row_count_effect": 0,
            "explanation": "Regenerate one note.",
        },
        target_table="child",
    )
    applied = apply_edit(schema, original.rows, plan, seed=10)
    assert applied.affected_rows == 1
    assert [row["parent_id"] for row in applied.dataset.rows["child"]] == [
        row["parent_id"] for row in original.rows["child"]
    ]
    assert len(applied.dataset.rows["child"]) == 3

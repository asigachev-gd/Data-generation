"""Tests for deterministic, local constraint-safe data generation."""

from datetime import date

import pytest

from app.ddl import parse_schema
from app.generation import (
    GenerationError,
    generate_dataset,
    local_generation_profile,
    parse_generation_profile,
    validate_dataset,
)


def test_seeded_generation_is_deterministic_and_valid() -> None:
    schema = parse_schema(
        "CREATE TABLE users (id integer PRIMARY KEY, email varchar(50) NOT NULL UNIQUE, "
        "active boolean DEFAULT true, born date);"
    )

    first = generate_dataset(
        schema, row_counts={"users": 10}, seed=42, profile=local_generation_profile(schema)
    )
    second = generate_dataset(
        schema, row_counts={"users": 10}, seed=42, profile=local_generation_profile(schema)
    )

    assert first.rows == second.rows
    assert first.report.seed == 42
    assert validate_dataset(schema, first.rows) == ()


def test_defaults_nulls_and_type_bounds_are_enforced() -> None:
    schema = parse_schema(
        "CREATE TABLE things (id integer PRIMARY KEY, label varchar(8) NOT NULL, "
        "occurred_on date NOT NULL, amount numeric(5, 2) CHECK (amount >= 0));"
    )
    dataset = generate_dataset(
        schema, row_counts={"things": 25}, seed=3, profile=local_generation_profile(schema)
    )

    assert all(
        len(row["label"]) <= 8 and isinstance(row["occurred_on"], date)
        for row in dataset.rows["things"]
    )
    assert validate_dataset(schema, dataset.rows) == ()


def test_literal_defaults_are_generated_with_their_declared_type() -> None:
    schema = parse_schema(
        "CREATE TABLE settings (id integer PRIMARY KEY, enabled boolean NOT NULL DEFAULT true, "
        "limit_value numeric(5, 2) NOT NULL DEFAULT 12.50);"
    )
    dataset = generate_dataset(
        schema, row_counts={"settings": 2}, seed=4, profile=local_generation_profile(schema)
    )

    assert [(row["enabled"], row["limit_value"]) for row in dataset.rows["settings"]] == [
        (True, 12.5),
        (True, 12.5),
    ]


def test_parent_child_and_composite_foreign_keys_are_valid() -> None:
    schema = parse_schema(
        "CREATE TABLE parent (tenant integer NOT NULL, id integer NOT NULL, "
        "PRIMARY KEY (tenant, id)); CREATE TABLE child (id integer PRIMARY KEY, "
        "tenant integer NOT NULL, parent_id integer NOT NULL, FOREIGN KEY (tenant, "
        "parent_id) REFERENCES parent (tenant, id));"
    )
    dataset = generate_dataset(
        schema,
        row_counts={"parent": 5, "child": 20},
        seed=8,
        profile=local_generation_profile(schema),
    )

    parent_keys = {(row["tenant"], row["id"]) for row in dataset.rows["parent"]}
    assert {(row["tenant"], row["parent_id"]) for row in dataset.rows["child"]} <= parent_keys
    assert validate_dataset(schema, dataset.rows) == ()


def test_invalid_or_partial_structured_profile_is_rejected() -> None:
    schema = parse_schema("CREATE TABLE users (id integer PRIMARY KEY, email text NOT NULL);")
    with pytest.raises(GenerationError, match="exactly its columns"):
        parse_generation_profile(
            schema,
            {
                "tables": {
                    "users": {
                        "id": {
                            "semantic_category": "identifier",
                            "allow_null": False,
                            "rationale": "id",
                        }
                    }
                }
            },
        )


def test_validator_detects_unique_and_check_violations() -> None:
    schema = parse_schema(
        "CREATE TABLE things (id integer PRIMARY KEY, amount integer CHECK (amount >= 0));"
    )
    errors = validate_dataset(schema, {"things": ({"id": 1, "amount": -1}, {"id": 1, "amount": 2})})

    assert any("CHECK" in error for error in errors)
    assert any("not unique" in error for error in errors)


def test_unsatisfiable_unique_values_respect_the_retry_budget() -> None:
    schema = parse_schema(
        "CREATE TABLE flags (id integer PRIMARY KEY, enabled boolean NOT NULL UNIQUE);"
    )

    with pytest.raises(GenerationError, match="unique row"):
        generate_dataset(
            schema, row_counts={"flags": 3}, seed=1, profile=local_generation_profile(schema)
        )


def test_maximum_scope_seven_table_schema_is_validated() -> None:
    ddl = "CREATE TABLE t1 (id integer PRIMARY KEY);"
    ddl += "".join(
        f"CREATE TABLE t{number} (id integer PRIMARY KEY, parent_id integer NOT NULL "
        f"REFERENCES t{number - 1}(id));"
        for number in range(2, 8)
    )
    schema = parse_schema(ddl)
    counts = {f"t{number}": 3 for number in range(1, 8)}

    dataset = generate_dataset(
        schema, row_counts=counts, seed=12, profile=local_generation_profile(schema)
    )

    assert dataset.report.generated_rows == counts
    assert validate_dataset(schema, dataset.rows) == ()

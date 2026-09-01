"""Validated, bounded edits for immutable generated dataset versions."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.generation import (
    GeneratedDataset,
    GenerationError,
    GenerationProfile,
    GenerationReport,
    generate_dataset,
    local_generation_profile,
    validate_dataset,
)
from app.schema import Schema

EditOperation = Literal[
    "regenerate_matching_columns",
    "change_generator_parameter",
    "change_value_distribution",
]


class EditPlan(BaseModel):
    """The only edit shapes that model output or the UI may request."""

    model_config = ConfigDict(extra="forbid")

    target_table: str
    target_columns: list[str] = Field(min_length=1)
    operation: EditOperation
    scope: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    generator_parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    distribution: dict[str, float] = Field(default_factory=dict)
    expected_row_count_effect: int = 0
    explanation: str = Field(min_length=1, max_length=500)


class EditError(ValueError):
    """An edit is invalid or would require an unsafe relational mutation."""


@dataclass(frozen=True)
class AppliedEdit:
    dataset: GeneratedDataset
    affected_rows: int
    metadata: dict[str, Any]


def edit_plan_json_schema() -> dict[str, Any]:
    """Return the strict JSON schema supplied to Gemini for edit proposals."""

    return EditPlan.model_json_schema()


def parse_edit_plan(
    schema: Schema, payload: str | dict[str, Any], *, target_table: str
) -> EditPlan:
    """Parse model output and validate it against an explicit UI table target."""

    try:
        plan = (
            EditPlan.model_validate_json(payload)
            if isinstance(payload, str)
            else EditPlan.model_validate(payload)
        )
    except ValidationError as error:
        raise EditError("Edit plan is not valid structured output.") from error
    if plan.target_table != target_table:
        raise EditError("Edit plan must use the table explicitly selected in the UI.")
    _validate_plan(schema, plan)
    return plan


def request_edit_plan(
    schema: Schema,
    *,
    target_table: str,
    prompt: str,
    settings: Any | None,
) -> tuple[EditPlan, str | None, dict[str, Any]]:
    """Request a structured proposal; never apply a fallback or free-form edit."""

    metadata = {"prompt_length": len(prompt), "target_table": target_table}
    if settings is None:
        raise EditError(
            "Gemini edit planning is unavailable because application settings are missing."
        )
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=(
                "Propose exactly one safe dataset edit as JSON. The selected target table is "
                f"'{target_table}'. Only use the supplied JSON schema. Do not choose primary, "
                "unique, "
                "or foreign-key columns, and keep expected_row_count_effect at zero. "
                f"User request: {prompt}"
            ),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=edit_plan_json_schema(),
                temperature=0.0,
            ),
        )
        return (
            parse_edit_plan(schema, response.text, target_table=target_table),
            settings.gemini_model,
            metadata,
        )
    except EditError:
        raise
    except Exception as error:
        raise EditError(
            "Gemini could not produce a valid edit plan; no data was changed."
        ) from error


def apply_edit(
    schema: Schema,
    base_rows: dict[str, tuple[dict[str, Any], ...]],
    plan: EditPlan,
    *,
    seed: int | None = None,
) -> AppliedEdit:
    """Create changed rows locally while preserving relationally significant values.

    Key and FK columns are rejected before this point, so no dependent repair is needed.
    Requests that would need such a repair are deliberately refused rather than guessed.
    """

    _validate_plan(schema, plan)
    table = schema.table(plan.target_table)
    assert table is not None  # validated above
    counts = {name: len(rows) for name, rows in base_rows.items()}
    if set(counts) != {item.name for item in schema.tables}:
        raise EditError("The selected version does not contain every table in its schema.")
    profile = _profile_for_plan(schema, plan)
    actual_seed = seed if seed is not None else random.SystemRandom().randrange(0, 2**63)
    try:
        candidate = generate_dataset(schema, row_counts=counts, seed=actual_seed, profile=profile)
    except GenerationError as error:
        raise EditError("The proposed edit cannot satisfy the schema constraints.") from error
    changed = {name: [dict(row) for row in rows] for name, rows in base_rows.items()}
    affected = 0
    for index, row in enumerate(changed[table.name]):
        if _matches_scope(row, plan.scope):
            for column in plan.target_columns:
                row[column] = candidate.rows[table.name][index][column]
            affected += 1
    frozen = {name: tuple(rows) for name, rows in changed.items()}
    errors = validate_dataset(schema, frozen)
    if errors:
        raise EditError("The proposed edit would violate schema validation: " + "; ".join(errors))
    report = GenerationReport(
        requested_rows=counts,
        generated_rows=counts,
        seed=actual_seed,
        used_fallback_profile=True,
        warnings=(),
        validation_errors=(),
        model=None,
        prompt_metadata={"edit": True, "target_table": plan.target_table},
    )
    return AppliedEdit(
        GeneratedDataset(frozen, profile, report),
        affected,
        {
            "operation": plan.operation,
            "target_table": plan.target_table,
            "target_columns": plan.target_columns,
        },
    )


def _validate_plan(schema: Schema, plan: EditPlan) -> None:
    table = schema.table(plan.target_table)
    if table is None:
        raise EditError("Edit plan targets a table outside the active schema.")
    names = {column.name for column in table.columns}
    if (
        len(set(plan.target_columns)) != len(plan.target_columns)
        or not set(plan.target_columns) <= names
    ):
        raise EditError("Edit plan targets a column outside the selected table.")
    protected = set(table.primary_key)
    protected.update(column for key in table.unique_constraints for column in key.columns)
    protected.update(column for key in table.foreign_keys for column in key.columns)
    if protected.intersection(plan.target_columns):
        raise EditError(
            "Key and foreign-key columns cannot be edited; their safe impact is not bounded."
        )
    if not set(plan.scope) <= names:
        raise EditError("Edit scope references a column outside the selected table.")
    if plan.expected_row_count_effect != 0:
        raise EditError("Edits must not change row counts.")
    if plan.operation == "change_generator_parameter":
        if set(plan.generator_parameters) != {"text_prefix"}:
            raise EditError("Only the bounded text_prefix generator parameter is supported.")
        text_types = {"text", "character", "character varying"}
        if any(table.column(name).data_type.name not in text_types for name in plan.target_columns):
            raise EditError("text_prefix may only be used with text columns.")
    if plan.operation == "change_value_distribution":
        if set(plan.distribution) != {"null_probability"}:
            raise EditError("Only a bounded null_probability distribution is supported.")
        probability = plan.distribution["null_probability"]
        if not 0.0 <= probability <= 1.0:
            raise EditError("null_probability must be between 0.0 and 1.0.")
        if probability and any(not table.column(name).nullable for name in plan.target_columns):
            raise EditError("A non-nullable column cannot receive a null distribution.")


def _profile_for_plan(schema: Schema, plan: EditPlan) -> GenerationProfile:
    profile = local_generation_profile(schema).model_copy(deep=True)
    for column in plan.target_columns:
        item = profile.tables[plan.target_table][column]
        if plan.operation == "change_generator_parameter":
            item.parameters.update(plan.generator_parameters)
        elif plan.operation == "change_value_distribution":
            item.parameters["null_probability"] = plan.distribution["null_probability"]
    return profile


def _matches_scope(row: dict[str, Any], scope: dict[str, Any]) -> bool:
    return all(row.get(column) == value for column, value in scope.items())

"""Component-level coverage for the Data Generation UI inputs and service wiring."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.generation import local_generation_profile
from app.ui import decode_ddl_upload, run_generation, validate_generation_inputs


@dataclass
class Upload:
    name: str
    content: bytes

    def getvalue(self) -> bytes:
        return self.content


def test_upload_validation_rejects_missing_extension_and_invalid_encoding() -> None:
    with pytest.raises(ValueError, match="Choose"):
        decode_ddl_upload(None)
    with pytest.raises(ValueError, match="Supported"):
        decode_ddl_upload(Upload("schema.csv", b"CREATE TABLE x (id integer);"))
    with pytest.raises(ValueError, match="UTF-8"):
        decode_ddl_upload(Upload("schema.sql", b"\xff"))


def test_generation_controls_validate_temperature_seed_and_rows() -> None:
    valid = validate_generation_inputs("  names  ", 0.2, 4, {"users": 1_000})
    assert valid.instructions == "names"
    with pytest.raises(ValueError, match="Temperature"):
        validate_generation_inputs("", 1.1, None, {"users": 1})
    with pytest.raises(ValueError, match="Seed"):
        validate_generation_inputs("", 0.2, -1, {"users": 1})
    with pytest.raises(ValueError, match="Rows"):
        validate_generation_inputs("", 0.2, None, {"users": 0})


def test_run_generation_parses_and_persists_uploaded_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = Upload("users.ddl", b"CREATE TABLE users (id integer PRIMARY KEY, name text);")
    inputs = validate_generation_inputs("", 0.2, 7, {"users": 1})
    captured: dict[str, object] = {}

    class Store:
        def persist_dataset(self, schema, dataset):  # type: ignore[no-untyped-def]
            captured["schema"] = schema
            captured["rows"] = dataset.rows
            return "persisted"

    from app import ui

    monkeypatch.setattr(
        ui,
        "generate_dataset",
        lambda schema, **kwargs: __import__(
            "app.generation", fromlist=["generate_dataset"]
        ).generate_dataset(schema, profile=local_generation_profile(schema), **kwargs),
    )
    assert run_generation(upload, inputs, settings=object(), store=Store()) == "persisted"
    assert captured["schema"].tables[0].name == "users"  # type: ignore[union-attr]

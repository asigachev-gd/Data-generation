"""Unit coverage for safe, optional workflow telemetry."""

from __future__ import annotations

from types import SimpleNamespace

import langfuse
from pydantic import SecretStr

from app.observability import Telemetry, redact


class FakeTrace:
    def __init__(self) -> None:
        self.output = None

    def update(self, *, output):  # type: ignore[no-untyped-def]
        self.output = output


class FakeLangfuse:
    def __init__(self) -> None:
        self.metadata = None
        self.trace_instance = FakeTrace()

    def trace(self, *, name, metadata):  # type: ignore[no-untyped-def]
        self.metadata = {"name": name, **metadata}
        return self.trace_instance

    def flush(self) -> None:
        return None


def test_redaction_removes_secrets_and_content_by_default() -> None:
    event = redact(
        {
            "password": "not-for-logs",
            "prompt": "raw request",
            "rows": [{"email": "person@example.test"}],
            "instruction_length": 12,
        }
    )
    assert event == {
        "password": "[REDACTED]",
        "prompt": "[REDACTED]",
        "rows": "[REDACTED]",
        "instruction_length": 12,
    }


def test_disabled_telemetry_is_safe_and_records_no_client() -> None:
    telemetry = Telemetry()
    assert not telemetry.enabled
    with telemetry.workflow("generation", prompt="do not capture") as outcome:
        outcome["dataset_id"] = "dataset-id"
    assert outcome["validation_outcome"] == "success"


def test_enabled_telemetry_initializes_langfuse(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured = {}

    def fake_langfuse(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return FakeLangfuse()

    monkeypatch.setattr(langfuse, "Langfuse", fake_langfuse)
    settings = SimpleNamespace(
        observability_capture_content=False,
        langfuse_enabled=True,
        langfuse_public_key=SecretStr("pk-test"),
        langfuse_secret_key=SecretStr("sk-test"),
        langfuse_host="https://langfuse.test",
    )
    telemetry = Telemetry(settings)
    assert telemetry.enabled
    assert captured == {
        "public_key": "pk-test",
        "secret_key": "sk-test",
        "host": "https://langfuse.test",
    }


def test_trace_records_operational_metadata_without_raw_prompt() -> None:
    telemetry = Telemetry()
    client = FakeLangfuse()
    telemetry.client = client
    with telemetry.workflow("query", prompt="sensitive question", model="gemini-test") as outcome:
        outcome["validation_outcome"] = "success"
        outcome["result_row_count"] = 3
    assert client.metadata["name"] == "query"
    assert client.metadata["prompt"] == "[REDACTED]"
    assert client.trace_instance.output["result_row_count"] == 3
    assert client.trace_instance.output["validation_outcome"] == "success"

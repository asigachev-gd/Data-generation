import pytest
from pydantic import ValidationError

from app.config import Settings


def environment() -> dict[str, str]:
    return {
        "GOOGLE_CLOUD_PROJECT": "demo-project",
        "VERTEX_AI_ENABLED": "true",
        "POSTGRES_DB": "data_generation",
        "POSTGRES_USER": "data_generation",
        "POSTGRES_PASSWORD": "not-a-real-secret",
    }


def test_required_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment().items():
        monkeypatch.setenv(name, value)

    settings = Settings()

    assert settings.postgres_port == 5432
    assert settings.vertex_ai_enabled is True
    assert "not-a-real-secret" not in repr(settings)


def test_missing_required_setting_does_not_expose_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("POSTGRES_PASSWORD")

    with pytest.raises(ValidationError) as error:
        Settings()

    assert "postgres_password" in str(error.value)
    assert "not-a-real-secret" not in str(error.value)


def test_malformed_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("POSTGRES_PORT", "not-a-port")

    with pytest.raises(ValidationError, match="postgres_port"):
        Settings()

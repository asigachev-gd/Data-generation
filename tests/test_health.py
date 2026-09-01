from app.config import Settings
from app.health import database_is_ready


def test_database_readiness_returns_false_when_database_is_unavailable() -> None:
    settings = Settings(
        google_cloud_project="demo-project",
        vertex_ai_enabled=True,
        postgres_host="127.0.0.1",
        postgres_port=1,
        postgres_db="data_generation",
        postgres_user="data_generation",
        postgres_password="not-a-real-secret",
    )

    assert database_is_ready(settings) is False

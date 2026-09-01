"""Readiness checks used by the UI and container health check."""

import argparse
from dataclasses import dataclass

import psycopg
from pydantic import ValidationError

from app.config import Settings, get_settings


@dataclass(frozen=True)
class HealthStatus:
    app_ready: bool
    database_ready: bool
    detail: str


def database_is_ready(settings: Settings) -> bool:
    """Make a short, read-only database connectivity check."""
    try:
        with psycopg.connect(settings.database_dsn, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
    except psycopg.Error:
        return False


def get_health_status() -> HealthStatus:
    """Separate invalid application configuration from database availability."""
    try:
        settings = get_settings()
    except ValidationError:
        return HealthStatus(False, False, "Application configuration is invalid.")

    ready = database_is_ready(settings)
    detail = "Ready." if ready else "Database is unavailable."
    return HealthStatus(True, ready, detail)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check application and database readiness.")
    parser.add_argument(
        "--check", action="store_true", help="Exit non-zero unless both checks pass."
    )
    parser.parse_args()
    status = get_health_status()
    print(status.detail)
    return 0 if status.app_ready and status.database_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())

import pytest


@pytest.mark.integration
def test_postgres_container_fixture_starts_database() -> None:
    """Requires Docker; keeps integration tests independent of local PostgreSQL."""
    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16.8-bookworm") as postgres:
        connection = postgres.get_connection_url()
        assert connection.startswith("postgresql")

"""Verification fixtures remain inside the documented PostgreSQL DDL subset."""

from pathlib import Path

from app.ddl import parse_schema


def test_postgresql_library_sample_fixture_is_supported_and_related() -> None:
    source = (Path(__file__).parent / "fixtures" / "library_mgm_postgresql.sql").read_text()

    schema = parse_schema(source)

    assert [table.name for table in schema.tables] == ["authors", "publishers", "books"]
    assert {foreign_key.referenced_table for foreign_key in schema.table("books").foreign_keys} == {
        "authors",
        "publishers",
    }

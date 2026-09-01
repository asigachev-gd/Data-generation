"""PostgreSQL integration coverage for immutable dataset persistence and exports."""

from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest

from app.ddl import parse_schema
from app.generation import generate_dataset, local_generation_profile
from app.persistence import DatasetStore, PersistenceError


@pytest.fixture
def postgres_dsn() -> str:
    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16.8-bookworm") as postgres:
        yield postgres.get_connection_url().replace("+psycopg2", "")


@pytest.mark.integration
def test_persists_active_version_and_exports_tables(postgres_dsn: str) -> None:
    schema = parse_schema(
        "CREATE TABLE parent (id integer PRIMARY KEY, note text NOT NULL);"
        "CREATE TABLE child (id integer PRIMARY KEY, parent_id integer NOT NULL "
        "REFERENCES parent(id), label text NOT NULL);"
    )
    generated = generate_dataset(
        schema,
        row_counts={"parent": 2, "child": 3},
        seed=4,
        profile=local_generation_profile(schema),
    )
    # Exercise RFC 4180 escaping separately from the deterministic generator output.
    parent_rows = list(generated.rows["parent"])
    parent_rows[0] = {**parent_rows[0], "note": 'quoted, "value"'}
    generated = generated.__class__(
        {**generated.rows, "parent": tuple(parent_rows)}, generated.profile, generated.report
    )

    store = DatasetStore(postgres_dsn)
    stored = store.persist_dataset(schema, generated, name="integration dataset")

    active = store.get_version(stored.dataset_id)
    assert active.version_id == stored.version_id
    assert active.active is True
    assert len(store.table_rows(stored.dataset_id, "child")) == 3

    next_version = store.persist_dataset(schema, generated, dataset_id=stored.dataset_id)
    assert next_version.version_number == 2
    assert store.get_version(stored.dataset_id).version_id == next_version.version_id

    csv_rows = list(
        csv.DictReader(io.StringIO(store.export_csv(stored.dataset_id, "parent").decode()))
    )
    assert csv_rows[0]["note"] == 'quoted, "value"'

    archive = zipfile.ZipFile(io.BytesIO(store.export_zip(stored.dataset_id)))
    assert set(archive.namelist()) == {"parent.csv", "child.csv", "manifest.json"}
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["dataset_id"] == str(stored.dataset_id)
    assert manifest["version_id"] == str(next_version.version_id)


@pytest.mark.integration
def test_invalid_dataset_is_not_persisted(postgres_dsn: str) -> None:
    schema = parse_schema("CREATE TABLE records (id integer PRIMARY KEY, label text NOT NULL);")
    generated = generate_dataset(
        schema, row_counts={"records": 1}, seed=2, profile=local_generation_profile(schema)
    )
    invalid = generated.__class__(
        {"records": ({"id": 1, "label": None},)}, generated.profile, generated.report
    )
    store = DatasetStore(postgres_dsn)

    with pytest.raises(PersistenceError, match="pre-persistence validation"):
        store.persist_dataset(schema, invalid)

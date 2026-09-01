"""Transactional PostgreSQL storage, version retrieval, and safe dataset exports."""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.ddl import parse_schema
from app.generation import GeneratedDataset, validate_dataset
from app.observability import get_telemetry
from app.schema import Column, ForeignKey, Schema, Table

METADATA_SCHEMA = "data_generation"
QUERY_ROLE = "data_generation_query"
_SAFE_CHECK = re.compile(
    r'^\s*"?([A-Za-z_][\w$]*)"?\s*(<=|>=|<>|!=|=|<|>)\s*(-?\d+(?:\.\d+)?)\s*$'
    r'|^\s*"?([A-Za-z_][\w$]*)"?\s+BETWEEN\s+(-?\d+)\s+AND\s+(-?\d+)\s*$',
    re.IGNORECASE,
)


class PersistenceError(RuntimeError):
    """A dataset could not be made into an active, queryable version."""


@dataclass(frozen=True)
class PersistedVersion:
    dataset_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    storage_schema: str
    active: bool


@dataclass(frozen=True)
class DatasetVersion:
    dataset_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    storage_schema: str
    status: str
    active: bool
    schema: dict[str, Any]
    report: dict[str, Any]
    original_ddl: str
    parent_version_id: uuid.UUID | None


@dataclass(frozen=True)
class QueryableVersion:
    dataset_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    active: bool


class DatasetStore:
    """Persist immutable validated datasets using a caller-owned PostgreSQL DSN."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def initialize(self) -> None:
        """Create the application-owned metadata namespace if it is absent."""

        with psycopg.connect(self.dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {} ").format(sql.Identifier(METADATA_SCHEMA))
            )
            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.datasets (
                        id uuid PRIMARY KEY,
                        name text NOT NULL,
                        original_ddl text NOT NULL,
                        schema_model jsonb NOT NULL,
                        active_version_id uuid NULL,
                        created_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                ).format(sql.Identifier(METADATA_SCHEMA))
            )
            # The application login can assume this NOLOGIN role only for a query transaction.
            # A deployment account without CREATEROLE can still generate data, but querying fails
            # closed rather than silently running with its broader privileges.
            try:
                cursor.execute(
                    sql.SQL(
                        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {}) "
                        "THEN CREATE ROLE {} NOLOGIN NOINHERIT; END IF; END $$"
                    ).format(sql.Literal(QUERY_ROLE), sql.Identifier(QUERY_ROLE))
                )
                cursor.execute(
                    sql.SQL("GRANT {} TO CURRENT_USER").format(sql.Identifier(QUERY_ROLE))
                )
            except psycopg.Error as error:
                connection.rollback()
                # Metadata setup below must not continue in an aborted transaction. Reopen it on
                # the next initialize call; deployments lacking role administration fail closed
                # in the safe query layer.
                raise PersistenceError(
                    "Database user cannot configure the required read-only query role."
                ) from error
            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.dataset_versions (
                        id uuid PRIMARY KEY,
                        dataset_id uuid NOT NULL REFERENCES {}.datasets(id),
                        version_number integer NOT NULL,
                        storage_schema text NOT NULL UNIQUE,
                        status text NOT NULL CHECK (status IN ('pending', 'active', 'failed')),
                        generation_report jsonb NOT NULL,
                        failure_reason text NULL,
                        parent_version_id uuid NULL,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        UNIQUE (dataset_id, version_number)
                    )
                    """
                ).format(
                    sql.Identifier(METADATA_SCHEMA),
                    sql.Identifier(METADATA_SCHEMA),
                )
            )
            cursor.execute(
                sql.SQL(
                    "ALTER TABLE {}.dataset_versions "
                    "ADD COLUMN IF NOT EXISTS parent_version_id uuid NULL "
                    "REFERENCES {}.dataset_versions(id)"
                ).format(sql.Identifier(METADATA_SCHEMA), sql.Identifier(METADATA_SCHEMA))
            )
            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.table_versions (
                        version_id uuid NOT NULL REFERENCES {}.dataset_versions(id),
                        logical_name text NOT NULL,
                        physical_name text NOT NULL,
                        row_count integer NOT NULL,
                        PRIMARY KEY (version_id, logical_name)
                    )
                    """
                ).format(sql.Identifier(METADATA_SCHEMA), sql.Identifier(METADATA_SCHEMA))
            )
            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.generation_requests (
                        id uuid PRIMARY KEY,
                        version_id uuid NOT NULL REFERENCES {}.dataset_versions(id),
                        request_kind text NOT NULL,
                        request_metadata jsonb NOT NULL,
                        validation_report jsonb NOT NULL,
                        created_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                ).format(sql.Identifier(METADATA_SCHEMA), sql.Identifier(METADATA_SCHEMA))
            )
            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {}.export_audit (
                        id uuid PRIMARY KEY,
                        version_id uuid NOT NULL REFERENCES {}.dataset_versions(id),
                        export_kind text NOT NULL,
                        table_name text NULL,
                        created_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                ).format(sql.Identifier(METADATA_SCHEMA), sql.Identifier(METADATA_SCHEMA))
            )

    def persist_dataset(
        self,
        schema: Schema,
        dataset: GeneratedDataset,
        *,
        name: str = "Generated dataset",
        dataset_id: uuid.UUID | str | None = None,
        request_kind: str = "generation",
        request_metadata: dict[str, Any] | None = None,
        parent_version_id: uuid.UUID | str | None = None,
    ) -> PersistedVersion:
        """Atomically materialize a dataset version and make it the active version."""

        validation_errors = validate_dataset(schema, dataset.rows)
        if validation_errors:
            raise PersistenceError(
                "Dataset failed pre-persistence validation: " + "; ".join(validation_errors)
            )
        self.initialize()
        existing_dataset = dataset_id is not None
        dataset_id = uuid.UUID(str(dataset_id)) if dataset_id is not None else uuid.uuid4()
        version_number = self._next_version_number(dataset_id) if existing_dataset else 1
        version_id = uuid.uuid4()
        storage_schema = f"dg_{dataset_id.hex[:12]}_v{version_number}"
        try:
            with psycopg.connect(self.dsn) as connection, connection.cursor() as cursor:
                if not existing_dataset:
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {}.datasets (id, name, original_ddl, schema_model) "
                            "VALUES (%s, %s, %s, %s)"
                        ).format(sql.Identifier(METADATA_SCHEMA)),
                        (dataset_id, name, schema.original_ddl, Jsonb(_schema_metadata(schema))),
                    )
                cursor.execute(
                    sql.SQL("""INSERT INTO {}.dataset_versions
                    (id, dataset_id, version_number, storage_schema, status, generation_report,
                    parent_version_id)
                    VALUES (%s, %s, %s, %s, 'pending', %s, %s)""").format(
                        sql.Identifier(METADATA_SCHEMA)
                    ),
                    (
                        version_id,
                        dataset_id,
                        version_number,
                        storage_schema,
                        _jsonb(asdict(dataset.report)),
                        uuid.UUID(str(parent_version_id)) if parent_version_id else None,
                    ),
                )
                cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(storage_schema)))
                for table in schema.tables:
                    self._create_table(cursor, storage_schema, table)
                for table in schema.tables:
                    self._insert_rows(cursor, storage_schema, table, dataset.rows[table.name])
                # Adding FKs after loading permits the validated nullable/deferred cycle strategy,
                # while PostgreSQL validates every existing row when each constraint is added.
                for table in schema.tables:
                    for index, foreign_key in enumerate(table.foreign_keys):
                        self._add_foreign_key(cursor, storage_schema, table, foreign_key, index)
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {}.table_versions "
                            "(version_id, logical_name, physical_name, row_count) "
                            "VALUES (%s, %s, %s, %s)"
                        ).format(sql.Identifier(METADATA_SCHEMA)),
                        (version_id, table.name, table.name, len(dataset.rows[table.name])),
                    )
                cursor.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                        sql.Identifier(storage_schema), sql.Identifier(QUERY_ROLE)
                    )
                )
                cursor.execute(
                    sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                        sql.Identifier(storage_schema), sql.Identifier(QUERY_ROLE)
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "UPDATE {}.dataset_versions SET status = 'active' WHERE id = %s"
                    ).format(sql.Identifier(METADATA_SCHEMA)),
                    (version_id,),
                )
                cursor.execute(
                    sql.SQL("UPDATE {}.datasets SET active_version_id = %s WHERE id = %s").format(
                        sql.Identifier(METADATA_SCHEMA)
                    ),
                    (version_id, dataset_id),
                )
                cursor.execute(
                    sql.SQL("""INSERT INTO {}.generation_requests
                        (id, version_id, request_kind, request_metadata, validation_report)
                    VALUES (%s, %s, %s, %s, %s)""").format(sql.Identifier(METADATA_SCHEMA)),
                    (
                        uuid.uuid4(),
                        version_id,
                        request_kind,
                        Jsonb(
                            request_metadata
                            if request_metadata is not None
                            else dataset.report.prompt_metadata or {}
                        ),
                        Jsonb({"valid": True, "errors": []}),
                    ),
                )
        except psycopg.Error as error:
            self._record_failed_version(
                dataset_id,
                version_id,
                version_number,
                storage_schema,
                schema,
                dataset,
                name,
                existing_dataset,
            )
            raise PersistenceError(
                "PostgreSQL rejected dataset version; no version was activated."
            ) from error
        return PersistedVersion(dataset_id, version_id, version_number, storage_schema, True)

    def get_version(
        self, dataset_id: uuid.UUID | str, version_id: uuid.UUID | str | None = None
    ) -> DatasetVersion:
        """Retrieve an explicit version, or only the selected dataset's active version."""

        dataset_id = uuid.UUID(str(dataset_id))
        with (
            psycopg.connect(self.dsn, row_factory=dict_row) as connection,
            connection.cursor() as cursor,
        ):
            if version_id is None:
                cursor.execute(
                    sql.SQL("""SELECT d.id AS dataset_id, v.id AS version_id, v.version_number,
                    v.storage_schema, v.status, d.active_version_id = v.id AS active,
                    d.schema_model, v.generation_report, d.original_ddl, v.parent_version_id
                    FROM {}.datasets d JOIN {}.dataset_versions v ON v.id = d.active_version_id
                    WHERE d.id = %s""").format(
                        sql.Identifier(METADATA_SCHEMA), sql.Identifier(METADATA_SCHEMA)
                    ),
                    (dataset_id,),
                )
            else:
                cursor.execute(
                    sql.SQL("""SELECT d.id AS dataset_id, v.id AS version_id, v.version_number,
                    v.storage_schema, v.status, d.active_version_id = v.id AS active,
                    d.schema_model, v.generation_report, d.original_ddl, v.parent_version_id
                    FROM {}.datasets d JOIN {}.dataset_versions v ON v.dataset_id = d.id
                    WHERE d.id = %s AND v.id = %s""").format(
                        sql.Identifier(METADATA_SCHEMA), sql.Identifier(METADATA_SCHEMA)
                    ),
                    (dataset_id, uuid.UUID(str(version_id))),
                )
            item = cursor.fetchone()
        if item is None:
            raise PersistenceError("Dataset or requested version was not found.")
        return DatasetVersion(
            item["dataset_id"],
            item["version_id"],
            item["version_number"],
            item["storage_schema"],
            item["status"],
            item["active"],
            item["schema_model"],
            item["generation_report"],
            item["original_ddl"],
            item["parent_version_id"],
        )

    def schema_for_version(
        self, dataset_id: uuid.UUID | str, version_id: uuid.UUID | str | None = None
    ) -> Schema:
        """Rebuild the trusted canonical model retained with the selected dataset."""

        return parse_schema(self.get_version(dataset_id, version_id).original_ddl)

    def queryable_versions(self) -> list[QueryableVersion]:
        """List active or historical versions users may explicitly select for querying."""

        with (
            psycopg.connect(self.dsn, row_factory=dict_row) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                sql.SQL(
                    """SELECT v.dataset_id, v.id AS version_id, v.version_number,
                    d.active_version_id = v.id AS active
                    FROM {}.dataset_versions v JOIN {}.datasets d ON d.id = v.dataset_id
                    WHERE v.status = 'active'
                    ORDER BY d.created_at DESC, v.version_number DESC"""
                ).format(sql.Identifier(METADATA_SCHEMA), sql.Identifier(METADATA_SCHEMA))
            )
            return [
                QueryableVersion(
                    row["dataset_id"], row["version_id"], row["version_number"], row["active"]
                )
                for row in cursor.fetchall()
            ]

    def table_rows(
        self,
        dataset_id: uuid.UUID | str,
        table_name: str,
        *,
        version_id: uuid.UUID | str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return a bounded preview from one table in the selected dataset version."""

        if offset < 0 or not 1 <= limit <= 1_000:
            raise PersistenceError(
                "Preview offset must be non-negative and limit must be 1 through 1000."
            )
        version = self.get_version(dataset_id, version_id)
        self._verify_table(version, table_name)
        with (
            psycopg.connect(self.dsn, row_factory=dict_row) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                sql.SQL("SELECT * FROM {}.{} OFFSET %s LIMIT %s").format(
                    sql.Identifier(version.storage_schema), sql.Identifier(table_name)
                ),
                (offset, limit),
            )
            return list(cursor.fetchall())

    def export_csv(
        self,
        dataset_id: uuid.UUID | str,
        table_name: str,
        *,
        version_id: uuid.UUID | str | None = None,
    ) -> bytes:
        """Create a UTF-8 CSV for a selected table and audit the export."""

        with get_telemetry().workflow(
            "export", export_kind="csv", table_name=table_name
        ) as outcome:
            version = self.get_version(dataset_id, version_id)
            self._verify_table(version, table_name)
            output = io.StringIO(newline="")
            with (
                psycopg.connect(self.dsn, row_factory=dict_row) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    sql.SQL("SELECT * FROM {}.{}").format(
                        sql.Identifier(version.storage_schema), sql.Identifier(table_name)
                    )
                )
                rows = cursor.fetchall()
                writer = csv.DictWriter(
                    output,
                    fieldnames=[item.name for item in cursor.description],
                    lineterminator="\n",
                )
                writer.writeheader()
                writer.writerows(rows)
            self._audit_export(version.version_id, "csv", table_name)
            result = output.getvalue().encode("utf-8")
            outcome.update(
                dataset_id=str(version.dataset_id),
                version_id=str(version.version_id),
                bytes=len(result),
            )
            return result

    def export_zip(
        self, dataset_id: uuid.UUID | str, *, version_id: uuid.UUID | str | None = None
    ) -> bytes:
        """Create a ZIP with all CSV tables and a manifest for the selected version."""

        with get_telemetry().workflow("export", export_kind="zip") as outcome:
            version = self.get_version(dataset_id, version_id)
            table_names = self._table_names(version)
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for table_name in table_names:
                    archive.writestr(
                        f"{table_name}.csv",
                        self.export_csv(dataset_id, table_name, version_id=version.version_id),
                    )
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        {
                            "dataset_id": str(version.dataset_id),
                            "version_id": str(version.version_id),
                            "version_number": version.version_number,
                            "tables": table_names,
                            "schema": version.schema,
                        },
                        default=_jsonable,
                        sort_keys=True,
                        indent=2,
                    ),
                )
            self._audit_export(version.version_id, "zip", None)
            result = output.getvalue()
            outcome.update(
                dataset_id=str(version.dataset_id),
                version_id=str(version.version_id),
                bytes=len(result),
            )
            return result

    def _create_table(self, cursor: psycopg.Cursor[Any], storage_schema: str, table: Table) -> None:
        definitions = [
            sql.SQL("{} {}{}").format(
                sql.Identifier(column.name),
                sql.SQL(_type_sql(column)),
                sql.SQL(" NOT NULL") if not column.nullable else sql.SQL(""),
            )
            for column in table.columns
        ]
        if table.primary_key:
            definitions.append(sql.SQL("PRIMARY KEY ({})").format(_identifiers(table.primary_key)))
        definitions.extend(
            sql.SQL("UNIQUE ({})").format(_identifiers(item.columns))
            for item in table.unique_constraints
        )
        for check in (
            *table.checks,
            *(check for column in table.columns for check in column.checks),
        ):
            rendered = _safe_check(check)
            if rendered is None:
                raise PersistenceError(
                    "Dataset has a CHECK constraint that cannot be safely materialized."
                )
            definitions.append(sql.SQL("CHECK ({})").format(sql.SQL(rendered)))
        cursor.execute(
            sql.SQL("CREATE TABLE {}.{} ({})").format(
                sql.Identifier(storage_schema),
                sql.Identifier(table.name),
                sql.SQL(", ").join(definitions),
            )
        )

    def _insert_rows(
        self,
        cursor: psycopg.Cursor[Any],
        storage_schema: str,
        table: Table,
        rows: tuple[dict[str, Any], ...],
    ) -> None:
        if not rows:
            return
        names = tuple(column.name for column in table.columns)
        statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
            sql.Identifier(storage_schema),
            sql.Identifier(table.name),
            _identifiers(names),
            sql.SQL(", ").join(sql.Placeholder() for _ in names),
        )
        cursor.executemany(statement, [tuple(row[name] for name in names) for row in rows])

    def _add_foreign_key(
        self,
        cursor: psycopg.Cursor[Any],
        storage_schema: str,
        table: Table,
        foreign_key: ForeignKey,
        index: int,
    ) -> None:
        constraint = foreign_key.name or f"fk_{table.name}_{index}"
        suffix = sql.SQL(" DEFERRABLE") if foreign_key.deferrable else sql.SQL("")
        if foreign_key.initially_deferred:
            suffix += sql.SQL(" INITIALLY DEFERRED")
        cursor.execute(
            sql.SQL(
                "ALTER TABLE {}.{} ADD CONSTRAINT {} FOREIGN KEY ({}) "
                "REFERENCES {}.{} ({}) ON UPDATE {} ON DELETE {}{}"
            ).format(
                sql.Identifier(storage_schema),
                sql.Identifier(table.name),
                sql.Identifier(constraint),
                _identifiers(foreign_key.columns),
                sql.Identifier(storage_schema),
                sql.Identifier(foreign_key.referenced_table),
                _identifiers(foreign_key.referenced_columns),
                sql.SQL(foreign_key.on_update),
                sql.SQL(foreign_key.on_delete),
                suffix,
            )
        )

    def _verify_table(self, version: DatasetVersion, table_name: str) -> None:
        if table_name not in self._table_names(version):
            raise PersistenceError("Table is not part of the selected dataset version.")

    def _table_names(self, version: DatasetVersion) -> list[str]:
        with psycopg.connect(self.dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "SELECT logical_name FROM {}.table_versions "
                    "WHERE version_id = %s ORDER BY logical_name"
                ).format(sql.Identifier(METADATA_SCHEMA)),
                (version.version_id,),
            )
            return [item[0] for item in cursor.fetchall()]

    def _audit_export(self, version_id: uuid.UUID, kind: str, table_name: str | None) -> None:
        with psycopg.connect(self.dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "INSERT INTO {}.export_audit (id, version_id, export_kind, table_name) "
                    "VALUES (%s, %s, %s, %s)"
                ).format(sql.Identifier(METADATA_SCHEMA)),
                (uuid.uuid4(), version_id, kind, table_name),
            )

    def _next_version_number(self, dataset_id: uuid.UUID) -> int:
        with psycopg.connect(self.dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "SELECT COALESCE(MAX(version_number), 0) + 1 "
                    "FROM {}.dataset_versions WHERE dataset_id = %s"
                ).format(sql.Identifier(METADATA_SCHEMA)),
                (dataset_id,),
            )
            version_number = cursor.fetchone()[0]
        if version_number == 1:
            raise PersistenceError("Cannot create a version for an unknown dataset.")
        return version_number

    def _record_failed_version(
        self,
        dataset_id: uuid.UUID,
        version_id: uuid.UUID,
        version_number: int,
        storage_schema: str,
        schema: Schema,
        dataset: GeneratedDataset,
        name: str,
        existing_dataset: bool,
    ) -> None:
        """Keep diagnostic metadata while preserving the all-or-nothing data transaction."""

        try:
            with psycopg.connect(self.dsn) as connection, connection.cursor() as cursor:
                if not existing_dataset:
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {}.datasets (id, name, original_ddl, schema_model) "
                            "VALUES (%s, %s, %s, %s)"
                        ).format(sql.Identifier(METADATA_SCHEMA)),
                        (dataset_id, name, schema.original_ddl, Jsonb(_schema_metadata(schema))),
                    )
                cursor.execute(
                    sql.SQL("""INSERT INTO {}.dataset_versions
                    (id, dataset_id, version_number, storage_schema, status,
                    generation_report, failure_reason)
                    VALUES (%s, %s, %s, %s, 'failed', %s, %s)""").format(
                        sql.Identifier(METADATA_SCHEMA)
                    ),
                    (
                        version_id,
                        dataset_id,
                        version_number,
                        storage_schema,
                        _jsonb(asdict(dataset.report)),
                        "Database constraint validation failed.",
                    ),
                )
        except psycopg.Error:
            # A database outage can prevent diagnostics; retain the original failure instead.
            return


def _identifiers(names: tuple[str, ...]) -> sql.Composed:
    return sql.SQL(", ").join(sql.Identifier(name) for name in names)


def _type_sql(column: Column) -> str:
    # Types originate only from app.ddl's fixed normalized scalar allowlist.
    return column.data_type.display_name


def _safe_check(check: str) -> str | None:
    """Emit only the numeric check forms evaluated locally; never replay arbitrary DDL."""

    expression = check.strip().strip("()")
    return expression if _SAFE_CHECK.fullmatch(expression) else None


def _schema_metadata(schema: Schema) -> dict[str, Any]:
    return {
        "cycle_strategy": schema.cycle_strategy,
        "tables": [
            {
                "name": table.name,
                "columns": [
                    {
                        "name": column.name,
                        "type": column.data_type.display_name,
                        "nullable": column.nullable,
                    }
                    for column in table.columns
                ],
                "primary_key": list(table.primary_key),
                "unique_constraints": [list(item.columns) for item in table.unique_constraints],
                "foreign_keys": [
                    {
                        "columns": list(item.columns),
                        "referenced_table": item.referenced_table,
                        "referenced_columns": list(item.referenced_columns),
                    }
                    for item in table.foreign_keys
                ],
            }
            for table in schema.tables
        ],
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _jsonb(value: Any) -> Jsonb:
    return Jsonb(value, dumps=lambda item: json.dumps(item, default=_jsonable))

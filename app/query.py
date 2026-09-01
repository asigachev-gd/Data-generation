"""Structured natural-language query planning and a deliberately narrow SQL executor."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import psycopg
from pglast import parse_sql
from pglast.parser import ParseError
from pglast.stream import RawStream
from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_QUERY_ROWS = 500
MAX_QUERY_BYTES = 1_000_000
QUERY_ROLE = "data_generation_query"
SAFE_FUNCTIONS = {
    "abs",
    "avg",
    "coalesce",
    "count",
    "date_trunc",
    "extract",
    "lower",
    "max",
    "min",
    "round",
    "sum",
    "upper",
}


class QueryError(ValueError):
    """A question, model response, or SQL statement does not meet the query policy."""


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["bar", "line", "scatter"]
    x_column: str = Field(min_length=1)
    y_column: str = Field(min_length=1)
    title: str = Field(default="Query result", min_length=1, max_length=160)


class QueryPlan(BaseModel):
    """The only model response accepted before a query can run."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=20_000)
    explanation: str = Field(min_length=1, max_length=1_000)
    chart: ChartSpec | None = None


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    explanation: str
    chart: ChartSpec | None


@dataclass(frozen=True)
class QueryResult:
    rows: list[dict[str, Any]]
    sql: str
    explanation: str
    chart: ChartSpec | None


def query_plan_json_schema() -> dict[str, Any]:
    return QueryPlan.model_json_schema()


def parse_query_plan(payload: str | dict[str, Any]) -> QueryPlan:
    try:
        return (
            QueryPlan.model_validate_json(payload)
            if isinstance(payload, str)
            else QueryPlan.model_validate(payload)
        )
    except ValidationError as error:
        raise QueryError("Gemini did not return a valid structured query plan.") from error


def validate_query_plan(
    plan: QueryPlan, *, allowed_tables: set[str], storage_schema: str
) -> ValidatedQuery:
    """Validate one PostgreSQL SELECT AST and apply a non-bypassable result cap."""

    sql_text = plan.sql.strip().rstrip(";").strip()
    try:
        statements = parse_sql(sql_text)
    except ParseError as error:
        raise QueryError("Generated SQL is not valid PostgreSQL.") from error
    if len(statements) != 1 or type(statements[0].stmt).__name__ != "SelectStmt":
        raise QueryError("Only one SELECT or WITH ... SELECT statement is allowed.")
    statement = statements[0].stmt
    if getattr(statement, "intoClause", None) is not None or getattr(
        statement, "lockingClause", None
    ):
        raise QueryError("SELECT INTO and locking clauses are not allowed.")
    cte_names = _cte_names(statement)
    for node in _walk(statement):
        kind = type(node).__name__
        if kind in {"InsertStmt", "UpdateStmt", "DeleteStmt", "MergeStmt", "CopyStmt", "CallStmt"}:
            raise QueryError("Data-changing statements are not allowed, including inside WITH.")
        if kind == "SelectStmt" and (
            getattr(node, "intoClause", None) is not None or getattr(node, "lockingClause", None)
        ):
            raise QueryError("SELECT INTO and locking clauses are not allowed.")
        if kind == "RangeVar":
            _validate_relation(node, allowed_tables, cte_names)
        elif kind == "FuncCall":
            _validate_function(node)
        elif kind == "ParamRef":
            raise QueryError("Generated SQL must not contain unresolved parameters.")
    canonical = RawStream()(statement).rstrip(";")
    # A wrapping SELECT is safer than trusting a model-supplied LIMIT and applies to every shape.
    bounded = f"SELECT * FROM ({canonical}) AS query_result LIMIT {MAX_QUERY_ROWS}"
    return ValidatedQuery(bounded, plan.explanation, plan.chart)


def request_query_plan(
    *, question: str, table_mapping: dict[str, list[str]], settings: Any
) -> tuple[QueryPlan, str, dict[str, Any]]:
    """Ask Gemini for constrained JSON; SQL still undergoes local AST validation."""

    if not question.strip():
        raise QueryError("Ask a question about the selected dataset first.")
    if getattr(settings, "deterministic_test_mode", False):
        table_name, columns = next(iter(table_mapping.items()))
        # Table names originate from the validated schema; quoting keeps the test double
        # correct for otherwise valid mixed-case identifiers.
        escaped_table = table_name.replace('"', '""')
        escaped_column = columns[0].replace('"', '""')
        payload = {
            "sql": f'SELECT * FROM "{escaped_table}" ORDER BY "{escaped_column}"',
            "explanation": "Deterministic browser-test query result.",
        }
        return (
            parse_query_plan(payload),
            "deterministic-test-double",
            {"question_length": len(question), "test_double": True},
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
                "Return exactly one JSON query plan. Use only a single PostgreSQL SELECT or WITH "
                "... SELECT and only these unqualified tables and columns: "
                f"{json.dumps(table_mapping, sort_keys=True)}. Never use DDL/DML, system catalogs, "
                "schema-qualified names, external functions, or parameters. A chart is optional "
                "and may only reference returned columns. User question: " + question.strip()
            ),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=query_plan_json_schema(),
                temperature=0.0,
            ),
        )
        return (
            parse_query_plan(response.text),
            settings.gemini_model,
            {"question_length": len(question)},
        )
    except QueryError:
        raise
    except Exception as error:
        raise QueryError(
            "Gemini could not produce a valid query plan; nothing was executed."
        ) from error


def execute_validated_query(dsn: str, *, storage_schema: str, query: ValidatedQuery) -> QueryResult:
    """Run validated SQL with a read-only role, transaction, search path, and resource caps."""

    try:
        with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL ROLE {QUERY_ROLE}")
                cursor.execute("SET LOCAL TRANSACTION READ ONLY")
                cursor.execute("SET LOCAL statement_timeout = '3000ms'")
                cursor.execute(
                    sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(
                        sql.Identifier(storage_schema)
                    )
                )
                cursor.execute(query.sql)
                rows = list(cursor.fetchall())
    except psycopg.Error as error:
        raise QueryError("The validated query could not be executed safely.") from error
    size = len(json.dumps(rows, default=_json_default).encode("utf-8"))
    if size > MAX_QUERY_BYTES:
        raise QueryError("Query results exceed the 1 MB response limit; ask a narrower question.")
    _validate_chart(query.chart, rows)
    return QueryResult(rows, query.sql, query.explanation, query.chart)


def _cte_names(statement: Any) -> set[str]:
    with_clause = getattr(statement, "withClause", None)
    return {cte.ctename for cte in (getattr(with_clause, "ctes", None) or ())}


def _validate_relation(node: Any, allowed_tables: set[str], cte_names: set[str]) -> None:
    name = node.relname
    if node.schemaname or node.catalogname:
        raise QueryError("Schema-qualified tables are not allowed.")
    if name not in allowed_tables and name not in cte_names:
        raise QueryError("SQL references a table outside the selected dataset version.")


def _validate_function(node: Any) -> None:
    parts = getattr(node, "funcname", None) or ()
    name = ".".join(getattr(part, "sval", "") for part in parts).lower()
    if name not in SAFE_FUNCTIONS:
        raise QueryError(f"Function '{name or 'unknown'}' is not allowed by the query policy.")


def _walk(node: Any):
    if node is None or isinstance(node, str | int | float | bool | bytes):
        return
    if isinstance(node, tuple | list):
        for child in node:
            yield from _walk(child)
        return
    yield node
    for attribute in getattr(node, "__slots__", ()):
        if attribute == "location":
            continue
        try:
            yield from _walk(getattr(node, attribute))
        except AttributeError:
            continue


def _validate_chart(chart: ChartSpec | None, rows: list[dict[str, Any]]) -> None:
    if chart is None:
        return
    if not rows:
        raise QueryError("A chart cannot be rendered because the query returned no rows.")
    columns = set(rows[0])
    if {chart.x_column, chart.y_column} - columns:
        raise QueryError("Chart columns must be present in the query result.")


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)

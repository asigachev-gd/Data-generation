"""Coverage for the supported PostgreSQL DDL subset."""

from pathlib import Path

import pytest

from app.ddl import DDLError, parse_schema


def test_parses_quoted_composite_schema_and_orders_dependencies() -> None:
    schema = parse_schema(
        """
        CREATE TABLE "Account" (
            "tenantId" integer NOT NULL,
            id integer NOT NULL,
            email character varying(255) NOT NULL UNIQUE,
            balance numeric(12, 2) DEFAULT 0 CHECK (balance >= 0),
            PRIMARY KEY ("tenantId", id)
        );
        CREATE TABLE invoice (
            tenant_id integer NOT NULL,
            account_id integer NOT NULL,
            issued_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tenant_id, account_id),
            FOREIGN KEY (tenant_id, account_id)
              REFERENCES "Account" ("tenantId", id) ON DELETE CASCADE DEFERRABLE
        );
        """
    )

    account = schema.table("Account")
    invoice = schema.table("invoice")
    assert account is not None and invoice is not None
    assert account.primary_key == ("tenantId", "id")
    assert account.column("email").data_type.display_name == "character varying(255)"
    assert account.column("balance").checks == ("balance >= 0",)
    assert invoice.foreign_keys[0].on_delete == "CASCADE"
    assert invoice.foreign_keys[0].deferrable is True
    assert schema.dependency_order() == ("Account", "invoice")


@pytest.mark.parametrize(
    ("ddl", "message"),
    [
        (
            "CREATE TABLE a (id integer PRIMARY KEY); CREATE VIEW v AS SELECT * FROM a;",
            "Only CREATE TABLE",
        ),
        ("CREATE TABLE a (id integer PRIMARY KEY, b_id text REFERENCES a(id));", "incompatible"),
        (
            "CREATE TABLE a (id integer PRIMARY KEY, b_id integer REFERENCES missing(id));",
            "unknown table",
        ),
        ("CREATE TABLE a (id integer, PRIMARY KEY (missing));", "unknown column"),
        (
            "CREATE TABLE a (id integer PRIMARY KEY, b_id integer NOT NULL REFERENCES b(id));"
            " CREATE TABLE b (id integer PRIMARY KEY, a_id integer NOT NULL REFERENCES a(id));",
            "cycle requires",
        ),
    ],
)
def test_rejects_invalid_schema_with_actionable_error(ddl: str, message: str) -> None:
    with pytest.raises(DDLError, match=message):
        parse_schema(ddl)


def test_accepts_nullable_cycle_and_records_strategy() -> None:
    schema = parse_schema(
        "CREATE TABLE a (id integer PRIMARY KEY, b_id integer REFERENCES b(id));"
        "CREATE TABLE b (id integer PRIMARY KEY, a_id integer REFERENCES a(id));"
    )

    assert schema.cycle_strategy == "nullable_or_deferred"


def test_supports_maximum_scope_dependency_chain() -> None:
    schema = parse_schema(
        "CREATE TABLE t1 (id integer PRIMARY KEY);"
        "CREATE TABLE t2 (id integer PRIMARY KEY, t1_id integer NOT NULL REFERENCES t1(id));"
        "CREATE TABLE t3 (id integer PRIMARY KEY, t2_id integer NOT NULL REFERENCES t2(id));"
        "CREATE TABLE t4 (id integer PRIMARY KEY, t3_id integer NOT NULL REFERENCES t3(id));"
        "CREATE TABLE t5 (id integer PRIMARY KEY, t4_id integer NOT NULL REFERENCES t4(id));"
        "CREATE TABLE t6 (id integer PRIMARY KEY, t5_id integer NOT NULL REFERENCES t5(id));"
        "CREATE TABLE t7 (id integer PRIMARY KEY, t6_id integer NOT NULL REFERENCES t6(id));"
    )

    assert schema.dependency_order() == ("t1", "t2", "t3", "t4", "t5", "t6", "t7")


@pytest.mark.parametrize(
    "filename",
    ["library_mgm_schema.ddl", "company_employee_schema.ddl", "restrurants_schema.ddl"],
)
def test_mysql_samples_are_rejected_without_execution(filename: str) -> None:
    source = Path(filename).read_text()

    with pytest.raises(DDLError, match="Invalid PostgreSQL DDL"):
        parse_schema(source)

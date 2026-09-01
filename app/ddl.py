"""Parse and validate the deliberately small PostgreSQL CREATE TABLE subset."""

from __future__ import annotations

from dataclasses import replace

from pglast import parse_sql
from pglast.ast import ColumnDef, Constraint, CreateStmt
from pglast.enums import ConstrType
from pglast.parser import ParseError
from pglast.stream import RawStream

from app.schema import Column, DataType, ForeignKey, Schema, SourceLocation, Table, UniqueConstraint

MAX_TABLES = 7
_TYPE_ALIASES = {
    "int2": "smallint",
    "smallint": "smallint",
    "int4": "integer",
    "int": "integer",
    "integer": "integer",
    "int8": "bigint",
    "bigint": "bigint",
    "float4": "real",
    "real": "real",
    "float8": "double precision",
    "double precision": "double precision",
    "numeric": "numeric",
    "decimal": "numeric",
    "varchar": "character varying",
    "character varying": "character varying",
    "bpchar": "character",
    "char": "character",
    "character": "character",
    "text": "text",
    "bool": "boolean",
    "boolean": "boolean",
    "date": "date",
    "time": "time without time zone",
    "time without time zone": "time without time zone",
    "timetz": "time with time zone",
    "time with time zone": "time with time zone",
    "timestamp": "timestamp without time zone",
    "timestamp without time zone": "timestamp without time zone",
    "timestamptz": "timestamp with time zone",
    "timestamp with time zone": "timestamp with time zone",
    "uuid": "uuid",
    "json": "json",
    "jsonb": "jsonb",
    "bytea": "bytea",
}
_ACTION_CODES = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}


class DDLError(ValueError):
    """A parser or validation error with a position in the uploaded DDL."""

    def __init__(self, message: str, location: SourceLocation | None = None) -> None:
        self.location = location
        prefix = f"Line {location.line}, column {location.column}: " if location else ""
        super().__init__(prefix + message)


def parse_schema(ddl: str) -> Schema:
    """Parse *ddl* without executing it and return its validated canonical model."""

    if not ddl.strip():
        raise DDLError("DDL upload is empty.")
    try:
        statements = parse_sql(ddl)
    except ParseError as error:
        position = error.args[1] if len(error.args) > 1 else None
        raise DDLError(f"Invalid PostgreSQL DDL: {error}", _location(ddl, position)) from error

    tables: list[Table] = []
    for raw_statement in statements:
        statement = raw_statement.stmt
        location = _location(ddl, raw_statement.stmt_location)
        if not isinstance(statement, CreateStmt):
            raise DDLError(
                "Only CREATE TABLE statements are supported; uploaded DDL is never executed.",
                location,
            )
        tables.append(_parse_table(statement, ddl, location))
    if len(tables) > MAX_TABLES:
        raise DDLError(f"At most {MAX_TABLES} related tables are supported per upload.")
    _validate_tables(tables)
    cycle_strategy = _validate_cycles(tables)
    return Schema(tables=tuple(tables), original_ddl=ddl, cycle_strategy=cycle_strategy)


def _parse_table(statement: CreateStmt, ddl: str, location: SourceLocation | None) -> Table:
    if (
        statement.relation.schemaname
        or statement.if_not_exists
        or statement.inhRelations
        or statement.partspec
    ):
        raise DDLError(
            "Schemas, IF NOT EXISTS, inheritance, and partitioning are unsupported.", location
        )
    if statement.constraints or statement.options or statement.ofTypename or statement.accessMethod:
        raise DDLError("This CREATE TABLE feature is outside the supported DDL subset.", location)
    columns: list[Column] = []
    primary_key: tuple[str, ...] = ()
    uniques: list[UniqueConstraint] = []
    foreign_keys: list[ForeignKey] = []
    checks: list[str] = []
    for element in statement.tableElts or ():
        local_column: str | None = None
        if isinstance(element, ColumnDef):
            column, constraints = _parse_column(element, ddl)
            columns.append(column)
            local_column = column.name
        elif isinstance(element, Constraint):
            constraints = (element,)
        else:
            raise DDLError("Only scalar columns and table constraints are supported.", location)
        for constraint in constraints:
            kind = constraint.contype
            constraint_location = _location(ddl, getattr(constraint, "location", None)) or location
            if kind == ConstrType.CONSTR_PRIMARY:
                if primary_key:
                    raise DDLError("A table may declare only one primary key.", constraint_location)
                primary_key = _constraint_names(constraint.keys, local_column)
            elif kind == ConstrType.CONSTR_UNIQUE:
                uniques.append(
                    UniqueConstraint(
                        _constraint_names(constraint.keys, local_column), constraint.conname or None
                    )
                )
            elif kind == ConstrType.CONSTR_FOREIGN:
                foreign_keys.append(_foreign_key(constraint, constraint_location, local_column))
            elif kind == ConstrType.CONSTR_CHECK:
                checks.append(_render(constraint.raw_expr))
            elif kind in (ConstrType.CONSTR_NOTNULL, ConstrType.CONSTR_DEFAULT):
                # These were consumed into the column model by _parse_column.
                continue
            else:
                raise DDLError(
                    "This constraint is outside the supported DDL subset.", constraint_location
                )
    if not columns:
        raise DDLError("CREATE TABLE must include at least one scalar column.", location)
    primary_key_set = set(primary_key)
    columns = [
        replace(column, nullable=False) if column.name in primary_key_set else column
        for column in columns
    ]
    return Table(
        name=statement.relation.relname,
        columns=tuple(columns),
        primary_key=primary_key,
        unique_constraints=tuple(uniques),
        foreign_keys=tuple(foreign_keys),
        checks=tuple(checks),
        location=location,
    )


def _parse_column(column: ColumnDef, ddl: str) -> tuple[Column, tuple[Constraint, ...]]:
    location = _location(ddl, column.location)
    if column.identity not in ("", "\x00") or column.generated not in ("", "\x00"):
        raise DDLError("Identity and generated columns are unsupported.", location)
    data_type = _data_type(column, location)
    nullable = True
    default: str | None = None
    checks: list[str] = []
    retained: list[Constraint] = []
    for constraint in column.constraints or ():
        if constraint.contype == ConstrType.CONSTR_NOTNULL:
            nullable = False
        elif constraint.contype == ConstrType.CONSTR_DEFAULT:
            default = _render(constraint.raw_expr)
        elif constraint.contype == ConstrType.CONSTR_CHECK:
            checks.append(_render(constraint.raw_expr))
        elif constraint.contype in (
            ConstrType.CONSTR_PRIMARY,
            ConstrType.CONSTR_UNIQUE,
            ConstrType.CONSTR_FOREIGN,
        ):
            retained.append(constraint)
        else:
            raise DDLError("This column constraint is outside the supported DDL subset.", location)
    return Column(column.colname, data_type, nullable, default, tuple(checks), location), tuple(
        retained
    )


def _data_type(column: ColumnDef, location: SourceLocation | None) -> DataType:
    names = tuple(part.sval for part in column.typeName.names)
    raw_name = " ".join(names[1:] if names[:1] == ("pg_catalog",) else names).lower()
    normalized = _TYPE_ALIASES.get(raw_name)
    if normalized is None:
        raise DDLError(f"Unsupported scalar type '{raw_name}'.", location)
    parameters: list[int] = []
    for modifier in column.typeName.typmods or ():
        try:
            parameters.append(modifier.val.ival)
        except AttributeError as error:
            raise DDLError("Type parameters must be integer literals.", location) from error
    return DataType(normalized, tuple(parameters))


def _foreign_key(
    constraint: Constraint, location: SourceLocation | None, local_column: str | None
) -> ForeignKey:
    if constraint.pktable.schemaname:
        raise DDLError(
            "Foreign keys may reference tables in this upload only, without a schema qualifier.",
            location,
        )
    if not constraint.pk_attrs:
        raise DDLError(
            "Foreign keys must explicitly name both local and referenced columns.", location
        )
    local_columns = _constraint_names(constraint.fk_attrs, local_column)
    if not local_columns:
        raise DDLError(
            "Foreign keys must explicitly name both local and referenced columns.", location
        )
    return ForeignKey(
        columns=local_columns,
        referenced_table=constraint.pktable.relname,
        referenced_columns=_names(constraint.pk_attrs),
        on_update=_ACTION_CODES.get(constraint.fk_upd_action, "NO ACTION"),
        on_delete=_ACTION_CODES.get(constraint.fk_del_action, "NO ACTION"),
        deferrable=constraint.deferrable,
        initially_deferred=constraint.initdeferred,
        name=constraint.conname or None,
        location=location,
    )


def _validate_tables(tables: list[Table]) -> None:
    by_name: dict[str, Table] = {}
    for table in tables:
        if table.name in by_name:
            raise DDLError(f"Duplicate table identifier '{table.name}'.", table.location)
        by_name[table.name] = table
        names = [column.name for column in table.columns]
        duplicate = _first_duplicate(names)
        if duplicate:
            raise DDLError(
                f"Duplicate column identifier '{duplicate}' in table '{table.name}'.",
                table.location,
            )
        if table.primary_key:
            _validate_key(table, table.primary_key, "primary key", table.location)
        for unique in table.unique_constraints:
            _validate_key(table, unique.columns, "unique constraint", table.location)
    for table in tables:
        for foreign_key in table.foreign_keys:
            _validate_key(table, foreign_key.columns, "foreign key", foreign_key.location)
            target = by_name.get(foreign_key.referenced_table)
            if target is None:
                raise DDLError(
                    f"Foreign key references unknown table '{foreign_key.referenced_table}'.",
                    foreign_key.location,
                )
            _validate_key(
                target, foreign_key.referenced_columns, "referenced key", foreign_key.location
            )
            if len(foreign_key.columns) != len(foreign_key.referenced_columns):
                raise DDLError(
                    "Foreign-key local and referenced column counts must match.",
                    foreign_key.location,
                )
            candidate_keys = {
                target.primary_key,
                *(unique.columns for unique in target.unique_constraints),
            }
            if foreign_key.referenced_columns not in candidate_keys:
                raise DDLError(
                    "Foreign keys must reference a primary or unique key.", foreign_key.location
                )
            for local, referenced in zip(
                foreign_key.columns, foreign_key.referenced_columns, strict=True
            ):
                if table.column(local).data_type.name != target.column(referenced).data_type.name:
                    raise DDLError(
                        f"Foreign-key types are incompatible: '{table.name}.{local}' and "
                        f"'{target.name}.{referenced}'.",
                        foreign_key.location,
                    )


def _validate_key(
    table: Table, columns: tuple[str, ...], label: str, location: SourceLocation | None
) -> None:
    if not columns:
        raise DDLError(f"A {label} must name at least one column.", location)
    duplicate = _first_duplicate(columns)
    if duplicate:
        raise DDLError(f"A {label} cannot name '{duplicate}' more than once.", location)
    unknown = next((name for name in columns if table.column(name) is None), None)
    if unknown:
        raise DDLError(f"{label.capitalize()} references unknown column '{unknown}'.", location)


def _validate_cycles(tables: list[Table]) -> str:
    graph = {table.name: {key.referenced_table for key in table.foreign_keys} for table in tables}
    components = _strongly_connected_components(graph)
    cycle_members = [
        component
        for component in components
        if len(component) > 1 or next(iter(component)) in graph[next(iter(component))]
    ]
    if not cycle_members:
        return "none"
    by_name = {table.name: table for table in tables}
    for component in cycle_members:
        for table_name in component:
            table = by_name[table_name]
            for foreign_key in table.foreign_keys:
                if foreign_key.referenced_table not in component:
                    continue
                nullable = all(table.column(column).nullable for column in foreign_key.columns)
                if not nullable and not foreign_key.deferrable:
                    raise DDLError(
                        "Foreign-key cycle requires every participating constraint to be nullable "
                        "or DEFERRABLE.",
                        foreign_key.location,
                    )
    return "nullable_or_deferred"


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[set[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[set[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in graph[node]:
            if neighbor not in indices:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])
        if lowlinks[node] == indices[node]:
            component: set[str] = set()
            while True:
                child = stack.pop()
                on_stack.remove(child)
                component.add(child)
                if child == node:
                    break
            components.append(component)

    for node in graph:
        if node not in indices:
            visit(node)
    return components


def _names(nodes: tuple[object, ...] | None) -> tuple[str, ...]:
    return tuple(node.sval for node in nodes or ())


def _constraint_names(
    nodes: tuple[object, ...] | None, local_column: str | None
) -> tuple[str, ...]:
    names = _names(nodes)
    return names or ((local_column,) if local_column else ())


def _render(node: object) -> str:
    return RawStream()(node)


def _location(source: str, offset: int | None) -> SourceLocation | None:
    if offset is None or offset < 0:
        return None
    before = source[:offset]
    return SourceLocation(before.count("\n") + 1, offset - before.rfind("\n"))


def _first_duplicate(names: list[str] | tuple[str, ...]) -> str | None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            return name
        seen.add(name)
    return None

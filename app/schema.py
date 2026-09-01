"""Canonical, validated representation of the supported PostgreSQL DDL subset."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CycleStrategy = Literal["none", "nullable_or_deferred"]


@dataclass(frozen=True)
class SourceLocation:
    """One-based source position, retained for actionable upload errors."""

    line: int
    column: int


@dataclass(frozen=True)
class DataType:
    """A normalized scalar PostgreSQL type and any declared parameters."""

    name: str
    parameters: tuple[int, ...] = ()

    @property
    def display_name(self) -> str:
        parameters = ", ".join(str(parameter) for parameter in self.parameters)
        return f"{self.name}({parameters})" if parameters else self.name


@dataclass(frozen=True)
class Column:
    name: str
    data_type: DataType
    nullable: bool = True
    default: str | None = None
    checks: tuple[str, ...] = ()
    location: SourceLocation | None = None


@dataclass(frozen=True)
class UniqueConstraint:
    columns: tuple[str, ...]
    name: str | None = None


@dataclass(frozen=True)
class ForeignKey:
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    on_update: str = "NO ACTION"
    on_delete: str = "NO ACTION"
    deferrable: bool = False
    initially_deferred: bool = False
    name: str | None = None
    location: SourceLocation | None = None


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    unique_constraints: tuple[UniqueConstraint, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    checks: tuple[str, ...] = ()
    location: SourceLocation | None = None

    def column(self, name: str) -> Column | None:
        return next((column for column in self.columns if column.name == name), None)


@dataclass(frozen=True)
class Schema:
    tables: tuple[Table, ...]
    original_ddl: str = field(repr=False, compare=False)
    cycle_strategy: CycleStrategy = "none"

    def table(self, name: str) -> Table | None:
        return next((table for table in self.tables if table.name == name), None)

    @property
    def dependency_graph(self) -> dict[str, frozenset[str]]:
        """Map each table to tables it depends on through foreign keys."""

        return {
            table.name: frozenset(
                foreign_key.referenced_table for foreign_key in table.foreign_keys
            )
            for table in self.tables
        }

    def dependency_order(self) -> tuple[str, ...]:
        """Return parent-first order; cyclic members retain upload order together."""

        graph = self.dependency_graph
        ordered: list[str] = []
        remaining = set(graph)
        while remaining:
            ready = [name for name in graph if name in remaining and not (graph[name] & remaining)]
            if not ready:
                ordered.extend(table.name for table in self.tables if table.name in remaining)
                break
            ordered.extend(ready)
            remaining.difference_update(ready)
        return tuple(ordered)

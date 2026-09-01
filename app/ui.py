"""Data Generation Streamlit workflow and small testable input helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.ddl import DDLError, parse_schema
from app.edits import AppliedEdit, EditError, EditPlan, apply_edit, request_edit_plan
from app.generation import MAX_ROW_COUNT, MIN_ROW_COUNT, generate_dataset
from app.persistence import DatasetStore, PersistedVersion
from app.query import (
    QueryError,
    QueryResult,
    execute_validated_query,
    request_query_plan,
    validate_query_plan,
)

ALLOWED_UPLOAD_SUFFIXES = (".sql", ".ddl", ".txt")
MIN_TEMPERATURE = 0.0
MAX_TEMPERATURE = 1.0
PREVIEW_PAGE_SIZE = 100


class UploadedFile(Protocol):
    name: str

    def getvalue(self) -> bytes: ...


@dataclass(frozen=True)
class GenerationInputs:
    instructions: str
    temperature: float
    seed: int | None
    row_counts: dict[str, int]


def decode_ddl_upload(upload: UploadedFile | None) -> str:
    """Validate the uploaded extension and decode a UTF-8 PostgreSQL DDL document."""

    if upload is None:
        raise ValueError("Choose a .sql, .ddl, or .txt schema file before generating.")
    if not upload.name.lower().endswith(ALLOWED_UPLOAD_SUFFIXES):
        raise ValueError("Supported schema files are .sql, .ddl, and .txt.")
    try:
        source = upload.getvalue().decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("The schema file must be UTF-8 encoded.") from error
    if not source.strip():
        raise ValueError("The uploaded schema file is empty.")
    return source


def validate_generation_inputs(
    instructions: str, temperature: float, seed: int | None, row_counts: dict[str, int]
) -> GenerationInputs:
    """Validate UI controls before any model, generation, or database work starts."""

    if not MIN_TEMPERATURE <= temperature <= MAX_TEMPERATURE:
        raise ValueError(
            f"Temperature must be between {MIN_TEMPERATURE:.1f} and {MAX_TEMPERATURE:.1f}."
        )
    if seed is not None and not 0 <= seed < 2**63:
        raise ValueError("Seed must be a whole number from 0 through 9223372036854775807.")
    invalid = [
        name
        for name, count in row_counts.items()
        if not isinstance(count, int) or not MIN_ROW_COUNT <= count <= MAX_ROW_COUNT
    ]
    if invalid:
        raise ValueError(
            f"Rows per table must be whole numbers from {MIN_ROW_COUNT} through {MAX_ROW_COUNT}."
        )
    return GenerationInputs(instructions.strip(), temperature, seed, row_counts)


def run_generation(
    upload: UploadedFile | None,
    inputs: GenerationInputs,
    *,
    settings: Any,
    store: DatasetStore,
) -> PersistedVersion:
    """Run the parse/generate/persist pipeline used by the Generate button."""

    schema = parse_schema(decode_ddl_upload(upload))
    dataset = generate_dataset(
        schema,
        row_counts=inputs.row_counts,
        seed=inputs.seed,
        instructions=inputs.instructions,
        settings=settings,
        temperature=inputs.temperature,
    )
    return store.persist_dataset(schema, dataset)


def propose_table_edit(
    store: DatasetStore,
    *,
    dataset_id: str,
    version_id: str,
    target_table: str,
    prompt: str,
    settings: Any,
) -> tuple[EditPlan, str | None, dict[str, Any]]:
    """Request a bounded edit proposal for the selected immutable version."""

    if not prompt.strip():
        raise ValueError("Describe the requested table change before proposing an edit.")
    version = store.get_version(dataset_id, version_id)
    if not version.active:
        raise ValueError("Select the active dataset version before proposing an edit.")
    schema = store.schema_for_version(dataset_id, version_id)
    return request_edit_plan(
        schema, target_table=target_table, prompt=prompt.strip(), settings=settings
    )


def execute_table_edit(
    store: DatasetStore,
    *,
    dataset_id: str,
    version_id: str,
    plan: EditPlan,
    prompt: str,
    model: str | None,
    model_metadata: dict[str, Any],
    seed: int | None = None,
) -> tuple[PersistedVersion, AppliedEdit]:
    """Apply a confirmed edit to a new version, retaining auditable lineage metadata."""

    version = store.get_version(dataset_id, version_id)
    if not version.active:
        raise ValueError("The selected version is no longer active; propose the edit again.")
    schema = store.schema_for_version(dataset_id, version_id)
    base_rows = {}
    for table in schema.tables:
        row_count = _table_row_count(store, dataset_id, version_id, table.name)
        base_rows[table.name] = tuple(
            store.table_rows(
                dataset_id, table.name, version_id=version_id, limit=1_000, offset=offset
            )
            for offset in range(0, row_count, 1_000)
        )
    flattened = {
        name: tuple(row for page in pages for row in page) for name, pages in base_rows.items()
    }
    applied = apply_edit(schema, flattened, plan, seed=seed)
    metadata = {
        "parent_version_id": version_id,
        "original_prompt": prompt,
        "validated_plan": plan.model_dump(),
        "model": model,
        "model_metadata": model_metadata,
        "telemetry": {"prompt_length": len(prompt), "target_table": plan.target_table},
        "validation": {"valid": True, "errors": []},
        "affected_rows": applied.affected_rows,
    }
    stored = store.persist_dataset(
        schema,
        applied.dataset,
        dataset_id=dataset_id,
        request_kind="edit",
        request_metadata=metadata,
        parent_version_id=version_id,
    )
    return stored, applied


def _table_row_count(store: DatasetStore, dataset_id: str, version_id: str, table_name: str) -> int:
    version = store.get_version(dataset_id, version_id)
    return int(version.report.get("generated_rows", {}).get(table_name, 0))


def answer_data_question(
    store: DatasetStore, *, dataset_id: str, version_id: str, question: str, settings: Any
) -> QueryResult:
    """Plan, validate, and execute a question only against its selected dataset version."""

    version = store.get_version(dataset_id, version_id)
    schema = store.schema_for_version(dataset_id, version_id)
    table_mapping = {
        table.name: [column.name for column in table.columns] for table in schema.tables
    }
    plan, _, _ = request_query_plan(
        question=question, table_mapping=table_mapping, settings=settings
    )
    validated = validate_query_plan(
        plan, allowed_tables=set(table_mapping), storage_schema=version.storage_schema
    )
    return execute_validated_query(
        store.dsn, storage_schema=version.storage_schema, query=validated
    )


def render_talk_to_data(st: Any, *, settings: Any) -> None:
    """Render selected-version natural-language querying without trusting model SQL."""

    st.header("Talk to your data")
    st.caption(
        "Ask about one generated dataset version. Queries are AST-validated SELECT statements, "
        "run with a read-only role and capped at 500 rows / 1 MB."
    )
    try:
        store = DatasetStore(settings.database_dsn)
        versions = store.queryable_versions()
    except Exception:
        st.warning("Generated datasets are not currently available from PostgreSQL.")
        return
    if not versions:
        st.info("Generate a dataset before asking questions about it.")
        return
    preferred = (
        st.session_state.get("active_dataset_id"),
        st.session_state.get("active_version_id"),
    )
    default_index = next(
        (
            index
            for index, item in enumerate(versions)
            if (str(item.dataset_id), str(item.version_id)) == preferred
        ),
        0,
    )
    selected = st.selectbox(
        "Dataset version",
        versions,
        index=default_index,
        format_func=lambda item: (
            f"Dataset {item.dataset_id} · version {item.version_number}"
            + (" (active)" if item.active else "")
        ),
    )
    question = st.text_area(
        "Question", placeholder="For example: What are the five most common customer cities?"
    )
    if not st.button("Ask", type="primary", disabled=not question.strip()):
        return
    try:
        with st.status("Validating a safe query…", expanded=True) as status:
            st.write("Gemini is creating a structured query plan.")
            result = answer_data_question(
                store,
                dataset_id=str(selected.dataset_id),
                version_id=str(selected.version_id),
                question=question,
                settings=settings,
            )
            st.write("The query passed the selected-version safety policy.")
            status.update(label="Query complete", state="complete")
        st.caption(result.explanation)
        with st.expander("Validated SQL"):
            st.code(result.sql, language="sql")
        if result.rows:
            st.dataframe(result.rows, use_container_width=True, hide_index=True)
        else:
            st.info("The query completed successfully but returned no rows.")
        if result.chart:
            st.subheader(result.chart.title)
            chart_data = {
                item[result.chart.x_column]: item[result.chart.y_column] for item in result.rows
            }
            if result.chart.chart_type == "line":
                st.line_chart(chart_data)
            elif result.chart.chart_type == "scatter":
                st.scatter_chart(chart_data)
            else:
                st.bar_chart(chart_data)
    except QueryError as error:
        st.error(str(error))
    except Exception:
        st.error("The question could not be completed safely. No query was run.")


def render_data_generation(st: Any, *, settings: Any) -> None:
    """Render the complete Step 5 view using persisted versions for all previews."""

    st.header("Data Generation")
    st.caption(
        "Upload supported PostgreSQL CREATE TABLE DDL (up to seven related tables), then "
        "generate a versioned, constraint-validated dataset."
    )
    upload = st.file_uploader("DDL schema", type=["sql", "ddl", "txt"])
    instructions = st.text_area(
        "Generation instructions", placeholder="For example: use plausible Mexican customer names."
    )
    controls, rows_column = st.columns(2)
    with controls:
        temperature = st.slider(
            "Temperature",
            MIN_TEMPERATURE,
            MAX_TEMPERATURE,
            0.2,
            0.1,
            help="Gemini profile-planning variability; allowed range is 0.0–1.0.",
        )
        seed_text = st.text_input(
            "Optional seed",
            placeholder="Leave empty for a new random seed",
            help="Use the same seed and schema to reproduce local generated rows.",
        )

    schema = None
    if upload is not None:
        try:
            schema = parse_schema(decode_ddl_upload(upload))
            st.success(f"Schema accepted: {len(schema.tables)} table(s).")
        except (ValueError, DDLError) as error:
            st.error(str(error))
    with rows_column:
        if schema is None:
            st.info("Row-count controls appear after a valid schema is uploaded.")
            row_counts: dict[str, int] = {}
        else:
            st.caption(f"Rows per table ({MIN_ROW_COUNT:,}–{MAX_ROW_COUNT:,})")
            row_counts = {
                table.name: st.number_input(
                    f"{table.name} rows",
                    MIN_ROW_COUNT,
                    MAX_ROW_COUNT,
                    1_000,
                    1,
                    key=f"rows_{table.name}",
                )
                for table in schema.tables
            }

    if st.button("Generate", type="primary", disabled=schema is None):
        try:
            seed = int(seed_text) if seed_text.strip() else None
            inputs = validate_generation_inputs(instructions, temperature, seed, row_counts)
            store = DatasetStore(settings.database_dsn)
            with st.status("Generating data…", expanded=True) as status:
                st.write("Creating a validated generation profile.")
                stored = run_generation(upload, inputs, settings=settings, store=store)
                st.write("Persisting and validating the new dataset version.")
                status.update(label="Dataset generated", state="complete")
            st.session_state["active_dataset_id"] = str(stored.dataset_id)
            st.session_state["active_version_id"] = str(stored.version_id)
            st.success(
                f"Generated dataset {stored.dataset_id} — active version {stored.version_number}."
            )
        except (ValueError, DDLError) as error:
            st.error(str(error))
        except Exception:
            st.error(
                "Generation could not complete. Check database readiness and the supported DDL."
            )

    _render_persisted_dataset(st, settings)


def _render_persisted_dataset(st: Any, settings: Any) -> None:
    dataset_id = st.session_state.get("active_dataset_id")
    version_id = st.session_state.get("active_version_id")
    if not dataset_id or not version_id:
        st.info("Generate a dataset to preview its tables and download exports.")
        return
    try:
        store = DatasetStore(settings.database_dsn)
        version = store.get_version(dataset_id, version_id)
    except Exception:
        st.warning("The selected generated dataset is not currently available from PostgreSQL.")
        return
    st.subheader("Active dataset")
    st.caption(
        f"Dataset `{version.dataset_id}` · version {version.version_number} · "
        f"`{version.version_id}`"
    )
    st.markdown("#### Validation report")
    if version.report.get("validation_errors"):
        st.error("\n".join(version.report["validation_errors"]))
    else:
        st.success("All generated rows passed validation before the version was activated.")
    st.json(
        {
            "requested_rows": version.report.get("requested_rows", {}),
            "generated_rows": version.report.get("generated_rows", {}),
            "seed": version.report.get("seed"),
            "used_fallback_profile": version.report.get("used_fallback_profile", False),
            "warnings": version.report.get("warnings", []),
        },
        expanded=False,
    )
    generated_counts = version.report.get("generated_rows", {})
    table_names = [table["name"] for table in version.schema["tables"]]
    st.download_button(
        "Download all tables (.zip)",
        data=store.export_zip(dataset_id, version_id=version_id),
        file_name=f"dataset-v{version.version_number}.zip",
        mime="application/zip",
    )
    _render_table_edit(st, settings, store, version, table_names)
    tabs = st.tabs(table_names)
    for tab, table_name in zip(tabs, table_names, strict=True):
        with tab:
            total = int(generated_counts.get(table_name, 0))
            pages = max(1, (total + PREVIEW_PAGE_SIZE - 1) // PREVIEW_PAGE_SIZE)
            page = st.number_input("Preview page", 1, pages, 1, key=f"preview_page_{table_name}")
            rows = store.table_rows(
                dataset_id,
                table_name,
                version_id=version_id,
                offset=(page - 1) * PREVIEW_PAGE_SIZE,
                limit=PREVIEW_PAGE_SIZE,
            )
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
            else:
                st.info("This table has no generated rows.")
            st.download_button(
                f"Download {table_name} CSV",
                data=store.export_csv(dataset_id, table_name, version_id=version_id),
                file_name=f"{table_name}.csv",
                mime="text/csv",
                key=f"download_{table_name}",
            )


def _render_table_edit(
    st: Any, settings: Any, store: DatasetStore, version: Any, table_names: list[str]
) -> None:
    """Render proposal/review/confirmation controls without retaining table values in state."""

    st.markdown("#### Edit a table")
    target = st.selectbox("Target table", table_names, key="edit_target_table")
    prompt = st.text_area("Requested change", key="edit_prompt")
    proposal_key = "pending_edit_proposal"
    if st.button("Propose edit", disabled=not prompt.strip()):
        try:
            plan, model, metadata = propose_table_edit(
                store,
                dataset_id=str(version.dataset_id),
                version_id=str(version.version_id),
                target_table=target,
                prompt=prompt,
                settings=settings,
            )
            st.session_state[proposal_key] = {
                "plan": plan.model_dump(),
                "model": model,
                "metadata": metadata,
                "dataset_id": str(version.dataset_id),
                "version_id": str(version.version_id),
                "prompt": prompt,
            }
        except (ValueError, EditError) as error:
            st.error(str(error))
    pending = st.session_state.get(proposal_key)
    if not pending:
        return
    if pending["dataset_id"] != str(version.dataset_id) or pending["version_id"] != str(
        version.version_id
    ):
        st.session_state.pop(proposal_key, None)
        return
    st.caption("Review the validated proposal. Confirmation creates a new immutable version.")
    st.json(pending["plan"])
    if st.button("Confirm and create new version", type="primary"):
        try:
            stored, applied = execute_table_edit(
                store,
                dataset_id=pending["dataset_id"],
                version_id=pending["version_id"],
                plan=EditPlan.model_validate(pending["plan"]),
                prompt=pending["prompt"],
                model=pending["model"],
                model_metadata=pending["metadata"],
            )
            st.session_state["active_dataset_id"] = str(stored.dataset_id)
            st.session_state["active_version_id"] = str(stored.version_id)
            st.session_state.pop(proposal_key, None)
            st.success(
                f"Edit applied to {applied.affected_rows} row(s); "
                f"version {stored.version_number} is active."
            )
            st.rerun()
        except (ValueError, EditError) as error:
            st.error(str(error))
        except Exception:
            st.error("The edit could not be persisted. The prior active version remains unchanged.")

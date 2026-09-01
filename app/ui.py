"""Data Generation Streamlit workflow and small testable input helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.ddl import DDLError, parse_schema
from app.generation import MAX_ROW_COUNT, MIN_ROW_COUNT, generate_dataset
from app.persistence import DatasetStore, PersistedVersion

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

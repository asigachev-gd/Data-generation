# Requirements

## Overview

The goal of this practice is to implement a conversational AI application with two primary functionalities: synthetic data generation and natural language data querying. This task is divided into 3 distinct phases:

- Phase 1 focuses on developing a core data generation engine. This engine must interpret the provided SQL schemas, identifying tables, columns, data types, and constraints. A data generation module will then use this parsed information to create realistic synthetic data, respecting all defined constraints, especially foreign keys, to ensure data integrity. The system should be capable of generating a configurable amount of data, such as 1,000 rows per table.
- Phase 2 and Phase 3 focus on implementing the conversational core module, which allows users to query SQL data using natural language (talk-to-your-data) and present results in the form of text, tables, and plots.

By the end of the practice, you need to develop a user-friendly UI and present both the results and the source code to your professor.

## Technical Requirements

1. LLM: Gemini 2.0 Flash or newer.
   - Use streaming, function calling, and JSON/structured output during implementation where appropriate.

2. SDK: Google GenAI SDK (with Vertex AI authentication through a GCP project).
   - Gemini access instructions
   - Tips for SQL generation with Gemini

3. UI: Streamlit or Gradio.

4. DB: PostgreSQL.

5. Docker.

6. Langfuse for observability.

## Implemented foundation (Step 1)

- The project uses Python 3.12, Streamlit, PostgreSQL 16, Docker Compose, and pinned dependencies.
- Configuration is loaded from environment variables with typed validation. Vertex AI is configured for Application Default Credentials; no service-account key is stored in the repository.
- Docker Compose health checks PostgreSQL before starting the app and the app checks its configuration and PostgreSQL connection separately. The Streamlit sidebar exposes the resulting readiness state.
- `Makefile` defines formatting, lint, unit-test, integration-test, and combined verification commands for local development and CI.
- Configuration tests isolate process environment variables from any local `.env` file, keeping missing and malformed setting checks reproducible.
- The remaining functional capabilities below are planned and will be delivered in their corresponding implementation-plan steps.

## Implemented DDL parsing (Step 2)

- Uploaded PostgreSQL DDL is parsed through a PostgreSQL-aware AST parser and is never executed during parsing.
- The accepted subset is documented in the implementation plan: scalar columns; `NOT NULL`, `DEFAULT`, `CHECK`, `UNIQUE`, primary keys, and foreign keys, including composite keys and supplied foreign-key actions/deferrability.
- The parser preserves normalized schema metadata and source positions, rejects unsupported or ambiguous SQL with actionable errors, validates foreign-key targets/type compatibility, and classifies allowable nullable/deferred cycles.
- The repository's current sample DDL files use MySQL-only constructs (`AUTO_INCREMENT`, `ENUM`, and `DATETIME`) and are intentionally rejected until PostgreSQL equivalents are supplied.

## Implemented generation engine (Step 3)

- Synthetic rows are generated locally in a reproducible seeded run, with 1,000 rows per table by default and validated per-table overrides from 1 through 10,000.
- A Gemini structured JSON profile is validated against every submitted table and column. Model failures, incomplete profiles, and invalid values safely use a local type/name-based profile instead; Gemini does not generate individual dataset rows.
- The pre-persistence validator enforces scalar type bounds, nullability, primary and unique keys, supported evaluable checks, and single/composite foreign-key membership. A generation report records counts, seed, validation result, fallback/warnings, model identifier, and sanitized metadata.

## Implemented PostgreSQL persistence and exports (Step 4)

- Validated datasets are loaded transactionally into isolated generated PostgreSQL schemas. The application renders its own quoted table definitions from the canonical model and never executes uploaded DDL.
- Dataset, schema, immutable version, table-version, request/validation, and export-audit metadata is stored in application-owned PostgreSQL tables. A version becomes active only after database-side constraint validation succeeds; failed materialization keeps diagnostics without replacing an active version.
- Retrieval is scoped to a dataset and version, defaulting to that dataset's active version. Table CSV exports are UTF-8, and ZIP exports include every table CSV plus a dataset/version/schema manifest.

## Implemented Data Generation UI (Step 5)

- The Streamlit sidebar provides the required Data Generation and Talk to your data primary views. Data Generation accepts UTF-8 `.sql`, `.ddl`, and `.txt` uploads and presents parser errors with their source context.
- Users supply instructions, a documented 0.0–1.0 profile-planning temperature, an optional seed, and per-table row counts from 1 through 10,000; no generation starts until **Generate** is selected.
- After persistence, the active dataset/version, validation-backed generated result, paginated table previews, per-table CSV downloads, and all-table ZIP download are available. Previews are read from PostgreSQL rather than retained in browser session state.

## Implemented bounded table edits (Step 6)

- A text request is sent to Gemini only to produce a strict JSON edit plan for the explicitly selected active table. The plan is displayed for review and must be confirmed before any data changes.
- Edits are deliberately limited to non-primary-key, non-unique, non-foreign-key columns. Supported safe changes regenerate matching values, add a bounded `text_prefix` for text generators, or set a nullable column's `null_probability`; unsupported relational changes are rejected rather than partially applied.
- A confirmed edit reloads the persisted version, revalidates every row and database constraint, and writes a new immutable active version. The prior version is retained, and edit metadata records parent-version lineage, the original request, validated plan, model metadata, and redacted telemetry fields.

## Implemented Talk to your data (Step 7)

- Users select a persisted generated dataset/version, ask a natural-language question, and receive a Gemini structured query plan only after local validation. The UI discloses the validated SQL, result table, explanation, and an optional allowlisted result-derived chart.
- The query policy permits one `SELECT` or `WITH ... SELECT` over only the selected version's unqualified tables. It rejects multiple statements, DDL/DML (including data-changing CTEs), schema-qualified names, unsafe functions, locks, `SELECT INTO`, and unresolved parameters.
- Every accepted query runs through the dedicated `data_generation_query` read-only role with a selected-version search path, read-only transaction, three-second statement timeout, 500-row cap, and one-megabyte response limit. If the role cannot be established, querying fails closed.

## Implemented observability and operational safeguards (Step 8)

- Generation, edit proposal/execution, CSV/ZIP export, and querying emit correlation-aware structured application logs and optional Langfuse workflow traces. Events include operation, latency, model where applicable, validation outcome, and dataset/version identifiers.
- Telemetry is disabled unless both Langfuse keys are configured. It is fail-open: unavailable Langfuse initialization, trace updates, or flushing never block generation, exports, edits, or queries.
- Prompts, SQL, generated values, result values, and secrets are redacted by default. `OBSERVABILITY_CAPTURE_CONTENT=true` is an explicit local opt-in for content capture and still never permits credential capture.
- Docker Compose waits for PostgreSQL health and invokes idempotent `app.bootstrap` metadata/query-role setup before starting Streamlit. The container health check then confirms both valid configuration and database connectivity.

## Implemented end-to-end verification (Step 9)

- A PostgreSQL-compatible library-management fixture adapted from the supplied sample validates a realistic multi-table workflow without weakening the documented DDL boundary; the original MySQL-only sample files remain rejection fixtures.
- A Playwright browser test starts a clean Docker Compose stack and completes DDL upload, generation, persisted preview, confirmed table edit, immutable version activation, CSV/ZIP downloads, and a safe Talk-to-your-data query.
- The E2E stack enables the explicit `DETERMINISTIC_TEST_MODE` structured-output double, so it exercises validated generation/edit/query plan handling without live Gemini credentials or nondeterministic model behavior. Normal application runs leave this setting disabled.
- `MAN_TEST.md` provides local startup instructions and scripted browser smoke tests for supported/rejected DDL, generation, validation, previews, exports, immutable edits, safe querying, restart persistence, and optional live Gemini/Langfuse behavior.

## Functional Requirements

### Phase 1: Synthetic Data Generation

- The system should generate consistent and valid data for the provided DDL schema (up to 5–7 tables) and instructions, including data types, null values, date and time formats, primary keys, foreign keys, and other constraints.
- The system should allow a user to modify the generated data through textual feedback.
- Generated data can be downloaded as CSV or ZIP archive and stored in the system so that it later becomes accessible in the “Talk to your data” tab.

### Sample DDL Schemas

- library_mgm.ddl
- restaurants.ddl
- company_employee.ddl

## UI Requirements

1. Sidebar with main tabs: Data Generation and Talk to your data.
2. Data Generation tab:
   - User can upload a DDL schema file (.sql, .txt, or .ddl).
   - User can add text instructions (prompt) for the data in a text box.
   - User can set additional generation parameters, such as temperature.
   - Generation happens after the user clicks the “Generate” button.
   - After data is generated, the user can preview each generated table.
   - User can apply changes to each table by entering a prompt and clicking the “Submit” button.

# Data-generation

## Project documentation

- [Requirements](reqs/REQUIREMENTS.md)
- [Implementation plan](reqs/PLAN.md) — implementation scope, milestone gates, supported DDL boundary, dataset versioning, and safe query policy.
- [Manual smoke tests](MAN_TEST.md) — local startup and browser verification scripts.

## Current status

Steps 1 through 9 are implemented: the repository contains the Streamlit application scaffold, validated environment configuration, Docker Compose local stack, database/app readiness checks, an AST-based PostgreSQL DDL parser, deterministic constraint-safe synthetic data generation, transactional PostgreSQL dataset persistence with exports, the Data Generation workflow, bounded versioned table edits, safe natural-language querying for persisted dataset versions, redacted operational telemetry, and clean Docker/browser end-to-end verification.

## Local development

1. Install Python 3.12 and Docker Desktop, then copy `.env.example` to `.env` and replace its placeholders. For live Gemini calls, provide the container with Application Default Credentials or workload identity; do not add service-account keys to this repository. Compose can parse without `.env`, but needs `POSTGRES_PASSWORD` and valid app configuration to start a ready service.
2. Start the local stack with `docker compose up --build`. Compose waits for PostgreSQL, initializes the application metadata and read-only query role, then starts Streamlit at `http://localhost:8501` by default (set `APP_PORT` to choose another host port); PostgreSQL data persists in the `postgres_data` Docker volume.
3. Install development dependencies with `python -m pip install -e '.[dev]'`, then run `make lint`, `make test-unit`, and `make test-integration`. For the browser suite, install Chromium once with `python -m playwright install chromium`, then run `make test-e2e`; `make verify` runs all checks. The integration and E2E markers require Docker. `uv sync --group dev` is also supported when uv is installed.

For a credential-free, repeatable manual smoke test of the full UI workflow, set
`DETERMINISTIC_TEST_MODE=true` in `.env` before starting Compose and follow
[MAN_TEST.md](MAN_TEST.md). This setting is test-only; keep it `false` for a
normal Gemini-backed run. A Docker container must have its own ADC or workload
identity access for live Vertex calls; a host-only ADC login is not mounted into
the container automatically.

The app configuration fails safely when required settings are missing or malformed. Its container readiness command checks both application configuration and PostgreSQL connectivity; the sidebar distinguishes an unavailable database from an invalid application configuration.
Configuration tests intentionally read only monkeypatched process environment variables, not a developer's local `.env`, so missing-setting assertions remain reproducible.

## Current design decisions

- The app will use Streamlit, PostgreSQL, Docker, Gemini through the Google GenAI SDK with Vertex AI authentication, and Langfuse.
- Synthetic rows are generated and validated locally from Gemini structured generation profiles; this keeps 1,000-row datasets reproducible and constraint-safe.
- Generated datasets are versioned. Table edits create a validated new version rather than mutating the active version in place.
- Talk-to-your-data executes only validated read-only queries against the user-selected dataset version.
- Uploaded DDL is parsed but never executed. The supported PostgreSQL subset is scalar columns; `NOT NULL`, `DEFAULT`, `CHECK`, `UNIQUE`, primary keys, and foreign keys (including composite keys and supplied actions/deferrability). Unsupported SQL and ambiguous schemas fail with source-aware errors.
- Foreign keys must point to a primary or unique key in the same upload and have compatible normalized types. Cycles are accepted only when every participating reference is nullable or `DEFERRABLE`; the accepted strategy is preserved in the schema metadata.
- Generation uses a seeded local random generator and validates every record before persistence. It supports 1,000 rows per table by default and validated per-table overrides from 1 to 10,000 rows. Gemini may propose a JSON generation profile through Vertex AI, but invalid/unavailable responses fall back to a local profile; Gemini never generates the individual records.
- The generation report records requested/generated counts, seed, fallback status, warnings, validation result, model identifier, and sanitized prompt metadata (instruction length and table count only).
- `app.persistence.DatasetStore` stores each validated dataset version in its own generated PostgreSQL schema, while keeping dataset/schema/version/request/validation/export metadata in application-owned tables. It never executes the uploaded DDL: it renders quoted identifiers and normalized supported types from the validated schema model.
- Persistence is transactional: local validation occurs first, PostgreSQL verifies materialized constraints before activation, and a failed attempt cannot replace an active version. Failed attempts retain diagnostic metadata without retained generated tables.
- CSV exports are UTF-8 and ZIP exports contain every version table plus `manifest.json`; both are scoped to a selected dataset/version (or that dataset's active version) and are audit logged.
- The Data Generation view accepts UTF-8 `.sql`, `.ddl`, and `.txt` files, shows source-aware parser errors, and only generates after **Generate** is pressed. It offers text instructions, a Gemini profile-planning temperature from 0.0 to 1.0, an optional non-negative seed, and per-table row counts from 1 to 10,000. Previews and downloads are always reloaded from the persisted active dataset version; session state retains only dataset/version identifiers.
- Table edits require the active dataset/version, an explicitly selected target table, and a Gemini JSON edit proposal that the backend validates and the user confirms. Only non-key/non-FK columns can be changed: matching values may be regenerated, text generators may receive a `text_prefix`, and nullable columns may receive a bounded `null_probability`. Unsafe relational mutations are rejected; successful edits fully validate and activate a new immutable version with parent-version lineage and redacted telemetry metadata.
- Talk to your data accepts a question for an explicitly selected persisted dataset/version. Gemini returns a strict JSON plan, but local PostgreSQL AST validation permits only one selected-version `SELECT`/`WITH ... SELECT`, allowlisted read-only functions, and unqualified uploaded table names. Multiple statements, DDL/DML, data-changing CTEs, namespace escapes, unsafe functions, locks, `SELECT INTO`, and unresolved parameters are rejected before execution.
- Query execution uses the dedicated `data_generation_query` NOLOGIN role with only `USAGE`/`SELECT` grants on generated version schemas, a read-only transaction, version-specific search path, three-second statement timeout, a 500-row result cap, and a one-megabyte serialized response cap. The query role is created during metadata initialization; if the deployment login cannot establish it, querying fails closed. The UI shows the validated SQL, result table, concise explanation, and only bar/line/scatter charts whose columns are present in returned results.
- The bundled sample files currently contain MySQL-only syntax such as `AUTO_INCREMENT`, `ENUM`, and `DATETIME`; they are intentionally rejected until replaced with PostgreSQL equivalents.
- Generation, edit planning/execution, exports, and queries write correlation-aware JSON logs and optional Langfuse traces containing operational metadata (latency, model when used, validation outcome, and dataset/version IDs). Prompts, SQL, raw generated data, query result values, and credentials are redacted by default. Langfuse activates only when both keys are configured and never blocks a workflow if it is unavailable. Set `OBSERVABILITY_CAPTURE_CONTENT=true` only for explicit local debugging; credentials remain redacted.
- `tests/fixtures/library_mgm_postgresql.sql` is a PostgreSQL-compatible adaptation of the supplied library-management sample, used by the clean Compose/Playwright workflow test. The original bundled sample files still use MySQL-only constructs and remain intentionally rejected. The E2E Compose override alone sets `DETERMINISTIC_TEST_MODE=true`, which returns locally generated, schema-validated structured profiles/plans and makes no Gemini call; keep it `false` for normal application use. The test uses an ephemeral localhost port, tears down a stale run of its dedicated Compose project before startup, and removes its containers and volume afterwards.

See [the implementation plan](reqs/PLAN.md) for the supported PostgreSQL DDL subset and the full verification criteria.

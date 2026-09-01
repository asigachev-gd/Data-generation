# Data-generation

## Project documentation

- [Requirements](reqs/REQUIREMENTS.md)
- [Implementation plan](reqs/PLAN.md) — implementation scope, milestone gates, supported DDL boundary, dataset versioning, and safe query policy.

## Current status

Steps 1 through 4 are implemented: the repository contains the Streamlit application scaffold, validated environment configuration, Docker Compose local stack, database/app readiness checks, an AST-based PostgreSQL DDL parser, deterministic constraint-safe synthetic data generation, and transactional PostgreSQL dataset persistence with exports. The UI intentionally shows placeholders until the later workflow steps are completed.

## Local development

1. Install Python 3.12 and Docker Desktop, then copy `.env.example` to `.env` and replace its placeholders. Authenticate Vertex AI with Application Default Credentials on the host; do not add service-account keys to this repository. Compose can parse without `.env`, but needs `POSTGRES_PASSWORD` and valid app configuration to start a ready service.
2. Start the local stack with `docker compose up --build`. Streamlit is available at `http://localhost:8501`; PostgreSQL data persists in the `postgres_data` Docker volume.
3. Install development dependencies with `python -m pip install -e '.[dev]'`, then run `make lint`, `make test-unit`, and `make test-integration` (or `make verify` for all checks). The integration marker starts its own PostgreSQL container and requires Docker. `uv sync --group dev` is also supported when uv is installed.

The app configuration fails safely when required settings are missing or malformed. Its container readiness command checks both application configuration and PostgreSQL connectivity; the sidebar distinguishes an unavailable database from an invalid application configuration.

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
- The bundled sample files currently contain MySQL-only syntax such as `AUTO_INCREMENT`, `ENUM`, and `DATETIME`; they are intentionally rejected until replaced with PostgreSQL equivalents.

See [the implementation plan](reqs/PLAN.md) for the supported PostgreSQL DDL subset and the full verification criteria.

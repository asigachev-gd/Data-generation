# Data-generation

## Project documentation

- [Requirements](reqs/REQUIREMENTS.md)
- [Implementation plan](reqs/PLAN.md) — implementation scope, milestone gates, supported DDL boundary, dataset versioning, and safe query policy.

## Current status

Step 1 is implemented: the repository contains the Streamlit application scaffold, validated environment configuration, Docker Compose local stack, database/app readiness checks, and initial unit/integration test commands. The UI intentionally shows placeholders until the later workflow steps are completed.

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

See [the implementation plan](reqs/PLAN.md) for the supported PostgreSQL DDL subset and the full verification criteria.

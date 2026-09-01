# Implementation Plan

## Scope and decisions

This plan implements `reqs/REQUIREMENTS.md` for PostgreSQL DDL schemas of up to seven related tables. The application is a Python/Streamlit service, with PostgreSQL as the persistent store and Gemini 2.0 Flash or a newer Gemini model accessed through the Google GenAI SDK using Vertex AI authentication.

The implementation must make these boundaries explicit:

- The first release supports a documented PostgreSQL `CREATE TABLE` subset: scalar columns, `NOT NULL`, `DEFAULT`, `CHECK`, `UNIQUE`, single- and composite-column primary keys, and single- and composite-column foreign keys. Unsupported executable SQL, views, triggers, procedures, domains, partitioning, and ambiguous DDL are rejected with actionable errors rather than partially interpreted.
- Cyclic foreign-key graphs are supported only when their constraints are nullable or deferrable; otherwise generation is rejected with an explanation. The accepted strategy is recorded with the dataset metadata.
- Gemini produces structured generation profiles, edit plans, SQL/query presentation plans, and streamed user-facing explanations where useful. Deterministic local code generates and validates individual rows; the model must not be relied on to emit thousands of unconstrained database rows.
- A generated dataset is immutable by version. An edit creates a new version atomically; the UI shows the active version. This preserves an audit trail and prevents a failed edit from corrupting a queryable dataset.
- Talk-to-your-data permits a single read-only `SELECT` or `WITH ... SELECT` statement for the selected dataset only. The database role, SQL parser/validator, statement timeout, row limit, and transaction are all read-only; prompt text is never treated as authority to bypass those controls.

## Step checklist

- [x] Step 1: Project scaffold, configuration, and local stack
- [x] Step 2: DDL parsing, validation, and schema model
- [x] Step 3: Structured generation planning and deterministic data generation
- [ ] Step 4: PostgreSQL persistence, versioning, and export
- [ ] Step 5: Data Generation UI
- [ ] Step 6: Bounded table-edit and regeneration workflow
- [ ] Step 7: Talk-to-your-data UI and safe query layer
- [ ] Step 8: Observability, deployment, and operational safeguards
- [ ] Step 9: Full end-to-end verification

Each step has a focused verification gate. Component, unit, and integration tests may run in earlier steps; browser-level end-to-end tests are reserved for Step 9.

---

## Step 1: Project scaffold, configuration, and local stack

### Objective

Create a reproducible Python/Streamlit project and Docker Compose development stack for the application and PostgreSQL.

### Implementation tasks

- Create application, service, database, UI, and test packages, with a single Streamlit entry point.
- Choose and lock supported Python, PostgreSQL, and dependency versions in `pyproject.toml` or `requirements.txt`.
- Configure Docker Compose for the app and PostgreSQL with persistent local database storage, health checks, and non-root application execution where practical.
- Provide `.env.example` only with variable names and safe placeholders. Required configuration includes `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, Vertex AI enablement, PostgreSQL connection settings, Gemini model name, and optional Langfuse credentials. Use Application Default Credentials; do not put service-account keys in the repository or image.
- Implement typed configuration validation at startup and a health endpoint/status check that distinguishes app readiness from database readiness.
- Add CI commands for formatting, linting, type checking if adopted, unit tests, and integration tests.

### Verification

- Configuration tests cover missing and malformed required settings without exposing secret values.
- Docker Compose validation and a smoke start confirm both services become healthy.
- A database integration fixture starts PostgreSQL without requiring a developer's existing database.

### Delivered

- Python 3.12, PostgreSQL 16, and pinned application/development dependencies are defined in `pyproject.toml`.
- `compose.yaml` provides an application container and health-checked PostgreSQL 16 service with a persistent named volume; the application runs as a non-root user.
- `.env.example` documents only safe placeholder configuration and Application Default Credentials usage; Compose can parse without a local `.env`, while a ready application still requires valid configuration.
- `app.config.Settings` validates typed configuration without rendering secrets; `app.health` reports application configuration and database readiness separately.
- `tests/` includes configuration/readiness coverage and a Docker-backed PostgreSQL integration fixture. `Makefile` provides `make lint`, `make test-unit`, `make test-integration`, and `make verify` after installing `.[dev]` (or use `uv sync --group dev` if uv is available).

---

## Step 2: DDL parsing, validation, and schema model

### Objective

Convert an uploaded `.sql`, `.ddl`, or `.txt` file containing supported PostgreSQL DDL into an unambiguous internal schema model.

### Implementation tasks

- Use a PostgreSQL-aware parser rather than regular expressions; retain source locations for validation errors.
- Parse quoted identifiers; scalar PostgreSQL types and their parameters; column and table-level `NOT NULL`, `DEFAULT`, `CHECK`, `UNIQUE`, primary-key, and foreign-key constraints; and foreign-key actions/deferrability where supplied.
- Represent the schema in typed models, including composite constraints, normalized types, nullable state, and a dependency graph.
- Validate duplicate identifiers, unknown FK targets, incompatible FK types, conflicting constraints, unsupported features, and cycles according to the scope rules above.
- Do not execute uploaded DDL as part of parsing. Preserve the validated canonical model and safe original text only as dataset metadata.

### Verification

- Unit tests cover each supported constraint, quoted names, composite primary/foreign keys, defaults, checks, and validation errors.
- Fixture tests include the supplied sample schemas when they are added to the repository and at least one five-to-seven-table schema.
- Integration tests verify dependency ordering and supported-cycle classification.

### Delivered

- `app.ddl.parse_schema` uses the PostgreSQL parser supplied by `pglast`; it only parses uploaded DDL and never executes it.
- `app.schema` provides immutable canonical models for scalar types, columns, composite constraints, foreign keys, source positions, dependency graph, and cycle strategy.
- Validation rejects unsupported statements/features, duplicate or unknown identifiers, invalid referenced keys, incompatible foreign-key types, and unsafe cycles with actionable source context.
- `tests/test_ddl.py` covers quoted identifiers, type parameters, defaults/checks, composite keys, foreign-key actions/deferrability, dependency order, validation failures, accepted cycles, and safe rejection of the bundled MySQL sample.

---

## Step 3: Structured generation planning and deterministic data generation

### Objective

Generate realistic, valid rows for every table while enforcing the schema model and user instructions.

### Implementation tasks

- Define a JSON schema for Gemini's generation profile: per-column semantic category, generator parameters, allowed null behavior, and instruction rationale. Validate every model response and fall back to type-based local profiles when it is unavailable or invalid.
- Request structured output through the Google GenAI SDK and record the model/version and sanitized prompt metadata. Use streaming only for progress/status text shown to the user; do not make persistence depend on an incomplete stream.
- Generate values locally in dependency order with a seeded random generator for reproducible test runs. Make row counts explicit: a default of 1,000 rows per table and a validated per-table override range.
- Enforce type bounds/precision, nullability, defaults, unique and primary-key constraints, evaluable checks, and foreign-key membership before persistence. Define a bounded retry budget and report unsatisfiable constraints instead of silently emitting invalid data.
- For supported cycles, use nullable/deferred references and validate the completed graph. Reject unsupported cycles before generation.
- Return a generation report containing requested/generated rows, fallback use, seed, warnings, and validation results.

### Verification

- Unit tests cover type generators, deterministic seeds, null/default handling, uniqueness collisions, check constraints, and invalid/partial structured model output.
- Integration tests cover parent/child and composite FK integrity, all supplied sample schemas, and a maximum-scope seven-table schema.
- Tests assert that all generated records pass the same validation routine used before persistence.

### Delivered

- `app.generation` defines a strict Gemini JSON generation-profile schema and validates complete table/column coverage before use. Unavailable or invalid model output falls back to local type/name-based profiles; only sanitized request metadata is retained.
- Rows are generated locally with an optional seed, explicit validated per-table row counts (default 1,000; range 1–10,000), bounded retries, scalar type bounds, nullability, primary/unique keys, supported evaluable checks, and single/composite foreign keys.
- All generated datasets pass `validate_dataset` before they can reach the persistence layer. The returned report includes counts, seed, fallback status, warnings, validation results, and model metadata.

---

## Step 4: PostgreSQL persistence, versioning, and export

### Objective

Persist valid dataset versions in PostgreSQL, retain their metadata, and provide safe CSV and ZIP downloads.

### Implementation tasks

- Create application metadata tables for dataset, schema, dataset version, table version, generation/edit request, validation report, and export audit data. Treat user identity/owner as optional until authentication is in scope; do not claim multi-user isolation before it exists.
- Materialize each validated dataset version in an isolated, generated schema or equivalent namespaced table mapping with quoted identifiers. Never apply untrusted uploaded DDL directly.
- Insert data transactionally, run database-side constraint validation, and mark a version active only after success. Roll back and retain diagnostic metadata on failure.
- Export selected active-version tables as UTF-8 CSV using safe response streaming; provide a ZIP containing one CSV per table and a manifest with dataset/version/schema information.
- Provide retrieval APIs that always require a dataset and version selection; default to the active version.

### Verification

- PostgreSQL integration tests cover rollback, persistence, version activation, metadata retrieval, and constraint enforcement.
- Export tests verify CSV escaping, ZIP validity, expected table files, and manifest contents.
- Tests confirm a failed generation or edit cannot replace the active dataset version.

---

## Step 5: Data Generation UI

### Objective

Implement the required Streamlit Data Generation workflow.

### Implementation tasks

- Add sidebar navigation for exactly the required primary views: **Data Generation** and **Talk to your data**.
- In Data Generation, accept `.sql`, `.ddl`, and `.txt` uploads; show parser errors with source context; offer an instruction field, temperature with a documented allowed range, optional seed, and row-count controls.
- Generate only after the user selects **Generate**. Stream progress/status text, then present the validation report, active dataset/version, paginated preview of every table, and CSV/ZIP download controls.
- Keep uploaded source and generated values out of browser/session state beyond what the UI needs; reload previews from persisted active versions.
- Include accessible error, empty, loading, and success states.

### Verification

- Component tests cover accepted/rejected uploads, required-field validation, controls, error rendering, and persisted preview selection.
- Service/UI integration tests cover upload through successful generation without a browser automation framework.

---

## Step 6: Bounded table-edit and regeneration workflow

### Objective

Allow useful natural-language changes without permitting unconstrained mutation of a relational dataset.

### Implementation tasks

- Define a JSON edit-plan schema: target table/columns, permitted operation (regenerate matching columns, change a supported generator parameter, or change a bounded value distribution), scope/filter, expected row-count effect, and explanation. Gemini returns this schema through function calling or structured output; the backend validates it against the active schema.
- The UI requires an active dataset/version and an explicit target table. It displays the validated proposed edit and asks for confirmation before execution.
- Apply an edit in a transaction to a new dataset version. Propagate only necessary dependent-table repairs, reject requests whose safe impact cannot be determined, and re-run full schema and database validation before activation.
- Record the original prompt, validated plan, model metadata, validation result, timestamps, and version lineage with sensitive content redacted in telemetry.

### Verification

- Unit tests cover invalid plans, disallowed target tables/columns, and model output that does not conform to the JSON schema.
- Integration tests cover successful scoped edits, FK-preserving dependent repairs, rejected unsafe edits, version lineage, and rollback.

---

## Step 7: Talk-to-your-data UI and safe query layer

### Objective

Let users query a selected generated dataset in natural language and receive a text answer, result table, or plot safely.

### Implementation tasks

- Add the Talk to your data view with dataset/version selection, a natural-language question field, streamed progress/explanation, generated SQL disclosure, result table, and chart rendering when a validated chart specification is returned.
- Supply Gemini with only the selected schema, allowed table mapping, current query policy, and the question. Request structured output containing one SQL statement, a concise explanation, and optional chart specification; validate before execution.
- Parse SQL with a PostgreSQL-aware AST validator. Permit only one `SELECT` or `WITH ... SELECT`; reject data-definition/manipulation, multiple statements, access outside the selected dataset namespace, dangerous functions, and unbounded result shapes.
- Execute with a dedicated read-only PostgreSQL role, read-only transaction, allowlisted search path, statement timeout, byte/row limits, and parameter binding where parameters are produced separately from SQL.
- Render only result-derived chart specifications with an allowlisted chart type and columns. Give useful errors for invalid requests without executing rejected SQL.

### Verification

- Unit tests cover structured response validation, SQL AST policy, prompt-injection-like questions, namespace escape attempts, multi-statements, and unsafe functions.
- PostgreSQL integration tests prove the read-only role cannot modify data and that timeouts/row limits apply.
- Tests cover tabular, textual, and aggregate chart-ready result handling.

---

## Step 8: Observability, deployment, and operational safeguards

### Objective

Make the complete service runnable in Docker, diagnosable, and safe for local demonstration use.

### Implementation tasks

- Instrument generation, edit, export, and query workflows with Langfuse traces, including model/version, latency, validation outcome, and dataset/version IDs. Redact secrets, credentials, raw generated values, and user prompts by default; permit enhanced content capture only through explicit local configuration.
- Configure structured application logs, correlation IDs, health/readiness checks, graceful shutdown, and error handling that returns actionable messages without internals or secrets.
- Finalize container configuration, migration/startup sequencing, local run instructions, and known supported/unsupported DDL behavior.
- Document how to configure Vertex AI Application Default Credentials, Gemini model selection, PostgreSQL, Langfuse, Docker startup, testing, and export behavior.

### Verification

- Tests verify configuration validation, trace initialization when enabled/disabled, telemetry redaction, health/readiness behavior, and database-unavailable handling.
- A Docker Compose smoke test exercises startup, migration, and a health check with no committed secrets.

---

## Step 9: Full end-to-end verification

### Objective

Validate the complete user journey in a clean Docker environment.

### Implementation tasks

- Add and document realistic supported fixtures, including at least one supplied sample DDL and a multi-table schema.
- Run browser automation against the Docker Compose stack using a configured test Gemini path or deterministic structured-output test double; a live-model smoke test is optional and clearly separated because it is nondeterministic and credential-dependent.
- Execute the workflow: upload DDL, supply instructions/parameters, generate, inspect each table, confirm a safe table edit, switch to the new active version, download CSV/ZIP, select the dataset in Talk to your data, and run a safe natural-language query that produces a table or plot.

### Verification and definition of done

- All formatting, lint, unit, integration, and browser end-to-end tests pass from a clean environment.
- PostgreSQL contains a validated, queryable active dataset version and exports are valid.
- The UI completes the required workflow without manual database intervention.
- Gemini integration uses Vertex AI through the Google GenAI SDK, and Langfuse records sanitized key events when enabled.

---

## Delivery checklist

Before final submission, confirm that:

- supported DDL behavior and unsupported constructs are documented and tested;
- all tests pass, including the clean-environment end-to-end suite;
- the app starts through Docker Compose;
- generated datasets are valid, versioned, exportable, and queryable in PostgreSQL;
- Gemini structured output/function calling and user-facing streaming are used where specified;
- Talk-to-your-data is enforced by both application SQL validation and a read-only database role; and
- Langfuse tracing is enabled when configured and does not expose secrets or raw user/data content by default.

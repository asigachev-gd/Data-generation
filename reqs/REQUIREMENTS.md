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

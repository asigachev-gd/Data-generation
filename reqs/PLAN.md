# Implementation Plan

This plan breaks the application into sequential milestones. Each step defines a concrete outcome and includes a verification phase with tests that must pass before moving on to the next phase.

## General working principles

- The project will use a Python-based application with a Streamlit UI.
- PostgreSQL will be the system of record for generated datasets and query execution.
- Gemini 2.0 Flash or newer will be used for schema interpretation, synthetic data generation, and natural language SQL generation.
- Langfuse will be enabled for tracing and observability.
- Every step ends with a verification gate: a focused automated test suite proving the implemented behavior.
- Only the final step will run full end-to-end tests.

---

## Step 1: Project scaffold and environment setup

### Objective
Set up the codebase structure, dependencies, container environment, and configuration needed to run the app locally and in Docker.

### Outcome
The repository contains a working project skeleton with:
- Python application entry points
- dependency management files
- Docker configuration for PostgreSQL and app services
- environment variables for Gemini, PostgreSQL, and Langfuse
- test folders and CI-ready structure

### Implementation tasks
- Create the application folder structure: app/, db/, services/, ui/, tests/
- Initialize dependency files: requirements.txt or pyproject.toml
- Configure Docker Compose with PostgreSQL and app containers
- Add `.env.example` with required variables
- Add health-check scripts and simple startup commands

### Verification
- Unit test: project scaffold loads without missing required config files.
- Unit test: Docker Compose file contains PostgreSQL and app services.
- Unit test: required environment variables are declared.

### Test examples
- `tests/test_scaffold.py`
  - asserts that `docker-compose.yml` exists
  - asserts that required directories exist
  - asserts that environment template file contains keys for `POSTGRES_*`, `GEMINI_*`, and `LANGFUSE_*`

---

## Step 2: DDL parsing and schema model

### Objective
Build a parser that reads SQL/DDL files and extracts all structural information needed for synthetic data generation and SQL querying.

### Outcome
The parser successfully converts each DDL file into an internal schema model containing:
- table names
- column names
- data types
- nullability
- primary keys
- foreign keys
- unique constraints
- table relationships

### Implementation tasks
- Parse `CREATE TABLE` statements from `.sql`, `.ddl`, and `.txt` inputs
- Map raw SQL types to normalized data types
- Detect column constraints such as `NOT NULL`, `UNIQUE`, `DEFAULT`, `CHECK`
- Build a schema graph for foreign key relationships
- Validate that uploaded files produce a complete and usable schema

### Verification
- Unit tests for parsing valid DDL files
- Unit tests for rejecting malformed schema files
- Integration test for schema graph construction

### Test examples
- `tests/test_ddl_parser.py`
  - asserts that table names are extracted correctly
  - asserts that primary and foreign keys are identified
  - asserts that nullable columns are marked correctly
  - asserts that a malformed DDL file raises a validation error

---

## Step 3: Data generation engine and referential integrity

### Objective
Create the core synthetic data generation engine that generates realistic rows respecting the DDL schema and relational constraints.

### Outcome
The system can generate valid synthetic data for tables with correct:
- column typing
- null handling
- date/time formatting
- numeric ranges
- FK constraints
- row-count configuration

### Implementation tasks
- Create a generator service that iterates through schema tables in dependency order
- Generate parent tables before child tables
- Use foreign key references to keep child rows consistent
- Support a configurable number of rows per table
- Respect null probability and domain-specific generation instructions
- Support prompt-based generation guidance from the user

### Verification
- Unit tests for per-column generation logic
- Integration tests for foreign key integrity across tables
- Regression tests for null and date generation behavior

### Test examples
- `tests/test_data_generator.py`
  - asserts that generated rows match declared column types
  - asserts that no foreign key references missing parent rows exist
  - asserts that row count matches requested target
  - asserts that generated dates use expected formatting

---

## Step 4: PostgreSQL persistence, export, and storage layer

### Objective
Store generated datasets in PostgreSQL and provide downloadable export mechanisms.

### Outcome
The application can:
- create tables in PostgreSQL from the schema model
- insert generated rows
- persist generation metadata
- provide CSV export per table
- package datasets as ZIP archives
- make data available for later query use in the talk-to-data interface

### Implementation tasks
- Create database connection and migration logic
- Build schema creation from parsed DDL metadata
- Insert generated records with transaction handling
- Add export service for CSV and ZIP files
- Add metadata table for dataset name, creation time, owner, and schema version

### Verification
- Integration tests against a temporary PostgreSQL database
- Test for export file creation and archive validity
- Test for dataset retrieval for later use

### Test examples
- `tests/test_db_storage.py`
  - asserts that schema tables are created successfully
  - asserts that generated rows are inserted without errors
  - asserts that CSV export creates a valid file
  - asserts that ZIP archive contains data files for each table

---

## Step 5: Data Generation UI and user interaction flow

### Objective
Implement the main Data Generation tab in the UI so a user can upload a schema, add instructions, set generation parameters, trigger generation, and preview results.

### Outcome
The Data Generation page supports:
- schema upload from `.sql`, `.ddl`, or `.txt`
- prompt text input
- generation parameter controls such as temperature and row count
- generate button action
- preview for each generated table
- per-table edit prompt and submit flow

### Implementation tasks
- Build sidebar navigation with Data Generation and Talk to your data tabs
- Create upload widget and validation messages
- Connect UI inputs to generation service
- Render preview tables in the UI
- Add per-table edit controls that send a modification prompt
- Display success/error states after generation and change requests

### Verification
- UI component tests for the generation tab
- E2E-style flow tests for upload, generation, and preview rendering
- Validation tests for form inputs

### Test examples
- `tests/test_generation_ui.py`
  - asserts that schema upload accepts a valid file
  - asserts that invalid file types are rejected
  - asserts that Generate button triggers data creation
  - asserts that table preview appears after generation

---

## Step 6: Table editing and regeneration workflow

### Objective
Allow users to modify generated tables through natural-language instructions and re-render the dataset accordingly.

### Outcome
The user can issue prompt-based adjustments for a specific table, and the system updates the table while preserving schema integrity and relational consistency.

### Implementation tasks
- Build an edit endpoint or function that accepts table name and textual instruction
- Apply modification logic to rows or value distributions in a controlled way
- Recheck generated data for FK consistency after edits
- Refresh the preview UI with the updated table
- Record generation history for each edit action

### Verification
- Unit tests for prompt-to-change translation logic
- Integration tests for table-specific updates
- Regression test ensuring FK validity after edits

### Test examples
- `tests/test_table_editing.py`
  - asserts that a table-specific prompt updates the intended table
  - asserts that row count remains valid after edits
  - asserts that foreign key relations remain valid
  - asserts that the UI preview refreshes with updated data

---

## Step 7: Talk-to-your-data query layer

### Objective
Implement the conversational SQL querying engine that accepts natural language questions and turns them into database queries and visual output.

### Outcome
The app can:
- accept a natural-language user question
- map it to a valid SQL query using the schema context
- execute the query against PostgreSQL
- return text, tables, or chart-based results
- provide explanations for the query logic when required

### Implementation tasks
- Build a schema-aware SQL generation prompt using Gemini
- Validate generated SQL before execution
- Add safe execution environment and query restrictions
- Convert result sets into UI-friendly structures
- Support chart-ready output for aggregate data
- Add error handling for invalid or unsafe SQL generation

### Verification
- Unit tests for SQL generation from natural language prompts
- Unit tests for SQL validation and blocking unsafe queries
- Integration tests for query execution and formatting of results

### Test examples
- `tests/test_nl_to_sql.py`
  - asserts that natural language prompts generate valid SQL
  - asserts that invalid or unsafe SQL is rejected
  - asserts that result rows are returned in expected format
  - asserts that chart data is created for aggregate visualizations

---

## Step 8: Observability, deployment, and production readiness

### Objective
Ensure the system is observable, portable, and safe to run in a standard development environment.

### Outcome
The app includes:
- Langfuse tracing for generation and query flows
- Dockerized deployment configuration
- application health checks
- logs and error tracking
- stable startup and teardown for local usage

### Implementation tasks
- Add Langfuse client configuration and trace decorators
- Add startup health checks and logging across critical services
- Ensure Docker containers are configured for the app and Postgres
- Add environment validation on boot
- Document run instructions and expected outputs

### Verification
- Unit tests for environment validation and config load
- Integration tests for health endpoints and container startup
- Smoke test for tracing initialization

### Test examples
- `tests/test_observability.py`
  - asserts that Langfuse is initialized when enabled
  - asserts that health endpoint returns success status
  - asserts that logs capture generation and query errors

---

## Step 9: Full end-to-end verification of the application

### Objective
Verify that the complete app works as a single integrated system from user input to database output.

### Outcome
The whole application is validated end-to-end with a success path that includes:
- schema upload
- generation from DDL
- preview of generated data
- prompt-based table modification
- storage in PostgreSQL
- natural-language querying
- result presentation in table or chart format
- CSV/ZIP export

### Implementation tasks
- Prepare realistic sample DDL files for the supported schemas
- Run the full application stack using Docker Compose
- Perform complete user workflow in a headless browser or UI automation test
- Validate export and persistence results
- Validate natural language SQL query behavior on real data

### Verification
Only full end-to-end tests are run in this step.

### Test examples
- `tests/e2e/test_full_app_flow.py`
  - uploads a valid DDL schema
  - enters a generation prompt and sets parameters
  - clicks Generate and verifies table previews appear
  - edits one table with a natural-language instruction and verifies the update
  - queries the database using a natural-language question and verifies a result table/chart appears
  - exports generated data and verifies the file is created
  - confirms the generated dataset is available in the talk-to-data view

### Definition of done
The app is considered complete only if all end-to-end checks pass in a clean environment and the user can complete the full workflow without manual database fixes or broken UI steps.

---

## Delivery checklist

Before final submission, confirm that:
- all unit and integration tests for prior steps pass
- all end-to-end tests for the full workflow pass
- the app runs via Docker
- PostgreSQL contains generated and queryable data
- Gemini integration works for both generation and natural-language SQL tasks
- Langfuse tracing records key events
- exported CSV and ZIP files are downloadable and valid

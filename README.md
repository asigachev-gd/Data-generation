# Data-generation

## Project documentation

- [Requirements](reqs/REQUIREMENTS.md)
- [Implementation plan](reqs/PLAN.md) — implementation scope, milestone gates, supported DDL boundary, dataset versioning, and safe query policy.

## Current planning decisions

- The app will use Streamlit, PostgreSQL, Docker, Gemini through the Google GenAI SDK with Vertex AI authentication, and Langfuse.
- Synthetic rows are generated and validated locally from Gemini structured generation profiles; this keeps 1,000-row datasets reproducible and constraint-safe.
- Generated datasets are versioned. Table edits create a validated new version rather than mutating the active version in place.
- Talk-to-your-data executes only validated read-only queries against the user-selected dataset version.

See [the implementation plan](reqs/PLAN.md) for the supported PostgreSQL DDL subset and the full verification criteria.

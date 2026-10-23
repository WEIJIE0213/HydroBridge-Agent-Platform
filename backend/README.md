# HydroBridge AI Backend

FastAPI, LangGraph, PostgreSQL, Redis, Elasticsearch, MinIO and Celery backend
for the HydroBridge evidence-grounded agent platform. One bounded Agent loop
dispatches actions into three compiled LangGraph subgraphs: achievement discovery
and evaluation, requirement analysis and matching, and retrieval/question answering.
Every subgraph returns an observation to the Agent before it decides to continue
or finish. Individual action results are persisted before the next decision.

History is managed with incremental summaries, a budgeted recent-message window,
and separate authoritative requirement/scope state. See the repository README
for the `CONTEXT_*` settings and `docs/ARCHITECTURE.md` for checkpoint/version behavior.

The application is designed to run through the repository-level Docker Compose
configuration. Unit tests use isolated in-memory or SQLite-backed adapters;
the separate infrastructure smoke command checks actual PostgreSQL,
Elasticsearch and checkpoint persistence (see the repository README).

# HydroBridge AI Backend

FastAPI, LangGraph, PostgreSQL, Redis, Elasticsearch, MinIO and Celery backend
for the HydroBridge evidence-grounded agent platform. A single agent chooses
among five tools; matching and research use fixed server-side workflows.

The application is designed to run through the repository-level Docker Compose
configuration. Unit tests use isolated in-memory or SQLite-backed adapters;
the separate infrastructure smoke command checks actual PostgreSQL,
Elasticsearch and checkpoint persistence (see the repository README).

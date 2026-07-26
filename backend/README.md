# HydroBridge AI Backend

FastAPI, LangGraph, MySQL, Redis, Qdrant, MinIO and Celery backend for the
HydroBridge AI multi-agent platform.

The application is designed to run through the repository-level Docker Compose
configuration. Unit tests use isolated in-memory or SQLite-backed adapters;
integration tests run against the same service types used by the application.

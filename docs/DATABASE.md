# Database operations

## Local SQLite

SQLite is the zero-dependency demo and unit-test database. `src.app` creates its tables automatically.

## PostgreSQL

Alembic is the schema contract:

```bash
set DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/workcore
alembic upgrade head
python -m src.seed
```

Do not use application startup as a production migration runner. In Docker Compose, the API command runs migrations and seed data with `MIGRATION_DATABASE_URL`, then starts Uvicorn with the restricted `workcore_app` connection.

Render the exact SQL without connecting:

```bash
alembic upgrade head --sql
```

## Roles and RLS

- Migration/seed role: owns DDL and reference-data setup.
- `workcore_app`: no RLS bypass. It can read `tenants` for API-key authentication; all operational tables require `app.tenant_id`.
- `workcore_worker`: `BYPASSRLS` service role because it claims work across tenants. Every claimed job still carries immutable `tenant_id`.

After authentication, `Database.set_tenant_context()` calls transaction-local `set_config`. A SQLAlchemy `after_begin` hook restores that value after each commit. Transaction-local state prevents pooled connections from leaking one tenant into the next request.

`migrations/versions/0002_tenant_rls.py` enables and forces RLS on:

- vendors
- units
- jobs
- executions
- work_orders
- tool_invocations
- llm_calls

## PostgreSQL-only proof

SQLite cannot prove `FOR UPDATE SKIP LOCKED` or RLS. The opt-in integration suite does:

```bash
set POSTGRES_TEST_ADMIN_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/workcore
set POSTGRES_TEST_APP_URL=postgresql+asyncpg://workcore_app:workcore_app@localhost:5432/workcore
pytest tests/test_postgres.py -v
```

GitHub Actions starts PostgreSQL 15, migrates it, creates restricted roles, then runs these tests.
The PostgreSQL image includes pgvector; the same suite also executes a tenant-scoped vector search.

## Existing Docker volume

The role initialization script only runs for a fresh Postgres volume. If this demo used an older schema:

```bash
docker compose down -v
docker compose up --build
```

Removing the volume deletes local demo data.

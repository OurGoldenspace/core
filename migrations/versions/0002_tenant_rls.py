"""Enforce tenant isolation with PostgreSQL row-level security.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "vendors",
    "departments",
    "jobs",
    "executions",
    "payments",
    "tool_invocations",
    "llm_calls",
)


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::INTEGER
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::INTEGER
            )
            """
        )

    # Roles are provisioned by infrastructure, not migrations. Conditional
    # grants make the migration portable to managed Postgres.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regrole('workcore_app') IS NOT NULL THEN
                GRANT USAGE ON SCHEMA public TO workcore_app;
                GRANT SELECT ON tenants TO workcore_app;
                GRANT SELECT, INSERT, UPDATE ON
                    vendors, departments, jobs, executions, payments,
                    tool_invocations, llm_calls
                TO workcore_app;
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO workcore_app;
            END IF;

            IF to_regrole('workcore_worker') IS NOT NULL THEN
                GRANT USAGE ON SCHEMA public TO workcore_worker;
                GRANT SELECT ON tenants TO workcore_worker;
                GRANT SELECT, INSERT, UPDATE ON
                    vendors, departments, jobs, executions, payments,
                    tool_invocations, llm_calls
                TO workcore_worker;
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO workcore_worker;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

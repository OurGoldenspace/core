"""Initial durable maintenance-agent runtime.

Revision ID: 0001
Revises: None
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("api_key", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_table(
        "vendors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("vendor_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("risk_level", sa.String(50)),
        sa.Column("credit_limit", sa.Numeric(12, 2)),
        sa.Column("ytd_spent", sa.Numeric(12, 2), server_default="0"),
        sa.Column("country", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("tenant_id", "vendor_id"),
    )
    op.create_table(
        "units",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("property_name", sa.String(255)),
        sa.Column("budget_annual", sa.Numeric(12, 2)),
        sa.Column("budget_spent", sa.Numeric(12, 2), server_default="0"),
        sa.Column("budget_available", sa.Numeric(12, 2)),
        sa.Column("approval_threshold", sa.Numeric(12, 2)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("tenant_id", "unit_id"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("request_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("claimed_by", sa.Integer()),
        sa.Column("claimed_at", sa.DateTime()),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("available_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            "status IN ('pending','claimed','awaiting_review','completed','failed','dead_letter')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_jobs_retry_count"),
        sa.UniqueConstraint("tenant_id", "request_id"),
    )
    op.create_index("idx_jobs_status_created", "jobs", ["tenant_id", "status", "created_at"])
    op.create_table(
        "executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("request_id", sa.String(255), nullable=False),
        sa.Column("vendor_id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reported_date", sa.Date(), nullable=False),
        sa.Column("state", sa.String(50), nullable=False, server_default="running"),
        sa.Column("decision", sa.String(50)),
        sa.Column("reason", sa.Text()),
        sa.Column("iterations", sa.Integer()),
        sa.Column("tokens_used", sa.Integer()),
        sa.Column("started_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("duration_ms", sa.Integer()),
        sa.CheckConstraint(
            "state IN ('running','awaiting_review','reviewing','completed','failed')",
            name="ck_executions_state",
        ),
        sa.CheckConstraint("amount > 0", name="ck_executions_amount"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index(
        "idx_executions_tenant_created",
        "executions",
        ["tenant_id", sa.text("started_at DESC")],
    )
    op.create_table(
        "work_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "execution_id",
            sa.Integer(),
            sa.ForeignKey("executions.id"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(255), nullable=False),
        sa.Column("vendor_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("transaction_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("amount > 0", name="ck_work_orders_amount"),
        sa.CheckConstraint("status IN ('pending','succeeded','failed')", name="ck_work_orders_status"),
        sa.UniqueConstraint("tenant_id", "request_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
    )
    op.create_index("idx_work_orders_execution", "work_orders", ["execution_id"])
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "execution_id",
            sa.Integer(),
            sa.ForeignKey("executions.id"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("tool_input", sa.Text()),
        sa.Column("tool_result", sa.Text()),
        sa.Column("validation_error", sa.Text()),
        sa.Column("iteration_number", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index(
        "idx_tool_invocations_execution",
        "tool_invocations",
        ["execution_id"],
    )
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "execution_id",
            sa.Integer(),
            sa.ForeignKey("executions.id"),
            nullable=False,
        ),
        sa.Column("model", sa.String(100)),
        sa.Column("max_tokens", sa.Integer()),
        sa.Column("temperature", sa.Numeric(3, 2)),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("stop_reason", sa.String(100)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_llm_calls_execution", "llm_calls", ["execution_id"])


def downgrade() -> None:
    op.drop_table("llm_calls")
    op.drop_table("tool_invocations")
    op.drop_table("work_orders")
    op.drop_table("executions")
    op.drop_table("jobs")
    op.drop_table("units")
    op.drop_table("vendors")
    op.drop_table("tenants")

"""Add tenant-scoped pgvector retrieval.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE document_chunks (
            id SERIAL PRIMARY KEY,
            tenant_id INTEGER NOT NULL REFERENCES tenants(id),
            source_id VARCHAR(255) NOT NULL,
            chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
            content TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            embedding vector(64) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tenant_id, source_id, chunk_index)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_document_chunks_source
        ON document_chunks (tenant_id, source_id)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_document_chunks_embedding
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        """
    )
    op.execute("ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_chunks FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY document_chunks_tenant_isolation ON document_chunks
        USING (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::INTEGER
        )
        WITH CHECK (
            tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::INTEGER
        )
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regrole('workcore_app') IS NOT NULL THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON document_chunks TO workcore_app;
                GRANT USAGE, SELECT ON SEQUENCE document_chunks_id_seq TO workcore_app;
            END IF;
            IF to_regrole('workcore_worker') IS NOT NULL THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON document_chunks TO workcore_worker;
                GRANT USAGE, SELECT ON SEQUENCE document_chunks_id_seq TO workcore_worker;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("document_chunks")

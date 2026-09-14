-- WorkCore invoice agent schema (PostgreSQL).
-- Local SQLite uses the same tables via init_db() in database.py.

CREATE TABLE IF NOT EXISTS tenants (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    api_key VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vendors (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    vendor_id INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    is_approved BOOLEAN NOT NULL DEFAULT TRUE,
    risk_level VARCHAR(50),
    credit_limit DECIMAL(12, 2),
    ytd_spent DECIMAL(12, 2) DEFAULT 0,
    country VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, vendor_id)
);

CREATE TABLE IF NOT EXISTS departments (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    dept_id INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    budget_annual DECIMAL(12, 2),
    budget_spent DECIMAL(12, 2) DEFAULT 0,
    budget_available DECIMAL(12, 2),
    approval_threshold DECIMAL(12, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, dept_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    invoice_id VARCHAR(255) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    claimed_by INTEGER,
    claimed_at TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    last_error TEXT,
    available_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, invoice_id)
);

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS available_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_jobs_status_created
    ON jobs (tenant_id, status, created_at);

CREATE TABLE IF NOT EXISTS executions (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    job_id INTEGER REFERENCES jobs(id),
    idempotency_key VARCHAR(255),
    invoice_id VARCHAR(255) NOT NULL,
    vendor_id INTEGER,
    department_id INTEGER,
    amount DECIMAL(12, 2),
    invoice_date DATE,
    state VARCHAR(50) NOT NULL DEFAULT 'running',
    decision VARCHAR(50),
    reason TEXT,
    iterations INTEGER,
    tokens_used INTEGER,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INTEGER,
    UNIQUE (tenant_id, idempotency_key),
    UNIQUE (job_id)
);

ALTER TABLE executions ADD COLUMN IF NOT EXISTS invoice_date DATE;
ALTER TABLE executions ADD COLUMN IF NOT EXISTS state VARCHAR(50) NOT NULL DEFAULT 'running';

CREATE UNIQUE INDEX IF NOT EXISTS idx_executions_job_unique
    ON executions (job_id)
    WHERE job_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    execution_id INTEGER NOT NULL REFERENCES executions(id),
    invoice_id VARCHAR(255) NOT NULL,
    vendor_id INTEGER NOT NULL,
    amount DECIMAL(12, 2) NOT NULL CHECK (amount > 0),
    idempotency_key VARCHAR(255) NOT NULL,
    transaction_id VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'succeeded',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, invoice_id),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_payments_execution
    ON payments (execution_id);

CREATE TABLE IF NOT EXISTS tool_invocations (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    execution_id INTEGER NOT NULL REFERENCES executions(id),
    tool_name VARCHAR(255) NOT NULL,
    tool_input TEXT,
    tool_result TEXT,
    validation_error TEXT,
    iteration_number INTEGER,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id SERIAL PRIMARY KEY,
    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
    execution_id INTEGER NOT NULL REFERENCES executions(id),
    model VARCHAR(100),
    max_tokens INTEGER,
    temperature DECIMAL(3, 2),
    completion_tokens INTEGER,
    prompt_tokens INTEGER,
    total_tokens INTEGER,
    stop_reason VARCHAR(100),
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_executions_tenant_created
    ON executions (tenant_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_tool_invocations_execution
    ON tool_invocations (execution_id);

CREATE INDEX IF NOT EXISTS idx_llm_calls_execution
    ON llm_calls (execution_id);

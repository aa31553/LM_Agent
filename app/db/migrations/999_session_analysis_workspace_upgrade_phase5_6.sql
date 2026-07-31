ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS error_code VARCHAR(64);

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS error_details_json JSONB;

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS execution_duration_ms BIGINT;

CREATE INDEX IF NOT EXISTS ix_analysis_jobs_error_code
    ON analysis_jobs (error_code);

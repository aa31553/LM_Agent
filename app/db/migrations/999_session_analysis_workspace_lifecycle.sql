ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS progress INT NOT NULL DEFAULT 0;

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS retry_of_job_id UUID
        REFERENCES analysis_jobs(id) ON DELETE SET NULL;

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMP WITHOUT TIME ZONE;

CREATE INDEX IF NOT EXISTS ix_analysis_jobs_retry_of_job_id
    ON analysis_jobs(retry_of_job_id);

CREATE TABLE IF NOT EXISTS analysis_files (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_type VARCHAR(32) NOT NULL,
    file_path TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    confidential_level VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ready',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_analysis_files_session_id
    ON analysis_files(session_id);
CREATE INDEX IF NOT EXISTS ix_analysis_files_expires_at
    ON analysis_files(expires_at);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    file_id UUID NOT NULL REFERENCES analysis_files(id) ON DELETE CASCADE,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    request_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_json JSONB,
    error_message TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_analysis_jobs_session_id
    ON analysis_jobs(session_id);
CREATE INDEX IF NOT EXISTS ix_analysis_jobs_file_id
    ON analysis_jobs(file_id);
CREATE INDEX IF NOT EXISTS ix_analysis_jobs_status
    ON analysis_jobs(status);

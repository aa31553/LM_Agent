ALTER TABLE analysis_files
    ADD COLUMN IF NOT EXISTS dataset_manifest JSONB;
ALTER TABLE analysis_files
    ADD COLUMN IF NOT EXISTS profile_progress INTEGER NOT NULL DEFAULT 0;
ALTER TABLE analysis_files
    ADD COLUMN IF NOT EXISTS profile_error TEXT;
ALTER TABLE analysis_files
    ADD COLUMN IF NOT EXISTS profiled_at TIMESTAMP WITHOUT TIME ZONE;

CREATE TABLE IF NOT EXISTS analysis_plan_drafts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    source_file_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    plan_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    warnings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'validated',
    error_message TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_analysis_plan_drafts_workspace_id
    ON analysis_plan_drafts(workspace_id);
CREATE INDEX IF NOT EXISTS ix_analysis_plan_drafts_session_id
    ON analysis_plan_drafts(session_id);

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS draft_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_analysis_jobs_draft_id'
    ) THEN
        ALTER TABLE analysis_jobs
            ADD CONSTRAINT fk_analysis_jobs_draft_id
            FOREIGN KEY (draft_id)
            REFERENCES analysis_plan_drafts(id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_analysis_jobs_draft_id
    ON analysis_jobs(draft_id);

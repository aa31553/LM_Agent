CREATE TABLE IF NOT EXISTS workspaces (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT,
    visibility VARCHAR(32) NOT NULL DEFAULT 'private',
    is_personal BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_workspaces_owner_user_id
    ON workspaces(owner_user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_workspaces_personal_owner
    ON workspaces(owner_user_id) WHERE is_personal = TRUE;

CREATE TABLE IF NOT EXISTS workspace_permissions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    subject_type VARCHAR(32) NOT NULL,
    subject_value TEXT NOT NULL,
    permission VARCHAR(32) NOT NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_workspace_permission_subject
        UNIQUE (workspace_id, subject_type, subject_value)
);

CREATE INDEX IF NOT EXISTS ix_workspace_permissions_workspace_id
    ON workspace_permissions(workspace_id);

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS workspace_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_chat_sessions_workspace_id'
    ) THEN
        ALTER TABLE chat_sessions
            ADD CONSTRAINT fk_chat_sessions_workspace_id
            FOREIGN KEY (workspace_id)
            REFERENCES workspaces(id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_chat_sessions_workspace_id
    ON chat_sessions(workspace_id);

INSERT INTO workspaces (
    id,
    owner_user_id,
    name,
    visibility,
    is_personal,
    is_active
)
SELECT
    uuid_generate_v4(),
    users.id,
    COALESCE(users.display_name, users.username, users.external_user_id)
        || ' Personal Workspace',
    'private',
    TRUE,
    TRUE
FROM users
WHERE NOT EXISTS (
    SELECT 1
    FROM workspaces
    WHERE workspaces.owner_user_id = users.id
      AND workspaces.is_personal = TRUE
);

UPDATE chat_sessions
SET workspace_id = workspaces.id
FROM workspaces
WHERE chat_sessions.user_id = workspaces.owner_user_id
  AND workspaces.is_personal = TRUE
  AND chat_sessions.workspace_id IS NULL;

ALTER TABLE analysis_files
    ADD COLUMN IF NOT EXISTS workspace_id UUID;
ALTER TABLE analysis_files
    ADD COLUMN IF NOT EXISTS profile_path TEXT;

UPDATE analysis_files
SET workspace_id = chat_sessions.workspace_id
FROM chat_sessions
WHERE analysis_files.session_id = chat_sessions.id
  AND analysis_files.workspace_id IS NULL;

ALTER TABLE analysis_files
    ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE analysis_files
    ALTER COLUMN session_id DROP NOT NULL;

ALTER TABLE analysis_files
    DROP CONSTRAINT IF EXISTS analysis_files_session_id_fkey;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_analysis_files_workspace_id'
    ) THEN
        ALTER TABLE analysis_files
            ADD CONSTRAINT fk_analysis_files_workspace_id
            FOREIGN KEY (workspace_id)
            REFERENCES workspaces(id)
            ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_analysis_files_session_id'
    ) THEN
        ALTER TABLE analysis_files
            ADD CONSTRAINT fk_analysis_files_session_id
            FOREIGN KEY (session_id)
            REFERENCES chat_sessions(id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_analysis_files_workspace_id
    ON analysis_files(workspace_id);

UPDATE analysis_files
SET expires_at = NULL;

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS workspace_id UUID;
ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS result_path TEXT;

UPDATE analysis_jobs
SET workspace_id = analysis_files.workspace_id
FROM analysis_files
WHERE analysis_jobs.file_id = analysis_files.id
  AND analysis_jobs.workspace_id IS NULL;

ALTER TABLE analysis_jobs
    ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE analysis_jobs
    ALTER COLUMN session_id DROP NOT NULL;

ALTER TABLE analysis_jobs
    DROP CONSTRAINT IF EXISTS analysis_jobs_session_id_fkey;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_analysis_jobs_workspace_id'
    ) THEN
        ALTER TABLE analysis_jobs
            ADD CONSTRAINT fk_analysis_jobs_workspace_id
            FOREIGN KEY (workspace_id)
            REFERENCES workspaces(id)
            ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_analysis_jobs_session_id'
    ) THEN
        ALTER TABLE analysis_jobs
            ADD CONSTRAINT fk_analysis_jobs_session_id
            FOREIGN KEY (session_id)
            REFERENCES chat_sessions(id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_analysis_jobs_workspace_id
    ON analysis_jobs(workspace_id);

CREATE TABLE IF NOT EXISTS analysis_artifacts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    file_id UUID REFERENCES analysis_files(id) ON DELETE CASCADE,
    job_id UUID REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    artifact_type VARCHAR(32) NOT NULL,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    mime_type VARCHAR(255),
    size_bytes BIGINT NOT NULL DEFAULT 0,
    artifact_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_analysis_artifacts_workspace_id
    ON analysis_artifacts(workspace_id);
CREATE INDEX IF NOT EXISTS ix_analysis_artifacts_file_id
    ON analysis_artifacts(file_id);
CREATE INDEX IF NOT EXISTS ix_analysis_artifacts_job_id
    ON analysis_artifacts(job_id);

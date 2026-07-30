ALTER TABLE analysis_plan_drafts
    ADD COLUMN IF NOT EXISTS recipe_id VARCHAR(64);

ALTER TABLE analysis_plan_drafts
    ADD COLUMN IF NOT EXISTS recipe_version VARCHAR(16);

ALTER TABLE analysis_plan_drafts
    ADD COLUMN IF NOT EXISTS compiler_version VARCHAR(16);

CREATE INDEX IF NOT EXISTS ix_analysis_plan_drafts_recipe_id
    ON analysis_plan_drafts (recipe_id);

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS plan_origin VARCHAR(32) NOT NULL DEFAULT 'direct';

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS recipe_id VARCHAR(64);

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS recipe_version VARCHAR(16);

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS compiler_version VARCHAR(16);

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS dataset_hashes_json JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS result_schema_json JSONB;

ALTER TABLE analysis_jobs
    ADD COLUMN IF NOT EXISTS result_hash VARCHAR(64);

CREATE INDEX IF NOT EXISTS ix_analysis_jobs_recipe_id
    ON analysis_jobs (recipe_id);

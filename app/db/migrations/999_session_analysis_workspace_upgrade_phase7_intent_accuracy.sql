ALTER TABLE analysis_plan_drafts
    ADD COLUMN IF NOT EXISTS clarification_json JSONB;

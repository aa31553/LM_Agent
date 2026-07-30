ALTER TABLE analysis_plan_drafts
    ADD COLUMN IF NOT EXISTS raw_llm_json JSONB;

ALTER TABLE analysis_plan_drafts
    ADD COLUMN IF NOT EXISTS normalized_intent_json JSONB;

ALTER TABLE analysis_plan_drafts
    ADD COLUMN IF NOT EXISTS normalization_actions_json JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE analysis_plan_drafts
    ADD COLUMN IF NOT EXISTS validation_errors_json JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE analysis_plan_drafts
    ADD COLUMN IF NOT EXISTS repair_attempted BOOLEAN NOT NULL DEFAULT FALSE;

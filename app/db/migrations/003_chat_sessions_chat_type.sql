ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS chat_type TEXT NOT NULL DEFAULT 'general';

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_type
    ON chat_sessions(user_id, chat_type);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS session_id UUID;
ALTER TABLE documents ALTER COLUMN knowledge_base_id DROP NOT NULL;
ALTER TABLE document_chunks ALTER COLUMN knowledge_base_id DROP NOT NULL;
ALTER TABLE document_images ALTER COLUMN knowledge_base_id DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_documents_session_id'
    ) THEN
        ALTER TABLE documents
            ADD CONSTRAINT fk_documents_session_id
            FOREIGN KEY (session_id) REFERENCES chat_sessions(id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_documents_exactly_one_scope'
    ) THEN
        ALTER TABLE documents
            ADD CONSTRAINT ck_documents_exactly_one_scope
            CHECK (
                (knowledge_base_id IS NOT NULL AND session_id IS NULL)
                OR (knowledge_base_id IS NULL AND session_id IS NOT NULL)
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_documents_session_id ON documents(session_id);

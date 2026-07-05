-- Run this on the lm_agent database as a PostgreSQL superuser before app.db.init_db
-- when the lm_agent application user cannot create extensions.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


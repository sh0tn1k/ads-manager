-- Phase 5: richer LLM cost accounting.
ALTER TABLE llm_calls ADD COLUMN cache_write_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llm_calls ADD COLUMN web_search_requests INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llm_calls ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1;
ALTER TABLE llm_calls ADD COLUMN error TEXT;

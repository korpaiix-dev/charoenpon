-- Content Distribution System (2026-06-02)
-- See task #122/#123 — content_distributor.py + topic-aware routing
ALTER TYPE groupslug ADD VALUE IF NOT EXISTS "STORAGE";

CREATE TABLE IF NOT EXISTS content_distribution_queue (
    id BIGSERIAL PRIMARY KEY,
    source_chat_id BIGINT NOT NULL,
    source_msg_id BIGINT NOT NULL,
    media_type VARCHAR(20) NOT NULL,
    file_id VARCHAR(512),
    media_group_id VARCHAR(64),
    caption TEXT,
    tags TEXT[] NOT NULL DEFAULT "{}",
    min_tier VARCHAR(20) NOT NULL DEFAULT "TIER_300",
    captured_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_archived BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (source_chat_id, source_msg_id)
);
CREATE INDEX IF NOT EXISTS ix_cdq_min_tier ON content_distribution_queue(min_tier);
CREATE INDEX IF NOT EXISTS ix_cdq_captured_at ON content_distribution_queue(captured_at);
CREATE INDEX IF NOT EXISTS ix_cdq_archived ON content_distribution_queue(is_archived);

CREATE TABLE IF NOT EXISTS distribution_log (
    id BIGSERIAL PRIMARY KEY,
    content_id BIGINT NOT NULL REFERENCES content_distribution_queue(id) ON DELETE CASCADE,
    target_chat_id BIGINT NOT NULL,
    target_slug VARCHAR(20),
    posted_at TIMESTAMP NOT NULL DEFAULT NOW(),
    success BOOLEAN NOT NULL,
    target_msg_id BIGINT,
    error_msg TEXT,
    UNIQUE (content_id, target_chat_id)
);

CREATE TABLE IF NOT EXISTS distribution_config (
    key VARCHAR(64) PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS group_topic_routes (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    tag VARCHAR(40) NOT NULL,
    topic_id BIGINT NOT NULL,
    topic_name VARCHAR(255),
    set_by BIGINT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (chat_id, tag)
);

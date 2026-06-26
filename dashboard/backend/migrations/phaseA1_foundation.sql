-- ====================================================================
-- Phase A.1 Foundation Migration
-- Date: 2026-06-26
-- Purpose: Foundation tables for Dashboard 2.0 handoff
-- Safety: All tables empty by default. NOTHING reads them until feature flag
--         enabled per surface. Production behavior 100% unchanged.
-- Rollback: DROP TABLE feature_flags, bot_messages, bot_message_versions,
--           bot_menu_buttons CASCADE;
-- ====================================================================

-- ========== 1. Feature Flags ==========
-- The kill switch. Every new feature MUST check this before activating.
-- If flag missing or OFF, code falls back to existing hardcoded behavior.

CREATE TABLE IF NOT EXISTS feature_flags (
    flag_key VARCHAR(64) PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    scope VARCHAR(32) NOT NULL DEFAULT 'all',
        -- 'all'     → everyone (when enabled=TRUE)
        -- 'admin'   → only admin telegram IDs
        -- 'canary'  → specific telegram IDs (see canary_user_ids)
    canary_user_ids BIGINT[],
    description TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_by BIGINT
);

-- Seed known flags (all OFF — preserves current behavior)
INSERT INTO feature_flags (flag_key, enabled, scope, description) VALUES
    ('bot_messages_enabled',    FALSE, 'all',   'Read /start, /packages, etc. text from bot_messages table (vs hardcoded)'),
    ('bot_menu_buttons_enabled', FALSE, 'all',   'Read main menu inline buttons from bot_menu_buttons table'),
    ('promo_wizard_enabled',    FALSE, 'all',   'Use Promo Wizard campaigns vs hardcoded Lucky/Flash/Birthday/Endmonth'),
    ('prae_knowledge_enabled',  FALSE, 'all',   'Prepend Q+A knowledge from prae_knowledge to Prae prompt'),
    ('prae_persona_enabled',    FALSE, 'all',   'Use DB-stored Prae persona block in system prompt')
ON CONFLICT (flag_key) DO NOTHING;


-- ========== 2. Bot Messages ==========
-- The customer-facing text library. Empty initially → bot reads hardcoded.
-- When dashboard staff adds a row + flag bot_messages_enabled = TRUE,
-- the corresponding surface starts using DB value.

CREATE TABLE IF NOT EXISTS bot_messages (
    message_key VARCHAR(64) PRIMARY KEY,
    content_html TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
        -- Human-readable label for staff: "ข้อความ /start ลูกค้าใหม่"
    available_placeholders JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- Allowed {placeholders}: ["customer_name", "greeting", "expire_date"]
    category VARCHAR(32) NOT NULL DEFAULT 'general',
        -- group for UI: 'start', 'packages', 'payment',
        -- 'welcome', 'renewal', 'expired', 'general'
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_by BIGINT
);

CREATE INDEX IF NOT EXISTS ix_bot_messages_category ON bot_messages(category);

-- Version history for undo / rollback
CREATE TABLE IF NOT EXISTS bot_message_versions (
    id SERIAL PRIMARY KEY,
    message_key VARCHAR(64) NOT NULL REFERENCES bot_messages(message_key) ON DELETE CASCADE,
    content_html TEXT NOT NULL,
    changed_at TIMESTAMP NOT NULL DEFAULT NOW(),
    changed_by BIGINT,
    change_note TEXT
);
CREATE INDEX IF NOT EXISTS ix_bot_message_versions_key ON bot_message_versions(message_key, changed_at DESC);


-- ========== 3. Bot Menu Buttons ==========
-- Inline keyboard configuration. Empty initially → bot uses hardcoded buttons.

CREATE TABLE IF NOT EXISTS bot_menu_buttons (
    id SERIAL PRIMARY KEY,
    menu_key VARCHAR(32) NOT NULL,
        -- 'start_main', 'packages_list', 'vip_welcome'
    position INT NOT NULL,
    label TEXT NOT NULL,
        -- "📦 ดูแพ็กเกจ"
    action_type VARCHAR(16) NOT NULL,
        -- 'callback' / 'url' / 'webapp'
    action_value TEXT NOT NULL,
        -- callback_data / URL / webapp URL
    condition_key VARCHAR(64),
        -- NULL = always show
        -- 'flash_active', 'vip_active', 'balance_gt_0'
    is_protected BOOLEAN NOT NULL DEFAULT FALSE,
        -- TRUE = staff cannot delete
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_by BIGINT
);
CREATE INDEX IF NOT EXISTS ix_bot_menu_buttons_menu ON bot_menu_buttons(menu_key, position);


-- ========== Audit table (extend existing admin_logs) ==========
-- Nothing to add — admin_logs already exists. New action types:
--   'feature_flag_toggle', 'bot_message_create',
--   'bot_message_update', 'bot_message_delete'


-- ========== Verification ==========
SELECT 'Migration phaseA1 complete. Tables created:' AS status;
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('feature_flags', 'bot_messages', 'bot_message_versions', 'bot_menu_buttons')
ORDER BY table_name;
SELECT 'Seeded feature flags (all OFF for safety):' AS status;
SELECT flag_key, enabled, scope FROM feature_flags ORDER BY flag_key;

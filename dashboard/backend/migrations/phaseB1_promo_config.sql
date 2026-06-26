-- ====================================================================
-- Phase B.1 Promo Manager — config table
-- Date: 2026-06-27
-- ====================================================================

CREATE TABLE IF NOT EXISTS promo_config (
    config_key VARCHAR(64) PRIMARY KEY,
    value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    description TEXT NOT NULL DEFAULT '',
    category VARCHAR(32) NOT NULL DEFAULT 'general',
        -- 'comeback', 'quickbuy', 'gacha_discount', 'group_bot'
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_by BIGINT
);

CREATE INDEX IF NOT EXISTS ix_promo_config_category ON promo_config(category);

-- Seed current production values
INSERT INTO promo_config (config_key, value_json, description, category) VALUES
    -- Comeback DM Round 1
    ('comeback_r1_days_after_expiry',  '3'::jsonb,
        'Comeback รอบ 1: ส่งหลังหมดอายุกี่วัน', 'comeback'),
    ('comeback_r1_discount_pct',       '30'::jsonb,
        'Comeback รอบ 1: ลดราคา (%)', 'comeback'),

    -- Comeback DM Round 2
    ('comeback_r2_days_after_r1',      '7'::jsonb,
        'Comeback รอบ 2: ส่งหลังรอบ 1 ผ่านไปกี่วัน', 'comeback'),
    ('comeback_r2_discount_pct',       '40'::jsonb,
        'Comeback รอบ 2: ลดราคา (%)', 'comeback'),

    -- Comeback global
    ('comeback_max_dm_per_day',        '30'::jsonb,
        'Comeback DM: ส่งสูงสุดกี่คน/วัน (rate limit)', 'comeback'),
    ('comeback_base_price',            '300'::jsonb,
        'Comeback ราคาฐาน (VIP 30 วัน)', 'comeback'),
    ('comeback_enabled',               'true'::jsonb,
        'Comeback DM เปิด/ปิด ทั้งระบบ', 'comeback'),

    -- Quick Buy on /start (comeback deep link)
    ('quickbuy_default_discount_pct',  '25'::jsonb,
        'Quick Buy /start: ลดราคา default ถ้าไม่ระบุ (%)', 'quickbuy'),
    ('quickbuy_validity_hours',        '48'::jsonb,
        'Quick Buy: ลิงก์ใช้ได้กี่ชม.', 'quickbuy'),

    -- Gacha Discount
    ('gacha_discount_default_amount',  '50'::jsonb,
        'Gacha: เงินรางวัลส่วนลด default (บาท)', 'gacha_discount'),
    ('gacha_discount_cap_per_tier',
        '{"VIP_300": 50, "OF_500": 50, "GOD_1299": 100, "GOD_2499": 200}'::jsonb,
        'Gacha: เพดานส่วนลดต่อแพ็กเกจ (บาท)', 'gacha_discount'),

    -- Group Bot behavior
    ('group_bot_welcome_enabled',      'true'::jsonb,
        'Guardian: ทักทายสมาชิกใหม่ในกลุ่ม', 'group_bot'),
    ('group_bot_daily_content_enabled','true'::jsonb,
        'Content bot: โพสต์รูปประจำวัน', 'group_bot')

ON CONFLICT (config_key) DO NOTHING;

SELECT 'Phase B.1: promo_config seeded' AS status, COUNT(*) AS rows FROM promo_config;
SELECT category, COUNT(*) AS configs FROM promo_config GROUP BY category ORDER BY category;

-- 2026-06-30 receiver tracking + idempotency (DDL ที่ apply ลง live ตอน audit — backfill ให้ rebuild-from-source ตรง prod)
ALTER TABLE purchase_intents ADD COLUMN IF NOT EXISTS receiver_account_id INTEGER;
ALTER TABLE payments         ADD COLUMN IF NOT EXISTS matched_receiver_account_id INTEGER;
ALTER TABLE payments         ADD COLUMN IF NOT EXISTS slip_receiver_meta JSONB;
CREATE UNIQUE INDEX IF NOT EXISTS uq_admin_logs_receiver_credit
    ON admin_logs (target_id) WHERE action = 'receiver_credit';

-- onboarding rewards (ของขวัญต้อนรับ) — ย้ายจาก hardcode มาเป็น config ใน DB (แก้ใน dashboard ได้)
CREATE TABLE IF NOT EXISTS onboarding_rewards (
  tier TEXT PRIMARY KEY, gacha INT NOT NULL DEFAULT 0,
  discount NUMERIC(10,2) NOT NULL DEFAULT 0, days INT NOT NULL DEFAULT 0,
  enabled BOOLEAN NOT NULL DEFAULT TRUE, updated_at TIMESTAMP DEFAULT NOW());
INSERT INTO onboarding_rewards (tier,gacha,discount,days) VALUES
 ('TIER_100',1,20,0),('TIER_300',2,50,0),('TIER_500',3,100,3),
 ('TIER_1299',5,200,0),('TIER_2499',5,300,0) ON CONFLICT (tier) DO NOTHING;

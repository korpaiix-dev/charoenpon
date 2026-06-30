-- 2026-06-30 receiver tracking + idempotency (DDL ที่ apply ลง live ตอน audit — backfill ให้ rebuild-from-source ตรง prod)
ALTER TABLE purchase_intents ADD COLUMN IF NOT EXISTS receiver_account_id INTEGER;
ALTER TABLE payments         ADD COLUMN IF NOT EXISTS matched_receiver_account_id INTEGER;
ALTER TABLE payments         ADD COLUMN IF NOT EXISTS slip_receiver_meta JSONB;
CREATE UNIQUE INDEX IF NOT EXISTS uq_admin_logs_receiver_credit
    ON admin_logs (target_id) WHERE action = 'receiver_credit';

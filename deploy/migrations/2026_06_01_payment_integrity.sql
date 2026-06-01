-- Migration: payment integrity triggers + CHECK constraint
-- Date: 2026-06-01
-- Purpose: enforce data consistency between payments + users.total_spent

-- 1. CHECK: amount non-negative
ALTER TABLE payments DROP CONSTRAINT IF EXISTS ck_payments_amount_nonneg;
ALTER TABLE payments ADD CONSTRAINT ck_payments_amount_nonneg CHECK (amount >= 0);

-- 2. Function: sync users.total_spent from SUM(payments.amount WHERE CONFIRMED)
DROP FUNCTION IF EXISTS sync_total_spent() CASCADE;
CREATE FUNCTION sync_total_spent() RETURNS TRIGGER AS $$
DECLARE
    _uid INTEGER;
BEGIN
    IF TG_OP = 'DELETE' THEN
        _uid := OLD.user_id;
    ELSIF TG_OP = 'UPDATE' AND OLD.user_id <> NEW.user_id THEN
        UPDATE users SET total_spent = COALESCE(
            (SELECT SUM(amount) FROM payments p WHERE p.user_id = OLD.user_id AND p.status = 'CONFIRMED'), 0
        ) WHERE id = OLD.user_id;
        _uid := NEW.user_id;
    ELSE
        _uid := NEW.user_id;
    END IF;
    UPDATE users SET total_spent = COALESCE(
        (SELECT SUM(amount) FROM payments p WHERE p.user_id = _uid AND p.status = 'CONFIRMED'), 0
    ) WHERE id = _uid;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_payments_sync_total_spent ON payments;
CREATE TRIGGER trg_payments_sync_total_spent
AFTER INSERT OR UPDATE OR DELETE ON payments
FOR EACH ROW EXECUTE FUNCTION sync_total_spent();

-- 3. Function: cancel subscription on payment status → REJECTED/REFUNDED
DROP FUNCTION IF EXISTS cancel_sub_on_payment_reject() CASCADE;
CREATE FUNCTION cancel_sub_on_payment_reject() RETURNS TRIGGER AS $$
BEGIN
    UPDATE subscriptions SET status='EXPIRED', updated_at=NOW()
     WHERE payment_id = NEW.id AND status='ACTIVE';
    PERFORM pg_notify('subscription_cancelled', NEW.id::text);
    RETURN NULL;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_payments_cancel_sub_on_reject ON payments;
DROP TRIGGER IF EXISTS trg_payments_cancel_sub_on_reject_or_refund ON payments;
CREATE TRIGGER trg_payments_cancel_sub_on_reject_or_refund
AFTER UPDATE ON payments
FOR EACH ROW
WHEN (OLD.status = 'CONFIRMED' AND NEW.status IN ('REJECTED', 'REFUNDED'))
EXECUTE FUNCTION cancel_sub_on_payment_reject();

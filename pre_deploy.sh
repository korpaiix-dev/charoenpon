#!/bin/bash
# Pre-deploy checklist สำหรับ Charoenpon
# รัน script นี้ก่อนทุก deploy / docker cp / docker restart
#
# Exit code:
#   0 = ผ่านทุกข้อ ปลอดภัย deploy ได้
#   1 = test fail หรือ syntax error → ห้าม deploy

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PROJECT_ROOT="${PROJECT_ROOT:-/root/charoenpon}"
CONTAINER="${CONTAINER:-charoenpon-sales-bot}"

echo -e "${YELLOW}━━━ Pre-deploy Checklist สำหรับ Charoenpon ━━━${NC}"
echo ""

# ─── Check 1: Python syntax (all .py files) ────────────────────────────
echo -n "🔍 [1/5] Python syntax check ทุกไฟล์... "
SYNTAX_ERRORS=$(find "$PROJECT_ROOT/shared" "$PROJECT_ROOT/bots" -name "*.py" -exec python3 -m py_compile {} \; 2>&1 | wc -l)
if [ "$SYNTAX_ERRORS" -gt 0 ]; then
    echo -e "${RED}❌ syntax error${NC}"
    find "$PROJECT_ROOT/shared" "$PROJECT_ROOT/bots" -name "*.py" -exec python3 -m py_compile {} \;
    exit 1
fi
echo -e "${GREEN}✓ pass${NC}"

# ─── Check 2: Import smoke test ────────────────────────────────────────
echo -n "🔍 [2/5] Import smoke test (core modules)... "
IMPORT_TEST=$(docker exec "$CONTAINER" python3 -c "
import sys
sys.path.insert(0, '/app')
errors = []
modules = [
    'shared.payment_approval', 'shared.loyalty_rank', 'shared.welcome_journey',
    'shared.slip2go_retry_worker', 'shared.admin_alert', 'shared.notify',
    'shared.payment_health_check', 'shared.customer_dm', 'shared.models',
    'bots.sales_bot.payment_util.truemoney_handler',
    'bots.sales_bot.handlers.payment',
    'bots.sales_bot.handlers.start',
    'bots.guardian_bot.scheduler',
]
for m in modules:
    try:
        __import__(m)
    except Exception as e:
        errors.append(f'{m}: {e}')
if errors:
    for e in errors: print(e)
    sys.exit(1)
print('OK')
" 2>&1)
if [[ "$IMPORT_TEST" != *"OK"* ]]; then
    echo -e "${RED}❌ import fail${NC}"
    echo "$IMPORT_TEST"
    exit 1
fi
echo -e "${GREEN}✓ pass${NC}"

# ─── Check 3: Diff host vs container (catch deploy-lag) ──────────────
echo -n "🔍 [3/5] Diff host vs container (ทุก shared/*.py)... "
DIFF_FILES=""
for f in "$PROJECT_ROOT/shared"/*.py; do
    [ -f "$f" ] || continue
    fname=$(basename "$f")
    if ! docker exec "$CONTAINER" diff /app/shared/$fname "$f" > /dev/null 2>&1; then
        if [ -f "$f" ]; then
            DIFF_FILES="$DIFF_FILES $fname"
        fi
    fi
done
if [ -n "$DIFF_FILES" ]; then
    echo -e "${YELLOW}⚠ host ต่างจาก container:${DIFF_FILES}${NC}"
    echo -e "${YELLOW}  → ต้อง docker cp + restart container ที่เกี่ยวข้อง${NC}"
    # อันนี้ไม่ exit เพราะอาจต้องการ deploy รอบนี้
else
    echo -e "${GREEN}✓ pass${NC}"
fi

# ─── Pre-Check 4: Cleanup test data ก่อนรัน test ────────────────────
docker exec charoenpon-postgres psql -U postgres -d charoenpon -c """
DELETE FROM gachapon_credits WHERE telegram_id >= 9900000000;
DELETE FROM subscriptions WHERE user_id IN (SELECT id FROM users WHERE telegram_id >= 9900000000);
DELETE FROM payments WHERE user_id IN (SELECT id FROM users WHERE telegram_id >= 9900000000);
DELETE FROM comeback_dm_log WHERE telegram_id >= 9900000000;
DELETE FROM admin_logs WHERE details LIKE '%TEST_PYTEST%';
DELETE FROM users WHERE telegram_id >= 9900000000;
""" > /dev/null 2>&1

# ─── Check 4: Regression tests ──────────────────────────────────────
echo "🔍 [4/5] Regression tests..."
TEST_OUTPUT=$(docker exec "$CONTAINER" bash -c "cd /app && python3 -m pytest tests/regression/ --tb=line -q 2>&1" || echo "TEST_FAILED")
TEST_RESULT=$(echo "$TEST_OUTPUT" | tail -3 | head -1)
if echo "$TEST_OUTPUT" | grep -qE "failed|error"; then
    echo -e "${RED}❌ tests fail:${NC}"
    echo "$TEST_OUTPUT" | tail -10
    exit 1
fi
echo -e "${GREEN}   ✓ $TEST_RESULT${NC}"

# ─── Check 5: System invariants (DB integrity) ──────────────────────
echo "🔍 [5/5] System invariants (health check)..."
HEALTH_OUTPUT=$(docker exec "$CONTAINER" python3 -c "
import sys, asyncio
sys.path.insert(0, '/app')
from shared.payment_health_check import health_check_payment_system
issues = asyncio.run(health_check_payment_system())
if issues:
    for i in issues: print(f'  {i}')
    sys.exit(1)
print('OK')
" 2>&1)
if [[ "$HEALTH_OUTPUT" != *"OK"* ]]; then
    echo -e "${RED}❌ health check fail:${NC}"
    echo "$HEALTH_OUTPUT"
    exit 1
fi
echo -e "${GREEN}   ✓ pass${NC}"

# ─── Summary ────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━ ✅ ผ่านทุกข้อ — Deploy ได้ ━━━${NC}"
exit 0

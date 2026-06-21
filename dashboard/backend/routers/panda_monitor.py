"""Panda Monitor — Business metrics + system health."""
from __future__ import annotations
import os
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from datetime import datetime
from ..database import pool
from shared.rate_limit_simple import rate_limit_check

router = APIRouter(tags=["panda-monitor"])
_PANDA_TOKEN = os.environ.get("PANDA_MONITOR_TOKEN", "panda2026")


@router.get("/panda-monitor/data")
async def panda_monitor_data(request: Request, token: str = Query(None)):
    await rate_limit_check(request, key="panda_monitor", limit=60, window=60)
    if token != _PANDA_TOKEN:
        raise HTTPException(status_code=403, detail="invalid token")

    # FIX 2026-06-21: ใช้ BKK timezone (DB เก็บ UTC) + exclude test users
    row = await pool.fetchrow("""
        WITH t AS (
            SELECT p.amount, (p.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok' AS bkk
            FROM payments p
            JOIN users u ON u.id = p.user_id
            WHERE p.status::text = 'CONFIRMED' AND u.telegram_id < 9000000000
        ), today AS (SELECT (NOW() AT TIME ZONE 'Asia/Bangkok')::date AS d)
        SELECT
            COALESCE(SUM(CASE WHEN bkk::date = (SELECT d FROM today) THEN amount ELSE 0 END), 0)::int AS today,
            COALESCE(SUM(CASE WHEN bkk::date = (SELECT d FROM today) - 1 THEN amount ELSE 0 END), 0)::int AS yesterday,
            COALESCE(SUM(CASE WHEN bkk > NOW() AT TIME ZONE 'Asia/Bangkok' - INTERVAL '7 days' THEN amount ELSE 0 END), 0)::int AS d7,
            COALESCE(SUM(CASE WHEN bkk > NOW() AT TIME ZONE 'Asia/Bangkok' - INTERVAL '30 days' THEN amount ELSE 0 END), 0)::int AS d30,
            COUNT(*) FILTER (WHERE bkk::date = (SELECT d FROM today)) AS payments_today,
            COUNT(*) FILTER (WHERE bkk > NOW() AT TIME ZONE 'Asia/Bangkok' - INTERVAL '7 days') AS payments_7d
        FROM t
    """)
    revenue = dict(row)

    row = await pool.fetchrow("""
        WITH t AS (
            SELECT (created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok' AS bkk
            FROM users WHERE telegram_id < 9000000000
        )
        SELECT
            COUNT(*) FILTER (WHERE bkk::date = (NOW() AT TIME ZONE 'Asia/Bangkok')::date) AS today,
            COUNT(*) FILTER (WHERE bkk > NOW() AT TIME ZONE 'Asia/Bangkok' - INTERVAL '7 days') AS d7,
            COUNT(*) FILTER (WHERE bkk > NOW() AT TIME ZONE 'Asia/Bangkok' - INTERVAL '30 days') AS d30,
            COUNT(*) AS total
        FROM t
    """)
    customers = dict(row)

    rows = await pool.fetch("""
        SELECT COALESCE(loyalty_rank, 'NONE') AS rank, COUNT(*) AS n
        FROM users GROUP BY loyalty_rank ORDER BY n DESC
    """)
    loyalty = {r["rank"]: r["n"] for r in rows}

    rows = await pool.fetch("""
        SELECT pk.tier::text AS tier, COUNT(*) AS n
        FROM subscriptions s JOIN packages pk ON pk.id = s.package_id
        WHERE s.status::text = 'ACTIVE' AND s.end_date > NOW()
        GROUP BY pk.tier ORDER BY n DESC
    """)
    active_subs = {r["tier"]: r["n"] for r in rows}

    rows = await pool.fetch("""
        SELECT p.id, u.first_name AS name, u.telegram_id AS tg,
            p.amount::int AS amount, pk.tier::text AS tier, p.status::text AS status,
            to_char((p.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok', 'DD/MM HH24:MI') AS created
        FROM payments p
        JOIN users u ON u.id = p.user_id
        JOIN packages pk ON pk.id = p.package_id
        WHERE u.telegram_id < 9000000000
        ORDER BY p.id DESC LIMIT 10
    """)
    recent_payments = [dict(r) for r in rows]

    rows = await pool.fetch("""
        SELECT u.first_name AS name, u.telegram_id AS tg, SUM(p.amount)::int AS total
        FROM payments p JOIN users u ON u.id = p.user_id
        WHERE p.status::text = 'CONFIRMED'
          AND (p.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Bangkok' > NOW() AT TIME ZONE 'Asia/Bangkok' - INTERVAL '30 days'
          AND u.telegram_id < 9000000000
        GROUP BY u.id, u.first_name, u.telegram_id
        ORDER BY total DESC LIMIT 10
    """)
    top_spenders = [dict(r) for r in rows]

    row = await pool.fetchrow("""
        SELECT
            (SELECT COUNT(*) FROM slip2go_retry_queue WHERE status IN ('WAITING', 'PROCESSING')) AS retry_pending,
            (SELECT COUNT(*) FROM payments WHERE status::text = 'PENDING' AND created_at < NOW() - INTERVAL '15 minutes') AS payment_stuck,
            (SELECT COUNT(*) FROM subscriptions WHERE status::text = 'ACTIVE' AND end_date < NOW()) AS sub_expired_stuck,
            (SELECT COUNT(*) FROM users WHERE is_blocked_bot = TRUE) AS blocked_users
    """)
    health = dict(row)
    health["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "revenue": revenue, "customers": customers, "loyalty": loyalty,
        "active_subs": active_subs, "recent_payments": recent_payments,
        "top_spenders_30d": top_spenders, "health": health,
    }


@router.get("/panda-monitor", response_class=HTMLResponse)
async def panda_monitor_html(token: str = Query(None)):
    if token != _PANDA_TOKEN:
        return HTMLResponse("<h1>Forbidden</h1>", status_code=403)
    html_body = _build_html(token)
    return HTMLResponse(html_body)


def _build_html(token: str) -> str:
    return PANDA_HTML.replace("__TOKEN__", token)


PANDA_HTML = """<!DOCTYPE html>
<html lang="th"><head><meta charset="UTF-8"><title>Panda Monitor</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:-apple-system,Tahoma,sans-serif;background:#1a1a2e;color:#eee;margin:0;padding:16px}
h1{margin:0 0 16px;color:#ffd23f;font-size:1.4em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.card{background:#16213e;border-radius:12px;padding:16px;box-shadow:0 2px 8px rgba(0,0,0,.3)}
.card h2{margin:0 0 12px;color:#4cc9f0;font-size:1em;border-bottom:1px solid #333;padding-bottom:6px}
.metric{display:flex;justify-content:space-between;padding:4px 0}
.metric b{color:#ffd23f}
.green{color:#06ffa5}.red{color:#ff6b6b}
table{width:100%;font-size:.85em}table td{padding:3px 4px}
.refresh{float:right;color:#888;font-size:.8em}
.alert{background:#c62828;color:#fff;padding:8px;border-radius:6px;margin-bottom:12px}
.ok{background:#2e7d32;color:#fff;padding:8px;border-radius:6px;margin-bottom:12px}
</style></head><body>
<h1>Panda Monitor <span class="refresh" id="ts"></span></h1>
<div id="health"></div>
<div class="grid" id="content">Loading...</div>
<script>
const TOKEN="__TOKEN__";
function fmt(n){return new Intl.NumberFormat('th-TH').format(n)}
function bahts(n){return 'B'+fmt(n)}
async function refresh(){
  try{
    const r=await fetch('/panda-monitor/data?token='+TOKEN);
    const d=await r.json();
    render(d);
    document.getElementById('ts').textContent='RELOAD '+new Date().toLocaleTimeString('th-TH');
  }catch(e){document.getElementById('content').innerHTML='<div class=alert>Error: '+e.message+'</div>'}
}
function render(d){
  const h=d.health;const issues=[];
  if(h.retry_pending>0)issues.push('retry queue:'+h.retry_pending);
  if(h.payment_stuck>0)issues.push('payment stuck:'+h.payment_stuck);
  if(h.sub_expired_stuck>0)issues.push('expired ca:'+h.sub_expired_stuck);
  document.getElementById('health').innerHTML=issues.length?'<div class=alert>'+issues.join(' | ')+'</div>':'<div class=ok>OK</div>';
  let html='<div class=card><h2>Revenue</h2>'
    +'<div class=metric>Today: <b>'+bahts(d.revenue.today)+'</b> ('+d.revenue.payments_today+')</div>'
    +'<div class=metric>Yesterday: <b>'+bahts(d.revenue.yesterday)+'</b></div>'
    +'<div class=metric>7d: <b>'+bahts(d.revenue.d7)+'</b> ('+d.revenue.payments_7d+')</div>'
    +'<div class=metric>30d: <b>'+bahts(d.revenue.d30)+'</b></div></div>';
  html+='<div class=card><h2>Customers</h2>'
    +'<div class=metric>Today: <b>'+fmt(d.customers.today)+'</b></div>'
    +'<div class=metric>7d: <b>'+fmt(d.customers.d7)+'</b></div>'
    +'<div class=metric>30d: <b>'+fmt(d.customers.d30)+'</b></div>'
    +'<div class=metric>Total: <b>'+fmt(d.customers.total)+'</b></div></div>';
  html+='<div class=card><h2>Loyalty</h2>';
  ['DIAMOND','SILVER','BRONZE','NONE'].forEach(r=>{if(d.loyalty[r]!==undefined)html+='<div class=metric>'+r+': <b>'+fmt(d.loyalty[r])+'</b></div>'});
  html+='</div>';
  html+='<div class=card><h2>Active Subs</h2>';
  Object.entries(d.active_subs).forEach(([k,v])=>{html+='<div class=metric>'+k+': <b>'+fmt(v)+'</b></div>'});
  html+='</div>';
  html+='<div class=card><h2>Recent</h2><table>';
  d.recent_payments.forEach(p=>{const c=p.status==='CONFIRMED'?'green':(p.status==='REJECTED'?'red':'');html+='<tr><td>#'+p.id+'</td><td>'+(p.name||'-').substring(0,12)+'</td><td>'+bahts(p.amount)+'</td><td class='+c+'>'+p.status+'</td><td>'+p.created+'</td></tr>'});
  html+='</table></div>';
  html+='<div class=card><h2>Top Spenders 30d</h2><table>';
  d.top_spenders_30d.forEach((s,i)=>{html+='<tr><td>'+(i+1)+'.</td><td>'+(s.name||'-').substring(0,15)+'</td><td><b>'+bahts(s.total)+'</b></td></tr>'});
  html+='</table></div>';
  html+='<div class=card><h2>System Health</h2>'
    +'<div class=metric>Retry pending: <b>'+h.retry_pending+'</b></div>'
    +'<div class=metric>Payment stuck: <b>'+h.payment_stuck+'</b></div>'
    +'<div class=metric>Expired stuck: <b>'+h.sub_expired_stuck+'</b></div>'
    +'<div class=metric>Blocked: <b>'+fmt(h.blocked_users)+'</b></div>'
    +'<div class=metric style="color:#888;font-size:.8em">checked: '+h.timestamp+'</div></div>';
  document.getElementById('content').innerHTML=html;
}
refresh();setInterval(refresh,60000);
</script></body></html>"""

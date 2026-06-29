"""Panda Errors — error dashboard (self-hosted, replaces Sentry).

URL: /panda-errors?token=XXX — HTML page
URL: /panda-errors/data?token=XXX — JSON
URL: /panda-errors/resolve?token=XXX&id=N — mark resolved
"""
from __future__ import annotations
import os
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from ..database import pool
from shared.rate_limit_simple import rate_limit_check

router = APIRouter(tags=["panda-errors"])
_TOKEN = os.environ.get("PANDA_MONITOR_TOKEN")


@router.get("/panda-errors/data")
async def panda_errors_data(request: Request, token: str = Query(None), since_hours: int = 24):
    await rate_limit_check(request, key="panda_errors", limit=60, window=60)
    if not _TOKEN or token != _TOKEN:
        raise HTTPException(status_code=403, detail="invalid token")

    summary = await pool.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE occurred_at > NOW() - INTERVAL '1 hour') AS h1,
            COUNT(*) FILTER (WHERE occurred_at > NOW() - INTERVAL '24 hours') AS h24,
            COUNT(*) FILTER (WHERE occurred_at > NOW() - INTERVAL '7 days') AS d7,
            COUNT(*) FILTER (WHERE NOT resolved AND occurred_at > NOW() - INTERVAL '24 hours') AS unresolved_24h,
            COUNT(DISTINCT fingerprint) FILTER (WHERE occurred_at > NOW() - INTERVAL '24 hours') AS unique_issues_24h
        FROM error_log
    """)

    issues = await pool.fetch("""
        SELECT
            fingerprint, error_type,
            (array_agg(error_msg ORDER BY id DESC))[1] AS latest_msg,
            (array_agg(container ORDER BY id DESC))[1] AS container,
            COUNT(*) AS occurrences,
            COUNT(DISTINCT telegram_id) FILTER (WHERE telegram_id IS NOT NULL) AS users_affected,
            MAX(occurred_at) AS last_seen,
            MIN(occurred_at) AS first_seen,
            BOOL_AND(resolved) AS all_resolved
        FROM error_log
        WHERE occurred_at > NOW() - make_interval(hours => $1)
        GROUP BY fingerprint, error_type
        ORDER BY all_resolved ASC, last_seen DESC
        LIMIT 50
    """, since_hours)

    by_container = await pool.fetch("""
        SELECT container, COUNT(*) AS n
        FROM error_log WHERE occurred_at > NOW() - make_interval(hours => $1)
        GROUP BY container ORDER BY n DESC
    """, since_hours)

    return {
        "summary": dict(summary),
        "issues": [dict(r) for r in issues],
        "by_container": {r["container"]: r["n"] for r in by_container},
    }


@router.post("/panda-errors/resolve")
async def panda_errors_resolve(token: str = Query(None), fingerprint: str = Query(None)):
    if not _TOKEN or token != _TOKEN:
        raise HTTPException(status_code=403, detail="invalid token")
    if not fingerprint:
        raise HTTPException(status_code=400, detail="fingerprint required")
    n = await pool.fetchval(
        "UPDATE error_log SET resolved=TRUE, resolved_at=NOW(), resolved_by='admin' "
        "WHERE fingerprint=$1 AND NOT resolved RETURNING (SELECT COUNT(*) FROM error_log WHERE fingerprint=$1)",
        fingerprint,
    )
    return {"resolved_fingerprint": fingerprint, "rows": n or 0}


@router.get("/panda-errors", response_class=HTMLResponse)
async def panda_errors_html(token: str = Query(None)):
    if not _TOKEN or token != _TOKEN:
        return HTMLResponse("<h1>Forbidden</h1>", status_code=403)
    return HTMLResponse(_HTML.replace("__TOKEN__", token))


_HTML = """<!DOCTYPE html>
<html lang="th"><head><meta charset="UTF-8"><title>Panda Errors</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:-apple-system,Tahoma,sans-serif;background:#1a1a2e;color:#eee;margin:0;padding:16px}
h1{margin:0 0 16px;color:#ff6b6b;font-size:1.4em}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-bottom:16px}
.stat{background:#16213e;border-radius:8px;padding:12px;text-align:center}
.stat .label{font-size:.8em;color:#888}
.stat .value{font-size:1.6em;font-weight:bold;color:#ffd23f}
.stat.alert .value{color:#ff6b6b}
.issue{background:#16213e;border-radius:8px;padding:12px;margin-bottom:8px;border-left:4px solid #ff6b6b}
.issue.resolved{border-left-color:#06ffa5;opacity:.6}
.issue h3{margin:0 0 6px;color:#4cc9f0;font-size:1em}
.issue .meta{font-size:.8em;color:#888;margin-bottom:6px}
.issue .msg{font-family:monospace;font-size:.85em;background:#0d1b2a;padding:6px;border-radius:4px;white-space:pre-wrap;word-wrap:break-word;max-height:80px;overflow:auto}
.issue .actions{margin-top:8px}
.btn{background:#4cc9f0;color:#fff;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:.8em}
.btn.resolve{background:#06ffa5;color:#000}
.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.75em;margin-right:4px}
.tag-container{background:#4cc9f0;color:#000}
.tag-count{background:#ff6b6b;color:#fff;font-weight:bold}
.tag-users{background:#ffd23f;color:#000}
.refresh{float:right;color:#888;font-size:.8em}
.container-pills{margin-bottom:12px}
.pill{display:inline-block;background:#16213e;padding:4px 10px;border-radius:12px;margin-right:6px;font-size:.85em}
.pill .num{color:#ff6b6b;font-weight:bold;margin-left:4px}
</style></head><body>
<h1>Panda Errors <span class="refresh" id="ts"></span></h1>
<div class="summary" id="summary"></div>
<div class="container-pills" id="containers"></div>
<div id="issues"></div>
<script>
const TOKEN="__TOKEN__";
function fmt(n){return new Intl.NumberFormat('th-TH').format(n)}
function age(d){const sec=Math.floor((new Date()-new Date(d))/1000);if(sec<60)return sec+'s';if(sec<3600)return Math.floor(sec/60)+'m';if(sec<86400)return Math.floor(sec/3600)+'h';return Math.floor(sec/86400)+'d'}
async function refresh(){
  try{
    const r=await fetch('/panda-errors/data?token='+TOKEN+'&since_hours=24');
    const d=await r.json();
    render(d);
    document.getElementById('ts').textContent='RELOAD '+new Date().toLocaleTimeString('th-TH');
  }catch(e){document.getElementById('issues').innerHTML='<div class=issue>Error: '+e.message+'</div>'}
}
async function resolveFingerprint(fp){
  await fetch('/panda-errors/resolve?token='+TOKEN+'&fingerprint='+fp,{method:'POST'});
  refresh();
}
function render(d){
  const s=d.summary;
  document.getElementById('summary').innerHTML=`
    <div class="stat"><div class="label">1 hour</div><div class="value">${fmt(s.h1)}</div></div>
    <div class="stat ${s.unresolved_24h>0?'alert':''}"><div class="label">Unresolved (24h)</div><div class="value">${fmt(s.unresolved_24h)}</div></div>
    <div class="stat"><div class="label">24 hours</div><div class="value">${fmt(s.h24)}</div></div>
    <div class="stat"><div class="label">7 days</div><div class="value">${fmt(s.d7)}</div></div>
    <div class="stat"><div class="label">Unique issues 24h</div><div class="value">${fmt(s.unique_issues_24h)}</div></div>`;
  const pills=Object.entries(d.by_container).map(([k,v])=>`<span class="pill">${k}<span class="num">${fmt(v)}</span></span>`).join('');
  document.getElementById('containers').innerHTML=pills||'<span style="color:#888">no errors</span>';
  if(!d.issues.length){document.getElementById('issues').innerHTML='<div style="color:#06ffa5;padding:20px;text-align:center;font-size:1.2em">ALL GREEN — no errors in last 24h</div>';return}
  const html=d.issues.map(i=>`
    <div class="issue ${i.all_resolved?'resolved':''}">
      <h3>${i.error_type}: ${escape(i.latest_msg.substring(0,120))}</h3>
      <div class="meta">
        <span class="tag tag-container">${i.container}</span>
        <span class="tag tag-count">${i.occurrences}x</span>
        <span class="tag tag-users">${i.users_affected} users</span>
        first: ${age(i.first_seen)} ago | last: ${age(i.last_seen)} ago
      </div>
      <div class="msg">${escape(i.latest_msg)}</div>
      <div class="actions">
        ${i.all_resolved?'<span style="color:#06ffa5">RESOLVED</span>':`<button class="btn resolve" onclick="resolveFingerprint('${i.fingerprint}')">Mark Resolved</button>`}
      </div>
    </div>`).join('');
  document.getElementById('issues').innerHTML=html;
}
function escape(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
refresh();setInterval(refresh,30000);
</script></body></html>"""

"""Marketing short URL redirect — public route for telebord.net/r/{code}.

Looks up short_code in marketing_invite_links, logs the click, and 302-redirects
to the underlying invite_link (bot deep-link or group invite).

Public — no auth required.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..database import pool

logger = logging.getLogger(__name__)
router = APIRouter(tags=["redirect"])


def _tg_app_url(invite_link: str) -> str:
    """Build a tg:// native-app deep link from an https://t.me/... link (desktop reliability)."""
    m = re.match(r"https?://t\.me/([A-Za-z0-9_]+)\?start=(.+)$", invite_link)
    if m:
        return f"tg://resolve?domain={m.group(1)}&start={m.group(2)}"
    m = re.match(r"https?://t\.me/\+([\w-]+)", invite_link)
    if m:
        return f"tg://join?invite={m.group(1)}"
    m = re.match(r"https?://t\.me/([A-Za-z0-9_]+)/?$", invite_link)
    if m:
        return f"tg://resolve?domain={m.group(1)}"
    return ""


_INTERSTITIAL = """<!doctype html>
<html lang="th"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>\u0e40\u0e1b\u0e34\u0e14\u0e43\u0e19\u0e40\u0e17\u0e40\u0e25\u0e41\u0e01\u0e23\u0e21</title>
<style>
  *{box-sizing:border-box}
  body{font-family:'Inter','Noto Sans Thai',system-ui,sans-serif;
    background:linear-gradient(160deg,#1a0e2e,#2a1245);color:#fff;display:flex;align-items:center;
    justify-content:center;min-height:100vh;margin:0;padding:1rem}
  .card{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);border-radius:20px;
    padding:2.2rem 1.8rem;max-width:360px;text-align:center}
  .logo{font-size:3rem;margin-bottom:.3rem}
  h1{font-size:1.3rem;margin:.2rem 0 .5rem}
  p{color:#d8ccec;margin:0 0 1.2rem;font-size:.95rem;line-height:1.6}
  .btn{display:block;background:linear-gradient(135deg,#8b5cf6,#d946ef);color:#fff;text-decoration:none;
    font-weight:700;padding:1rem;border-radius:12px;font-size:1.05rem}
  .small{font-size:.8rem;color:#b9a9d4;margin-top:1rem;word-break:break-all}
  .small a{color:#e9d5ff}
</style></head><body>
<div class="card">
  <div class="logo">\U0001F48E</div>
  <h1>\u0e40\u0e02\u0e49\u0e32\u0e2b\u0e49\u0e2d\u0e07 VIP \u0e40\u0e08\u0e23\u0e34\u0e0d\u0e1e\u0e23</h1>
  <p>\u0e01\u0e14\u0e1b\u0e38\u0e48\u0e21\u0e14\u0e49\u0e32\u0e19\u0e25\u0e48\u0e32\u0e07\u0e40\u0e1e\u0e37\u0e48\u0e2d\u0e40\u0e1b\u0e34\u0e14\u0e43\u0e19\u0e41\u0e2d\u0e1b Telegram</p>
  <a class="btn" id="go" href="__TME__">\U0001F449 \u0e40\u0e1b\u0e34\u0e14\u0e43\u0e19\u0e40\u0e17\u0e40\u0e25\u0e41\u0e01\u0e23\u0e21</a>
  <p class="small">\u0e40\u0e1b\u0e34\u0e14\u0e44\u0e21\u0e48\u0e44\u0e14\u0e49? \u0e40\u0e1b\u0e34\u0e14\u0e41\u0e2d\u0e1b Telegram \u0e04\u0e49\u0e32\u0e07\u0e44\u0e27\u0e49 \u0e41\u0e25\u0e49\u0e27\u0e01\u0e14\u0e2d\u0e35\u0e01\u0e04\u0e23\u0e31\u0e49\u0e07<br>
     <a href="__TME__">__TME__</a></p>
</div>
<script>
  var TME="__TME__", TG="__TGAPP__";
  if(TG){ try{ window.location.href=TG; }catch(e){} }
  document.getElementById('go').addEventListener('click',function(ev){
    if(TG){ ev.preventDefault(); window.location.href=TG; setTimeout(function(){ window.location.href=TME; },1200); }
  });
</script>
</body></html>"""

# Matches the alphabet used by _generate_short_code in shared/marketing_tools.py
# (a-z A-Z minus 0/O/1/I/l, plus 2-9)
_CODE_PATTERN = re.compile(r"^[a-zA-Z2-9]{4,8}$")


_HTML_NOT_FOUND = """<!doctype html>
<html lang="th"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ลิ้งไม่พบ — เจริญพร</title>
<style>
  body{font-family:'Inter','Noto Sans Thai',system-ui,sans-serif;background:#fafafa;color:#1a1a1f;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:1rem;}
  .card{background:#fff;border:1px solid #eaeaea;border-radius:12px;padding:2rem;max-width:380px;text-align:center;
        box-shadow:0 4px 16px rgba(0,0,0,0.06);}
  h1{font-size:1.25rem;margin:0 0 0.5rem;color:#dc2626;}
  p{color:#525252;margin:0 0 1rem;font-size:0.9rem;line-height:1.5;}
  a{color:#0070f3;text-decoration:none;font-weight:500;}
  a:hover{text-decoration:underline;}
</style></head><body>
<div class="card">
  <h1>ลิ้งไม่พบในระบบ</h1>
  <p>ลิ้งนี้อาจพิมพ์ผิด หรือไม่เคยถูกสร้างไว้ในระบบของเจริญพร</p>
  <p><a href="https://telebord.net/">กลับหน้าหลัก</a></p>
</div>
</body></html>"""

_HTML_REVOKED = """<!doctype html>
<html lang="th"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ลิ้งหมดอายุ — เจริญพร</title>
<style>
  body{font-family:'Inter','Noto Sans Thai',system-ui,sans-serif;background:#fafafa;color:#1a1a1f;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:1rem;}
  .card{background:#fff;border:1px solid #eaeaea;border-radius:12px;padding:2rem;max-width:380px;text-align:center;
        box-shadow:0 4px 16px rgba(0,0,0,0.06);}
  h1{font-size:1.25rem;margin:0 0 0.5rem;color:#d97706;}
  p{color:#525252;margin:0 0 1rem;font-size:0.9rem;line-height:1.5;}
  a{color:#0070f3;text-decoration:none;font-weight:500;}
  a:hover{text-decoration:underline;}
</style></head><body>
<div class="card">
  <h1>ลิ้งนี้ถูกยกเลิกแล้ว</h1>
  <p>แอดมินได้ยกเลิกลิ้งนี้แล้ว ติดต่อเจ้าของลิ้งหรือแอดมินเพื่อขอลิ้งใหม่</p>
  <p><a href="https://telebord.net/">กลับหน้าหลัก</a></p>
</div>
</body></html>"""


def _client_ip(request: Request) -> Optional[str]:
    """Extract real client IP — honor X-Forwarded-For from nginx."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()[:45]
    if request.client and request.client.host:
        return request.client.host[:45]
    return None


@router.get("/r/{code}")
async def short_link_redirect(code: str, request: Request):
    """Look up short_code and 302-redirect to the invite_link.

    Behaviors:
    - Invalid code format    → 404 HTML
    - Code not found         → 404 HTML
    - Code found but revoked → 410 HTML
    - Code found + active    → log click, 302 to invite_link
    """
    # Cheap input shape guard
    if not _CODE_PATTERN.fullmatch(code):
        return HTMLResponse(_HTML_NOT_FOUND, status_code=404)

    row = await pool.fetchrow(
        "SELECT id, invite_link, is_revoked "
        "FROM marketing_invite_links "
        "WHERE short_code = $1",
        code,
    )

    if not row:
        return HTMLResponse(_HTML_NOT_FOUND, status_code=404)

    if row["is_revoked"]:
        return HTMLResponse(_HTML_REVOKED, status_code=410)

    # Log click best-effort — failures must not block the redirect
    try:
        ip = _client_ip(request)
        ua = (request.headers.get("user-agent") or "")[:512]
        await pool.execute(
            "INSERT INTO marketing_link_clicks (link_id, ip, user_agent) "
            "VALUES ($1, $2, $3)",
            row["id"],
            ip,
            ua,
        )
    except Exception as exc:  # noqa: BLE001 — log + continue
        logger.warning("click log failed for code=%s: %s", code, exc)

    # FIX 2026-06-25: Notify Discord marketer feed about clicks (rate-limited)
    try:
        await _notify_click_discord(row["id"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("click discord notify failed: %s", exc)

    invite = row["invite_link"]
    ua = request.headers.get("user-agent") or ""
    # Mobile browsers and the in-Telegram browser open the t.me deep link reliably -> keep the
    # instant 302 (unchanged). Desktop browsers do NOT auto-open it, so serve an interstitial
    # with an explicit button + tg:// native-app link.
    if re.search(r"Android|iPhone|iPad|iPod|Mobile|Telegram", ua, re.I):
        return RedirectResponse(url=invite, status_code=302)
    html = _INTERSTITIAL.replace("__TME__", invite).replace("__TGAPP__", _tg_app_url(invite))
    return HTMLResponse(html)


# In-memory rate-limit: link_id -> last_notified_at (epoch seconds)
import time as _time
_last_click_notify: dict = {}
_CLICK_NOTIFY_COOLDOWN_SEC = 600  # 10 minutes between notifications per link

async def _notify_click_discord(link_id: int):
    """Notify the marketer's Discord feed channel about clicks (batched, embed-based)."""
    now = _time.time()
    last = _last_click_notify.get(link_id, 0)
    if now - last < _CLICK_NOTIFY_COOLDOWN_SEC:
        return
    _last_click_notify[link_id] = now

    link = await pool.fetchrow(
        "SELECT marketer, platform, short_code FROM marketing_invite_links WHERE id = $1",
        link_id,
    )
    if not link:
        return

    recent = await pool.fetchval(
        "SELECT COUNT(*) FROM marketing_link_clicks "
        "WHERE link_id = $1 AND clicked_at >= NOW() - INTERVAL \'10 minutes\'",
        link_id,
    )
    total = await pool.fetchval(
        "SELECT COUNT(*) FROM marketing_link_clicks WHERE link_id = $1", link_id,
    )

    from shared.discord_notify import _FEED_CHANNELS, post_embed, _PLATFORM_EMOJI
    ch = _FEED_CHANNELS.get(link["marketer"])
    if not ch:
        return

    plat_emoji = _PLATFORM_EMOJI.get((link["platform"] or "").lower(), "🔗")
    embed = {
        "color": 0x3b82f6,  # blue
        "title": f"👁 {recent} คลิก",
        "description": f"{plat_emoji} **{link['platform']}** · ลิ้ง `#{link_id}` · รวม **{total}** คลิก",
        "footer": {"text": f"telebord.net/r/{link['short_code']}"},
    }
    await post_embed(ch, embed)


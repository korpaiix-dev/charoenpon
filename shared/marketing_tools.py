"""Marketing tools for Prae Team Engine (Discord-side).

3 tools:
- create_marketing_link: gen new Telegram invite link + save to DB
- marketing_stats: query joins + conversions per marketer/platform
- marketing_links_list: list active links

Conversion windows: 7d / 30d / lifetime (default 30d)
"""
from __future__ import annotations
import datetime as dt
import logging
import os
from typing import Optional

import httpx
from sqlalchemy import text as sql_text

from shared.database import get_session

logger = logging.getLogger(__name__)

# Guardian bot is admin in PROMO_HUB + PROMO_NEWS — use it to create invite links
GUARDIAN_TOKEN = os.environ.get("GUARDIAN_BOT_TOKEN", "")

# Group slug → (chat_id, friendly Thai name)
_GROUPS = {
    "PROMO_HUB":  (-1003899592492, "รวมกลุ่ม เจริญพร x หลุมหลบภัย"),
    "PROMO_NEWS": (-1003763354393, "แจ้งข่าวสารเจริญพร X หลุมหลบภัย"),
}

# Allow fuzzy match for group name
def _resolve_group(group_name: str) -> Optional[tuple[str, int, str]]:
    """Resolve a user-typed group name to (slug, chat_id, title). None if unknown."""
    if not group_name:
        return None
    g = group_name.strip().lower()
    # Exact slug
    for slug, (cid, title) in _GROUPS.items():
        if g == slug.lower():
            return (slug, cid, title)
    # Match by Thai title keywords
    if "รวม" in g or "hub" in g:
        cid, title = _GROUPS["PROMO_HUB"]
        return ("PROMO_HUB", cid, title)
    if "ข่าว" in g or "news" in g or "แจ้ง" in g:
        cid, title = _GROUPS["PROMO_NEWS"]
        return ("PROMO_NEWS", cid, title)
    return None


def _bkk_now_str() -> str:
    # BKK = UTC+7
    return (dt.datetime.utcnow() + dt.timedelta(hours=7)).strftime("%Y%m%d-%H%M")


async def _telegram_create_invite_link(
    chat_id: int, name_tag: str
) -> dict:
    """Call Telegram createChatInviteLink. Returns dict with 'invite_link' or 'error'."""
    if not GUARDIAN_TOKEN:
        return {"error": "GUARDIAN_BOT_TOKEN not set"}
    url = f"https://api.telegram.org/bot{GUARDIAN_TOKEN}/createChatInviteLink"
    payload = {
        "chat_id": chat_id,
        "name": name_tag,
        # No member_limit, no expire — unlimited tracking link
        "creates_join_request": False,
    }
    async with httpx.AsyncClient(timeout=15.0) as cli:
        r = await cli.post(url, json=payload)
        data = r.json()
        if not data.get("ok"):
            return {"error": data.get("description", "unknown")}
        return {"invite_link": data["result"]["invite_link"]}


# =========================================================
# TOOL 1: create_marketing_link
# =========================================================
async def create_marketing_link(
    marketer: str,
    platform: str,
    group: str,
    created_by: str = "prae",
) -> dict:
    """Create a new tracked invite link for a marketer + platform + group.

    Args:
        marketer: 'Ivy' / 'Wasu' / 'Pai' (case-insensitive — will be normalized)
        platform: 'facebook' / 'tiktok' / 'youtube' / etc. — free-form
        group: 'รวมกลุ่ม' or 'แจ้งข่าวสาร' or 'PROMO_HUB' / 'PROMO_NEWS'
    """
    # Normalize marketer
    m = (marketer or "").strip()
    if m.lower() in ("ivy", "ไอวี่"):
        marketer = "Ivy"
    elif m.lower() in ("wasu", "วสุ"):
        marketer = "Wasu"
    elif m.lower() in ("pai", "ไผ่"):
        marketer = "Pai"
    else:
        return {"error": f"unknown marketer '{m}' — must be Ivy / Wasu / Pai"}

    platform = (platform or "").strip().lower()
    if not platform:
        return {"error": "platform required (e.g. facebook, tiktok, youtube)"}

    resolved = _resolve_group(group)
    if not resolved:
        return {"error": f"unknown group '{group}' — try 'รวมกลุ่ม' หรือ 'แจ้งข่าวสาร'"}
    slug, chat_id, title = resolved

    # Generate unique name_tag
    name_tag = f"{marketer.lower()}_{platform}_{_bkk_now_str()}"
    # Telegram caps name at 32 chars
    name_tag = name_tag[:32]

    # Call Telegram
    tg_resp = await _telegram_create_invite_link(chat_id, name_tag)
    if "error" in tg_resp:
        return {"error": f"Telegram: {tg_resp['error']}"}
    invite_link = tg_resp["invite_link"]

    # Save to DB
    try:
        async with get_session() as s:
            r = await s.execute(sql_text(
                """
                INSERT INTO marketing_invite_links
                  (marketer, platform, group_slug, group_chat_id, invite_link, name_tag, created_by)
                VALUES (:m, :p, CAST(:slug AS groupslug), :cid, :link, :tag, :by)
                RETURNING id
                """
            ), {
                "m": marketer, "p": platform, "slug": slug, "cid": chat_id,
                "link": invite_link, "tag": name_tag, "by": created_by,
            })
            link_id = r.scalar_one()
            await s.commit()
    except Exception as exc:
        logger.exception("DB insert failed: %s", exc)
        return {"error": f"DB save failed: {str(exc)[:200]}"}

    return {
        "ok": True, "id": link_id, "marketer": marketer, "platform": platform,
        "group_slug": slug, "group_title": title,
        "invite_link": invite_link, "name_tag": name_tag,
    }


# =========================================================
# TOOL 2: marketing_stats
# =========================================================
_WINDOW_DAYS = {"7d": 7, "30d": 30, "lifetime": None}


async def marketing_stats(
    marketer: Optional[str] = None,
    platform: Optional[str] = None,
    window: str = "30d",
) -> dict:
    """Get conversion stats.

    - joins (all-time count from joins table)
    - paid_7d / paid_30d / paid_lifetime — distinct users who paid within window
    - revenue_window — total revenue from those users (only counted once per window)
    - arpu_window — revenue / joins
    - avg_days_to_pay — average days between join and first payment

    Args:
        marketer: filter by marketer (None = all)
        platform: filter by platform (None = all)
        window: '7d' / '30d' / 'lifetime' (default 30d)
    """
    if window not in _WINDOW_DAYS:
        return {"error": f"invalid window '{window}' — use 7d / 30d / lifetime"}
    days = _WINDOW_DAYS[window]

    # Build WHERE clause for filters
    where_parts = []
    params = {}
    if marketer:
        # normalize
        m = marketer.strip().lower()
        if m == "ivy":
            marketer = "Ivy"
        elif m == "wasu":
            marketer = "Wasu"
        elif m == "pai":
            marketer = "Pai"
        where_parts.append("l.marketer = :marketer")
        params["marketer"] = marketer
    if platform:
        where_parts.append("LOWER(l.platform) = LOWER(:platform)")
        params["platform"] = platform
    where_sql = " AND ".join(where_parts) if where_parts else "1=1"

    # Conversion: a join "converts" if the same telegram_id paid (status CONFIRMED, amount > 0)
    # WITHIN `days` of joining (or anytime for 'lifetime')
    if days is None:
        time_cond = ""
    else:
        time_cond = f"AND (p.created_at - j.joined_at) <= interval '{int(days)} days'"

    async with get_session() as s:
        # Per-marketer-platform breakdown
        rows = (await s.execute(sql_text(f"""
            WITH joins AS (
                SELECT l.marketer, l.platform, l.group_slug::text AS group_slug,
                       j.telegram_id, j.joined_at,
                       (SELECT MIN(p.created_at) FROM payments p
                          JOIN users u2 ON u2.id = p.user_id
                          WHERE u2.telegram_id = j.telegram_id
                            AND p.status = 'CONFIRMED'
                            AND p.amount > 0
                            {time_cond}
                            AND p.created_at >= j.joined_at) AS first_pay_at,
                       (SELECT COALESCE(SUM(p.amount),0) FROM payments p
                          JOIN users u3 ON u3.id = p.user_id
                          WHERE u3.telegram_id = j.telegram_id
                            AND p.status = 'CONFIRMED'
                            AND p.amount > 0
                            {time_cond}
                            AND p.created_at >= j.joined_at) AS total_paid
                FROM marketing_invite_links l
                JOIN marketing_invite_joins j ON j.link_id = l.id
                WHERE {where_sql}
            )
            SELECT marketer, platform,
                   COUNT(*) AS joins,
                   COUNT(*) FILTER (WHERE first_pay_at IS NOT NULL) AS paid_users,
                   COALESCE(SUM(total_paid), 0) AS revenue,
                   AVG(EXTRACT(EPOCH FROM (first_pay_at - joined_at)) / 86400.0)
                     FILTER (WHERE first_pay_at IS NOT NULL) AS avg_days_to_pay
            FROM joins
            GROUP BY marketer, platform
            ORDER BY revenue DESC, joins DESC
        """), params)).fetchall()

        breakdown = []
        total_joins = 0
        total_paid = 0
        total_rev = 0.0
        for r in rows:
            j = int(r.joins or 0)
            pu = int(r.paid_users or 0)
            rv = float(r.revenue or 0)
            arpu = rv / j if j > 0 else 0.0
            cvr = (pu / j * 100) if j > 0 else 0.0
            atp = float(r.avg_days_to_pay or 0)
            breakdown.append({
                "marketer": r.marketer, "platform": r.platform,
                "joins": j, "paid": pu, "revenue": rv,
                "arpu": round(arpu, 2),
                "conversion_pct": round(cvr, 2),
                "avg_days_to_pay": round(atp, 1),
            })
            total_joins += j
            total_paid += pu
            total_rev += rv

        total_arpu = total_rev / total_joins if total_joins > 0 else 0
        total_cvr = (total_paid / total_joins * 100) if total_joins > 0 else 0

    return {
        "window": window,
        "filter": {"marketer": marketer, "platform": platform},
        "totals": {
            "joins": total_joins, "paid": total_paid,
            "revenue": round(total_rev, 2),
            "arpu": round(total_arpu, 2),
            "conversion_pct": round(total_cvr, 2),
        },
        "breakdown": breakdown,
    }


# =========================================================
# TOOL 3: marketing_links_list
# =========================================================
async def marketing_links_list(
    marketer: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """List active marketing links."""
    where_parts = ["l.is_revoked = false"]
    params = {"lim": int(limit)}
    if marketer:
        m = marketer.strip().lower()
        if m == "ivy":
            marketer = "Ivy"
        elif m == "wasu":
            marketer = "Wasu"
        elif m == "pai":
            marketer = "Pai"
        where_parts.append("l.marketer = :marketer")
        params["marketer"] = marketer
    where_sql = " AND ".join(where_parts)

    async with get_session() as s:
        rows = (await s.execute(sql_text(f"""
            SELECT l.id, l.marketer, l.platform, l.group_slug::text AS group_slug,
                   l.invite_link, l.name_tag, l.created_at,
                   (SELECT COUNT(*) FROM marketing_invite_joins j WHERE j.link_id = l.id) AS join_count
            FROM marketing_invite_links l
            WHERE {where_sql}
            ORDER BY l.created_at DESC
            LIMIT :lim
        """), params)).fetchall()

    return {
        "count": len(rows),
        "links": [
            {
                "id": r.id, "marketer": r.marketer, "platform": r.platform,
                "group_slug": r.group_slug,
                "invite_link": r.invite_link,
                "joins": int(r.join_count or 0),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }

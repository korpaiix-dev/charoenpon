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



# =========================================================
# TOOL 4: marketing_heatmap — peak times analysis
# =========================================================
_DOW_TH = {0: "อา", 1: "จ", 2: "อ", 3: "พ", 4: "พฤ", 5: "ศ", 6: "ส"}


async def marketing_heatmap(
    marketer: Optional[str] = None,
    platform: Optional[str] = None,
    window_days: int = 30,
) -> dict:
    """Get join heatmap by day-of-week × hour (BKK timezone).
    
    Returns top peak times.
    """
    where_parts = []
    params = {"days": int(window_days)}
    if marketer:
        m = marketer.strip().lower()
        if m == "ivy": marketer = "Ivy"
        elif m == "wasu": marketer = "Wasu"
        elif m == "pai": marketer = "Pai"
        where_parts.append("l.marketer = :marketer")
        params["marketer"] = marketer
    if platform:
        where_parts.append("LOWER(l.platform) = LOWER(:platform)")
        params["platform"] = platform
    where_sql = " AND ".join(where_parts) if where_parts else "1=1"

    async with get_session() as s:
        rows = (await s.execute(sql_text(f"""
            SELECT
              EXTRACT(DOW FROM (j.joined_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok'))::int AS dow,
              EXTRACT(HOUR FROM (j.joined_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok'))::int AS hour,
              COUNT(*)::int AS joins
            FROM marketing_invite_joins j
            JOIN marketing_invite_links l ON l.id = j.link_id
            WHERE j.joined_at >= now() - (:days * interval '1 day')
              AND {where_sql}
            GROUP BY 1, 2
            ORDER BY joins DESC
            LIMIT 10
        """), params)).fetchall()

    if not rows:
        return {"window_days": window_days, "filter": {"marketer": marketer, "platform": platform},
                "buckets": [], "message": "ไม่มีข้อมูล join ในช่วงนี้"}

    buckets = []
    for r in rows:
        dow_str = _DOW_TH.get(r.dow, "?")
        buckets.append({
            "dow": r.dow,
            "dow_th": dow_str,
            "hour": r.hour,
            "hour_label": f"{r.hour:02d}:00-{(r.hour+1)%24:02d}:00",
            "joins": r.joins,
        })

    return {
        "window_days": window_days,
        "filter": {"marketer": marketer, "platform": platform},
        "buckets": buckets,
    }



def _current_year_month() -> str:
    """Return YYYY-MM for current month (BKK timezone)."""
    return (dt.datetime.utcnow() + dt.timedelta(hours=7)).strftime("%Y-%m")


# =========================================================
# TOOL 5: set_marketing_goal
# =========================================================
async def set_marketing_goal(
    marketer: str,
    target_revenue: float,
    target_joins: Optional[int] = None,
    year_month: Optional[str] = None,
) -> dict:
    """Set monthly target for a marketer.
    
    Args:
        marketer: Ivy / Wasu / Pai
        target_revenue: target ฿ for the month
        target_joins: optional target join count
        year_month: 'YYYY-MM' (default = current month BKK)
    """
    m = (marketer or "").strip()
    if m.lower() in ("ivy", "ไอวี่"): marketer = "Ivy"
    elif m.lower() in ("wasu", "วสุ"): marketer = "Wasu"
    elif m.lower() in ("pai", "ไผ่"): marketer = "Pai"
    else:
        return {"error": f"unknown marketer '{m}'"}
    
    ym = year_month or _current_year_month()
    target_joins = int(target_joins) if target_joins is not None else 0
    
    try:
        async with get_session() as s:
            await s.execute(sql_text("""
                INSERT INTO marketing_goals (marketer, year_month, target_revenue, target_joins)
                VALUES (:m, :ym, :tr, :tj)
                ON CONFLICT (marketer, year_month) DO UPDATE
                SET target_revenue = EXCLUDED.target_revenue,
                    target_joins = EXCLUDED.target_joins,
                    updated_at = now()
            """), {"m": marketer, "ym": ym, "tr": float(target_revenue), "tj": target_joins})
            await s.commit()
        return {"ok": True, "marketer": marketer, "year_month": ym,
                "target_revenue": float(target_revenue), "target_joins": target_joins}
    except Exception as exc:
        return {"error": f"DB save failed: {str(exc)[:200]}"}


# =========================================================
# TOOL 6: get_marketing_goal — with progress bar
# =========================================================
async def get_marketing_goal(
    marketer: Optional[str] = None,
    year_month: Optional[str] = None,
) -> dict:
    """Get goal + current progress for a marketer (or all marketers if not specified)."""
    m = (marketer or "").strip()
    if m.lower() in ("ivy", "ไอวี่"): marketer = "Ivy"
    elif m.lower() in ("wasu", "วสุ"): marketer = "Wasu"
    elif m.lower() in ("pai", "ไผ่"): marketer = "Pai"
    elif m:
        return {"error": f"unknown marketer '{m}'"}

    ym = year_month or _current_year_month()
    
    where_marketer = ""
    params = {"ym": ym}
    if marketer:
        where_marketer = "AND g.marketer = :m"
        params["m"] = marketer

    async with get_session() as s:
        rows = (await s.execute(sql_text(f"""
            WITH goals AS (
                SELECT marketer, year_month, target_revenue, target_joins
                FROM marketing_goals g
                WHERE year_month = :ym {where_marketer}
            ),
            actuals AS (
                SELECT l.marketer,
                       COUNT(DISTINCT j.id)::int AS joins,
                       COUNT(DISTINCT p.id) FILTER (WHERE p.status = 'CONFIRMED' AND p.amount > 0)::int AS paid,
                       COALESCE(SUM(p.amount) FILTER (WHERE p.status = 'CONFIRMED' AND p.amount > 0), 0) AS revenue
                FROM marketing_invite_links l
                LEFT JOIN marketing_invite_joins j ON j.link_id = l.id
                  AND date_trunc('month', j.joined_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Bangkok')
                    = date_trunc('month', to_timestamp(:ym, 'YYYY-MM'))
                LEFT JOIN users u ON u.telegram_id = j.telegram_id
                LEFT JOIN payments p ON p.user_id = u.id
                  AND p.created_at >= j.joined_at
                  AND (p.created_at - j.joined_at) <= interval '30 days'
                GROUP BY l.marketer
            )
            SELECT
              COALESCE(g.marketer, a.marketer) AS marketer,
              COALESCE(g.target_revenue, 0) AS target_revenue,
              COALESCE(g.target_joins, 0) AS target_joins,
              COALESCE(a.revenue, 0) AS actual_revenue,
              COALESCE(a.joins, 0) AS actual_joins,
              COALESCE(a.paid, 0) AS actual_paid
            FROM goals g
            FULL OUTER JOIN actuals a ON a.marketer = g.marketer
            ORDER BY COALESCE(g.marketer, a.marketer)
        """), params)).fetchall()

    results = []
    for r in rows:
        tr = float(r.target_revenue or 0)
        ar = float(r.actual_revenue or 0)
        rev_pct = (ar / tr * 100) if tr > 0 else 0
        bar = _progress_bar(rev_pct)
        results.append({
            "marketer": r.marketer,
            "year_month": ym,
            "target_revenue": tr,
            "actual_revenue": ar,
            "revenue_pct": round(rev_pct, 1),
            "revenue_bar": bar,
            "target_joins": int(r.target_joins or 0),
            "actual_joins": int(r.actual_joins or 0),
            "actual_paid": int(r.actual_paid or 0),
            "has_goal": tr > 0 or int(r.target_joins or 0) > 0,
        })
    return {"year_month": ym, "marketers": results}


def _progress_bar(pct: float, width: int = 10) -> str:
    """Render a unicode progress bar."""
    pct = max(0, min(pct, 100))
    filled = int(round(pct / 100 * width))
    return "█" * filled + "░" * (width - filled) + f" {pct:.0f}%"


# =========================================================
# TOOL 7: revoke_marketing_link
# =========================================================
async def revoke_marketing_link(
    link_id: int,
    reason: Optional[str] = None,
) -> dict:
    """Revoke a marketing invite link — both in Telegram + DB."""
    try:
        async with get_session() as s:
            row = (await s.execute(sql_text(
                "SELECT id, group_chat_id, invite_link, is_revoked FROM marketing_invite_links WHERE id = :i"
            ), {"i": int(link_id)})).first()
            if not row:
                return {"error": f"link_id {link_id} not found"}
            if row.is_revoked:
                return {"ok": True, "already_revoked": True, "link_id": link_id}

            # Revoke in Telegram
            if GUARDIAN_TOKEN:
                async with httpx.AsyncClient(timeout=15.0) as cli:
                    r = await cli.post(
                        f"https://api.telegram.org/bot{GUARDIAN_TOKEN}/revokeChatInviteLink",
                        json={"chat_id": row.group_chat_id, "invite_link": row.invite_link},
                    )
                    tg_ok = r.json().get("ok", False)
            else:
                tg_ok = False
            
            # Mark in DB regardless of TG result
            await s.execute(sql_text(
                "UPDATE marketing_invite_links SET is_revoked = true, notes = COALESCE(notes,'') || :n WHERE id = :i"
            ), {"i": int(link_id), "n": f"\n[revoked] {reason or 'manual'}"})
            await s.commit()
            
        return {"ok": True, "link_id": link_id, "tg_revoked": tg_ok, "reason": reason}
    except Exception as exc:
        return {"error": f"revoke failed: {str(exc)[:200]}"}



# =========================================================
# TOOL 8: find_customer_attribution
# =========================================================
async def find_customer_attribution(query: str) -> dict:
    """หาว่าลูกค้าคนนี้เข้ามาจากลิ้ง marketing ไหน + จ่ายไปเท่าไหร่.
    
    Args:
        query: telegram_id (number), username, or first_name to match
    
    Returns dict with:
        - customer: {tg_id, name, username, total_spent, rank}
        - marketing_journey: [{link_id, marketer, platform, group, joined_at}]
        - paid_within_30d: bool — ถ้า paid ภายใน 30d ของ join
        - payments: [...]
    """
    q = (query or "").strip()
    if not q:
        return {"error": "query required"}

    async with get_session() as s:
        # Find user
        if q.isdigit():
            r = await s.execute(sql_text(
                "SELECT id, telegram_id, first_name, last_name, username, total_spent, loyalty_rank "
                "FROM users WHERE telegram_id = :tg LIMIT 1"
            ), {"tg": int(q)})
        else:
            r = await s.execute(sql_text(
                "SELECT id, telegram_id, first_name, last_name, username, total_spent, loyalty_rank "
                "FROM users WHERE first_name ILIKE :q OR last_name ILIKE :q OR username ILIKE :q "
                "ORDER BY total_spent DESC LIMIT 1"
            ), {"q": f"%{q}%"})
        urow = r.first()
        if not urow:
            return {"found": False, "query": q}

        user_info = {
            "user_id": urow.id, "tg_id": urow.telegram_id,
            "name": f"{urow.first_name or ''} {urow.last_name or ''}".strip(),
            "username": urow.username,
            "total_spent": float(urow.total_spent or 0),
            "rank": urow.loyalty_rank,
        }

        # All marketing joins for this user
        jr = await s.execute(sql_text("""
            SELECT
              j.id AS join_id, j.joined_at,
              l.id AS link_id, l.marketer, l.platform, l.group_slug::text AS group_slug,
              (SELECT title FROM group_registry WHERE slug = l.group_slug) AS group_title,
              l.invite_link
            FROM marketing_invite_joins j
            JOIN marketing_invite_links l ON l.id = j.link_id
            WHERE j.telegram_id = :tg
            ORDER BY j.joined_at DESC
        """), {"tg": urow.telegram_id})
        joins = []
        for jrow in jr.fetchall():
            joins.append({
                "join_id": jrow.join_id,
                "joined_at": jrow.joined_at.isoformat() if jrow.joined_at else None,
                "link_id": jrow.link_id,
                "marketer": jrow.marketer,
                "platform": jrow.platform,
                "group_slug": jrow.group_slug,
                "group_title": jrow.group_title,
                "invite_link": jrow.invite_link,
            })

        # Confirmed payments
        pr = await s.execute(sql_text("""
            SELECT p.id, p.amount, p.status, pk.tier::text AS tier, p.created_at
            FROM payments p
            LEFT JOIN packages pk ON pk.id = p.package_id
            WHERE p.user_id = :uid AND p.status = 'CONFIRMED' AND p.amount > 0
            ORDER BY p.created_at DESC
            LIMIT 20
        """), {"uid": urow.id})
        payments = [
            {"id": pr2.id, "amount": float(pr2.amount), "tier": pr2.tier,
             "created_at": pr2.created_at.isoformat() if pr2.created_at else None}
            for pr2 in pr.fetchall()
        ]

        # Attribution: first paid within 30d of any join
        attribution = None
        if joins and payments:
            for j in joins:
                j_at = j["joined_at"]
                for p in payments:
                    if not p["created_at"] or not j_at: continue
                    import datetime as _dt
                    j_dt = _dt.datetime.fromisoformat(j_at.replace("Z",""))
                    p_dt = _dt.datetime.fromisoformat(p["created_at"].replace("Z",""))
                    if p_dt >= j_dt and (p_dt - j_dt).days <= 30:
                        attribution = {
                            "link_id": j["link_id"],
                            "marketer": j["marketer"],
                            "platform": j["platform"],
                            "group_title": j["group_title"],
                            "first_paid_at": p["created_at"],
                            "days_to_pay": (p_dt - j_dt).days,
                            "amount": p["amount"],
                            "tier": p["tier"],
                        }
                        break
                if attribution: break

        return {
            "found": True,
            "customer": user_info,
            "marketing_joins": joins,
            "payments": payments,
            "attribution": attribution,  # None = ไม่ได้มาจาก marketing link
        }

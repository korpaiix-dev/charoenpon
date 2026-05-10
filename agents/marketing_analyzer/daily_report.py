"""Marketing Analytics — Daily Report Generator.

รวบรวม KPIs จากทุกระบบ marketing:
- Flash Sale, COMEBACK DM, Trial DM, Trial Upsell, Teaser (มิน), Payments, Subscriptions
- AI วิเคราะห์ผ่าน OpenRouter (Gemini Flash)
- ส่ง report → Discord #daily-report + Telegram admin group
- เขียน action items → /root/charoenpon/data/marketing_actions.json

Scheduled: ทุกวัน 23:30 ไทย (16:30 UTC)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import Integer, func, select, and_, cast

from shared.api_cost_tracker import call_openrouter, normalize_model
from shared.database import get_session
from shared.models import (
    ComebackDmLog,
    ContentQueue,
    FlashSale,
    Package,
    PackageTier,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    TeaserClick,
    TrialDmLog,
    User,
)

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))

# Facebook Manager data
FB_CUSTOMERS_FILE = "/root/charoenpon/fb-manager/data/customers.json"
FB_POST_LOG_FILE = "/root/charoenpon/fb-manager/data/post_log.json"

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
AI_MODEL = normalize_model("anthropic/claude-sonnet-4-20250514")

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CH_DAILY_REPORT = os.environ.get("DISCORD_CH_DAILY_REPORT", "")

ADMIN_BOT_TOKEN = os.environ.get("ADMIN_BOT_TOKEN", "")
ADMIN_GROUP_CHAT_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))

ACTIONS_FILE = Path("/root/charoenpon/data/marketing_actions.json")


# ─── Data Collection ─────────────────────────────────────────────────────────


async def _get_flash_sale_data(today_start: datetime, today_end: datetime) -> dict:
    """Flash Sale KPIs."""
    async with get_session() as session:
        result = await session.execute(
            select(FlashSale).where(
                and_(
                    FlashSale.starts_at >= today_start,
                    FlashSale.starts_at < today_end,
                )
            ).order_by(FlashSale.starts_at.desc()).limit(1)
        )
        sale = result.scalar_one_or_none()

        if not sale:
            # ลองหา flash sale ที่ active อยู่วันนี้
            result = await session.execute(
                select(FlashSale).where(
                    and_(
                        FlashSale.starts_at < today_end,
                        FlashSale.ends_at > today_start,
                    )
                ).order_by(FlashSale.starts_at.desc()).limit(1)
            )
            sale = result.scalar_one_or_none()

    if not sale:
        return {
            "sold_slots": 0,
            "total_slots": 0,
            "flash_price": 0,
            "revenue": 0,
            "has_sale": False,
        }

    return {
        "sold_slots": sale.sold_slots,
        "total_slots": sale.total_slots,
        "flash_price": float(sale.flash_price),
        "revenue": float(sale.flash_price * sale.sold_slots),
        "has_sale": True,
    }


async def _get_comeback_dm_data(today_start_utc: datetime) -> dict:
    """COMEBACK DM KPIs."""
    async with get_session() as session:
        result = await session.execute(
            select(
                func.count(ComebackDmLog.id).label("sent"),
                func.coalesce(
                    func.sum(cast(ComebackDmLog.responded, Integer)), 0
                ).label("responded"),
                func.coalesce(
                    func.sum(cast(ComebackDmLog.purchased, Integer)), 0
                ).label("purchased"),
            ).where(ComebackDmLog.sent_at >= today_start_utc)
        )
        row = result.one()

    sent = row.sent or 0
    responded = row.responded or 0
    purchased = row.purchased or 0
    rate = round((purchased / sent * 100), 1) if sent > 0 else 0.0

    return {
        "sent": sent,
        "responded": responded,
        "purchased": purchased,
        "conversion_rate": rate,
    }


async def _get_trial_dm_data(today_start_utc: datetime) -> dict:
    """Trial ฿99 DM KPIs."""
    async with get_session() as session:
        result = await session.execute(
            select(
                func.count(TrialDmLog.id).label("sent"),
                func.coalesce(
                    func.sum(cast(TrialDmLog.clicked, Integer)), 0
                ).label("clicked"),
                func.coalesce(
                    func.sum(cast(TrialDmLog.purchased, Integer)), 0
                ).label("purchased"),
            ).where(TrialDmLog.sent_at >= today_start_utc)
        )
        row = result.one()

    sent = row.sent or 0
    clicked = row.clicked or 0
    purchased = row.purchased or 0
    rate = round((purchased / sent * 100), 1) if sent > 0 else 0.0

    return {
        "sent": sent,
        "clicked": clicked,
        "purchased": purchased,
        "conversion_rate": rate,
    }


async def _get_trial_upsell_data(today_start_utc: datetime) -> dict:
    """Trial → VIP Upsell KPIs.

    นับ trial ที่หมดอายุวันนี้ vs คนที่ซื้อ VIP เต็มหลัง trial
    """
    async with get_session() as session:
        # หา trial package
        pkg_result = await session.execute(
            select(Package).where(Package.tier == PackageTier.TIER_99)
        )
        trial_pkg = pkg_result.scalar_one_or_none()

        if not trial_pkg:
            return {"expired": 0, "converted": 0, "conversion_rate": 0.0}

        # Trial subscriptions ที่หมดอายุวันนี้
        expired_result = await session.execute(
            select(func.count(Subscription.id)).where(
                and_(
                    Subscription.package_id == trial_pkg.id,
                    Subscription.status == SubscriptionStatus.EXPIRED,
                    Subscription.end_date >= today_start_utc,
                )
            )
        )
        expired = expired_result.scalar() or 0

        # คนที่ trial หมด → สมัคร VIP เต็ม (มี subscription active ใน package อื่น)
        # Subquery: user_ids ที่มี expired trial วันนี้
        trial_users = (
            select(Subscription.user_id)
            .where(
                and_(
                    Subscription.package_id == trial_pkg.id,
                    Subscription.status == SubscriptionStatus.EXPIRED,
                    Subscription.end_date >= today_start_utc,
                )
            )
            .scalar_subquery()
        )

        converted_result = await session.execute(
            select(func.count(Subscription.id.distinct())).where(
                and_(
                    Subscription.user_id.in_(trial_users),
                    Subscription.package_id != trial_pkg.id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
            )
        )
        converted = converted_result.scalar() or 0

    rate = round((converted / expired * 100), 1) if expired > 0 else 0.0
    return {"expired": expired, "converted": converted, "conversion_rate": rate}


async def _get_teaser_data(today_start_utc: datetime) -> dict:
    """Content (มิน) Teaser KPIs."""
    async with get_session() as session:
        # Teaser clicks วันนี้
        click_result = await session.execute(
            select(func.count(TeaserClick.id)).where(
                TeaserClick.created_at >= today_start_utc
            )
        )
        clicks = click_result.scalar() or 0

        # Content queue: used vs remaining
        used_result = await session.execute(
            select(func.count(ContentQueue.id)).where(
                and_(
                    ContentQueue.is_used == True,  # noqa: E712
                    ContentQueue.used_at >= today_start_utc,
                )
            )
        )
        used_today = used_result.scalar() or 0

        remaining_result = await session.execute(
            select(func.count(ContentQueue.id)).where(
                ContentQueue.is_used == False  # noqa: E712
            )
        )
        remaining = remaining_result.scalar() or 0

    return {
        "teaser_sent": used_today,
        "clicks": clicks,
        "remaining": remaining,
    }


async def _get_payment_data(today_start_utc: datetime) -> dict:
    """New customer + revenue KPIs."""
    async with get_session() as session:
        # รายได้วันนี้ (confirmed payments)
        rev_result = await session.execute(
            select(
                func.count(Payment.id).label("count"),
                func.coalesce(func.sum(Payment.amount), 0).label("total"),
            ).where(
                and_(
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.verified_at >= today_start_utc,
                )
            )
        )
        rev = rev_result.one()

        # แยกตาม method
        method_result = await session.execute(
            select(
                Payment.method,
                func.count(Payment.id).label("count"),
                func.sum(Payment.amount).label("total"),
            ).where(
                and_(
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.verified_at >= today_start_utc,
                )
            ).group_by(Payment.method)
        )
        by_method = {
            row.method.value: {"count": row.count, "total": float(row.total or 0)}
            for row in method_result.all()
        }

        # รายได้เมื่อวาน (สำหรับเทียบ)
        yesterday_start = today_start_utc - timedelta(days=1)
        yesterday_result = await session.execute(
            select(
                func.coalesce(func.sum(Payment.amount), 0).label("total"),
            ).where(
                and_(
                    Payment.status == PaymentStatus.CONFIRMED,
                    Payment.verified_at >= yesterday_start,
                    Payment.verified_at < today_start_utc,
                )
            )
        )
        yesterday_total = float(yesterday_result.scalar() or 0)

    today_total = float(rev.total or 0)
    diff = today_total - yesterday_total
    pct_change = round((diff / yesterday_total * 100), 1) if yesterday_total > 0 else 0.0

    return {
        "count": rev.count or 0,
        "total": today_total,
        "yesterday_total": yesterday_total,
        "diff": diff,
        "pct_change": pct_change,
        "by_method": by_method,
    }


async def _get_subscription_data(now_utc: datetime, today_start_utc: datetime) -> dict:
    """Subscription KPIs."""
    today_end_utc = today_start_utc + timedelta(days=1)

    async with get_session() as session:
        # Active subscriptions
        active_result = await session.execute(
            select(func.count(Subscription.id)).where(
                and_(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.end_date > now_utc,
                )
            )
        )
        active = active_result.scalar() or 0

        # หมดอายุวันนี้
        expiring_result = await session.execute(
            select(func.count(Subscription.id)).where(
                and_(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.end_date >= today_start_utc,
                    Subscription.end_date < today_end_utc,
                )
            )
        )
        expiring = expiring_result.scalar() or 0

        # สมัครใหม่วันนี้
        new_result = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.created_at >= today_start_utc,
            )
        )
        new_today = new_result.scalar() or 0

    return {
        "active": active,
        "expiring_today": expiring,
        "new_today": new_today,
    }


# ─── AI Analysis ─────────────────────────────────────────────────────────────


async def _get_facebook_data(today_str: str) -> dict:
    """ดึงข้อมูล Facebook Page จาก fb-manager data files"""
    import subprocess

    result = {
        "total_customers": 0,
        "new_today": 0,
        "hot_leads": 0,
        "warm_leads": 0,
        "cold_leads": 0,
        "posts_today": 0,
        "replies_today": 0,
        "top_categories": {},
        "page_followers": 0,
        "avg_likes_per_post": 0,
    }

    # 1. Customer data
    try:
        if os.path.exists(FB_CUSTOMERS_FILE):
            with open(FB_CUSTOMERS_FILE) as f:
                customers = json.load(f)
            result["total_customers"] = len(customers)
            for c in customers.values():
                score = c.get("lead_score", "COLD")
                if score == "HOT":
                    result["hot_leads"] += 1
                elif score == "WARM":
                    result["warm_leads"] += 1
                else:
                    result["cold_leads"] += 1
                if c.get("first_contact", "").startswith(today_str):
                    result["new_today"] += 1
                # Aggregate categories
                for cat, cnt in c.get("categories", {}).items():
                    result["top_categories"][cat] = result["top_categories"].get(cat, 0) + cnt
    except Exception as e:
        logger.warning(f"FB customers read error: {e}")

    # 2. Post log
    try:
        if os.path.exists(FB_POST_LOG_FILE):
            with open(FB_POST_LOG_FILE) as f:
                posts = json.load(f)
            result["posts_today"] = sum(1 for p in posts if p.get("time", "").startswith(today_str))
    except Exception as e:
        logger.warning(f"FB post log read error: {e}")

    # 3. Page stats จาก fb-manager container
    try:
        cmd = [
            "docker", "exec", "charoenpon-fb-manager", "python", "-c",
            "import sys; sys.path.insert(0,'/app'); from fb_api import get_page_info, get_feed; "
            "import json; info=get_page_info(); feed=get_feed(10); "
            "likes=sum(p.get('likes',{}).get('summary',{}).get('total_count',0) for p in feed); "
            "print(json.dumps({'fans':info.get('fan_count',0),'avg_likes':round(likes/max(len(feed),1),1)}))"
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode == 0 and proc.stdout.strip():
            stats = json.loads(proc.stdout.strip())
            result["page_followers"] = stats.get("fans", 0)
            result["avg_likes_per_post"] = stats.get("avg_likes", 0)
    except Exception as e:
        logger.warning(f"FB stats error: {e}")

    return result


async def _ai_analyze(data: dict) -> dict[str, str]:
    """วิเคราะห์ข้อมูล marketing ด้วย AI (Gemini Flash via OpenRouter).

    Returns {"insights": "...", "action_items": "...", "actions_json": {...}}
    """
    if not OPENROUTER_API_KEY:
        return {
            "insights": "⚠️ ไม่มี OPENROUTER_API_KEY — ข้าม AI วิเคราะห์",
            "action_items": "- ตั้งค่า OPENROUTER_API_KEY ใน .env",
            "actions_json": {},
        }

    data_text = json.dumps(data, ensure_ascii=False, indent=2, default=str)

    prompt = f"""คุณคือ Marketing Analyst ของ VIP เจริญพร (ขายสมาชิก Telegram 18+)

วิเคราะห์ข้อมูล marketing วันนี้ (รวม Facebook Page data):
{data_text}

ตอบในรูปแบบนี้:

## วิเคราะห์
สั้นๆ 3-5 bullet points:
1. สิ่งที่ดี (ทำต่อ)
2. สิ่งที่ต้องปรับ (ทำไม + แก้ยังไง)
3. สิ่งที่น่ากังวล (ถ้ามี)
4. วิเคราะห์ Facebook: engagement, lead quality, แนวทางปรับปรุง

## Action Items
ตอบเป็น JSON format:
```json
{{
  "content_bot": ["action 1", "action 2"],
  "sales_bot": ["action 1"],
  "flash_sale": ["action 1"],
  "trial": ["action 1"],
  "facebook": ["action 1", "action 2"],
  "summary": "สรุป 1 บรรทัด"
}}
```

ตอบสั้นกระชับ ใช้ภาษาไทย"""

    try:
        data = await call_openrouter(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            caller="marketing_analyzer/daily_report",
            temperature=0.3,
            max_tokens=1024,
            metadata={"report_type": "daily_marketing_analysis"},
        )
        ai_text = data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.error("AI analysis failed: %s", exc)
        return {
            "insights": f"⚠️ AI วิเคราะห์ล้มเหลว: {exc}",
            "action_items": "- ตรวจสอบ OpenRouter API",
            "actions_json": {},
        }

    # Parse AI response
    insights = ""
    action_items_text = ""
    actions_json = {}

    # แยก ## วิเคราะห์ กับ ## Action Items
    if "## วิเคราะห์" in ai_text:
        parts = ai_text.split("## Action Items")
        insights = parts[0].replace("## วิเคราะห์", "").strip()
        if len(parts) > 1:
            action_part = parts[1]
            # Extract JSON from code block
            if "```json" in action_part:
                json_str = action_part.split("```json")[1].split("```")[0].strip()
                try:
                    actions_json = json.loads(json_str)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse AI action items JSON")

            # สร้าง action_items_text จาก JSON
            if actions_json:
                lines = []
                for agent, items in actions_json.items():
                    if agent == "summary":
                        continue
                    if isinstance(items, list):
                        for item in items:
                            lines.append(f"• [{agent}] {item}")
                action_items_text = "\n".join(lines)
            else:
                action_items_text = action_part.strip()
    else:
        # Fallback: ใช้ response ทั้งหมดเป็น insights
        insights = ai_text.strip()
        action_items_text = "- ดูรายละเอียดจาก AI วิเคราะห์ด้านบน"

    return {
        "insights": insights,
        "action_items": action_items_text,
        "actions_json": actions_json,
    }


# ─── Report Generation ───────────────────────────────────────────────────────


async def generate_daily_marketing_report() -> dict[str, Any]:
    """รวบรวม KPIs ทุกวัน + AI วิเคราะห์.

    Returns dict with all data + formatted report text.
    """
    now_th = datetime.now(TH_TZ)
    now_utc = datetime.now(timezone.utc)

    # DB ใช้ naive datetime (UTC)
    today_start_utc = now_utc.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).replace(tzinfo=None)
    today_end_utc = (today_start_utc + timedelta(days=1))
    now_naive = now_utc.replace(tzinfo=None)

    # ─── Collect Data ───
    flash_sale = await _get_flash_sale_data(today_start_utc, today_end_utc)
    comeback_dm = await _get_comeback_dm_data(today_start_utc)
    trial_dm = await _get_trial_dm_data(today_start_utc)
    trial_upsell = await _get_trial_upsell_data(today_start_utc)
    teaser = await _get_teaser_data(today_start_utc)
    payments = await _get_payment_data(today_start_utc)
    subscriptions = await _get_subscription_data(now_naive, today_start_utc)

    # Facebook Page data
    facebook = await _get_facebook_data(now_th.strftime("%Y-%m-%d"))

    all_data = {
        "date": now_th.strftime("%Y-%m-%d"),
        "flash_sale": flash_sale,
        "comeback_dm": comeback_dm,
        "trial_dm": trial_dm,
        "trial_upsell": trial_upsell,
        "teaser": teaser,
        "payments": payments,
        "subscriptions": subscriptions,
        "facebook": facebook,
    }

    # ─── AI Analysis ───
    ai_result = await _ai_analyze(all_data)

    # ─── Format Report ───
    diff_sign = "+" if payments["diff"] >= 0 else ""
    date_str = now_th.strftime("%d/%m/%Y")

    report = (
        f"📊 รายงาน Marketing ประจำวัน — {date_str}\n"
        f"\n"
        f"💰 รายได้วันนี้: ฿{payments['total']:,.0f} ({diff_sign}฿{payments['diff']:,.0f} จากเมื่อวาน {payments['pct_change']:+.1f}%)\n"
        f"👥 ลูกค้าใหม่: {payments['count']} คน\n"
    )

    # Flash Sale
    if flash_sale["has_sale"]:
        report += (
            f"\n━━━ Flash Sale ━━━\n"
            f"🔥 ขายได้: {flash_sale['sold_slots']}/{flash_sale['total_slots']} slot = ฿{flash_sale['revenue']:,.0f}\n"
        )
    else:
        report += f"\n━━━ Flash Sale ━━━\n🔥 ไม่มี Flash Sale วันนี้\n"

    # COMEBACK DM
    report += (
        f"\n━━━ COMEBACK DM ━━━\n"
        f"📩 ส่ง: {comeback_dm['sent']} | ตอบ: {comeback_dm['responded']} | สมัคร: {comeback_dm['purchased']}\n"
        f"📈 Conversion: {comeback_dm['conversion_rate']}%\n"
    )

    # Trial DM
    report += (
        f"\n━━━ Trial ฿99 DM ━━━\n"
        f"📩 ส่ง: {trial_dm['sent']} | คลิก: {trial_dm['clicked']} | สมัคร: {trial_dm['purchased']}\n"
        f"📈 Conversion: {trial_dm['conversion_rate']}%\n"
    )

    # Trial → VIP Upsell
    report += (
        f"\n━━━ Trial → VIP Upsell ━━━\n"
        f"🔄 Trial หมด: {trial_upsell['expired']} | สมัคร VIP: {trial_upsell['converted']}\n"
        f"📈 Conversion: {trial_upsell['conversion_rate']}%\n"
    )

    # Content (มิน)
    report += (
        f"\n━━━ Content (มิน) ━━━\n"
        f"📸 Teaser ส่ง: {teaser['teaser_sent']} ครั้ง | คลิกลิงก์: {teaser['clicks']}\n"
        f"📦 Content Queue: {teaser['remaining']} เหลือ\n"
    )

    # Subscriptions
    report += (
        f"\n━━━ Subscription สรุป ━━━\n"
        f"✅ Active: {subscriptions['active']} | ⏳ หมดวันนี้: {subscriptions['expiring_today']} | 🆕 ใหม่: {subscriptions['new_today']}\n"
    )

    # Facebook Page
    report += (
        f"\n━━━ 📘 Facebook Page ━━━\n"
        f"👥 Followers: {facebook['page_followers']:,}\n"
        f"📝 โพสต์วันนี้: {facebook['posts_today']}\n"
        f"❤️ Avg Likes/Post: {facebook['avg_likes_per_post']}\n"
        f"💬 ลูกค้าทัก Messenger: {facebook['total_customers']} คน (ใหม่วันนี้: {facebook['new_today']})\n"
        f"🔴 HOT: {facebook['hot_leads']} | 🟡 WARM: {facebook['warm_leads']} | 🟢 COLD: {facebook['cold_leads']}\n"
    )
    if facebook["top_categories"]:
        cats = ", ".join(f"{k}: {v}" for k, v in facebook["top_categories"].items())
        report += f"📊 คำถามที่ถามบ่อย: {cats}\n"

    # AI Analysis
    report += (
        f"\n━━━ 🧠 AI วิเคราะห์ ━━━\n"
        f"{ai_result['insights']}\n"
    )

    # Action Items
    report += (
        f"\n━━━ 📋 Action Items ━━━\n"
        f"{ai_result['action_items']}\n"
    )

    return {
        "data": all_data,
        "ai_result": ai_result,
        "report_text": report,
        "date": now_th.strftime("%Y-%m-%d"),
    }


# ─── Save Action Items ───────────────────────────────────────────────────────


def save_action_items(date: str, actions_json: dict) -> None:
    """เขียน action items ลง JSON file ให้ agent อื่นอ่านได้."""
    ACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)

    action_data = {
        "date": date,
        "content_bot": actions_json.get("content_bot", []),
        "sales_bot": actions_json.get("sales_bot", []),
        "flash_sale": actions_json.get("flash_sale", []),
        "trial": actions_json.get("trial", []),
        "summary": actions_json.get("summary", ""),
        "updated_at": datetime.now(TH_TZ).isoformat(),
    }

    ACTIONS_FILE.write_text(
        json.dumps(action_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Action items saved to %s", ACTIONS_FILE)


# ─── Send Report ─────────────────────────────────────────────────────────────


async def send_to_discord(report_text: str) -> bool:
    """ส่ง report ไป Discord #daily-report."""
    if not DISCORD_BOT_TOKEN or not DISCORD_CH_DAILY_REPORT:
        logger.warning("Discord not configured, skipping")
        return False

    # Discord message limit: 2000 chars
    # ถ้ายาวเกิน → แบ่งเป็นหลาย message
    chunks = _split_message(report_text, max_len=1900)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for i, chunk in enumerate(chunks):
                resp = await client.post(
                    f"https://discord.com/api/v10/channels/{DISCORD_CH_DAILY_REPORT}/messages",
                    headers={
                        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                        "Content-Type": "application/json",
                    },
                    json={"content": chunk},
                )
                resp.raise_for_status()
                if i < len(chunks) - 1:
                    import asyncio
                    await asyncio.sleep(0.5)
        logger.info("Daily marketing report sent to Discord")
        return True
    except Exception as exc:
        logger.error("Failed to send Discord report: %s", exc)
        return False


async def send_to_telegram_admin(report_text: str) -> bool:
    """ส่ง report ไป Telegram admin group."""
    if not ADMIN_BOT_TOKEN:
        logger.warning("ADMIN_BOT_TOKEN not configured, skipping Telegram")
        return False

    # Telegram message limit: 4096 chars
    chunks = _split_message(report_text, max_len=4000)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for i, chunk in enumerate(chunks):
                resp = await client.post(
                    f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": ADMIN_GROUP_CHAT_ID,
                        "text": chunk,
                        "parse_mode": "HTML",
                    },
                )
                resp.raise_for_status()
                if i < len(chunks) - 1:
                    import asyncio
                    await asyncio.sleep(0.5)
        logger.info("Daily marketing report sent to Telegram admin group")
        return True
    except Exception as exc:
        logger.error("Failed to send Telegram report: %s", exc)
        return False


def _split_message(text: str, max_len: int = 1900) -> list[str]:
    """แบ่ง message ยาวเป็น chunks."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    lines = text.split("\n")
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line

    if current:
        chunks.append(current)

    return chunks


# ─── Main Entry Point ────────────────────────────────────────────────────────


async def run_daily_marketing_report() -> None:
    """Main entry: generate → AI analyze → send → save actions.

    เรียกจาก scheduler (Discord Bot หรือ standalone).
    """
    logger.info("🚀 Starting daily marketing report generation...")

    try:
        result = await generate_daily_marketing_report()
    except Exception as exc:
        logger.error("Failed to generate marketing report: %s", exc)
        # ส่ง error notification
        error_msg = f"❌ Marketing Daily Report ล้มเหลว: {exc}"
        await send_to_discord(error_msg)
        await send_to_telegram_admin(error_msg)
        return

    report_text = result["report_text"]

    # ส่ง report
    await send_to_discord(report_text)
    await send_to_telegram_admin(report_text)

    # Save action items
    actions_json = result["ai_result"].get("actions_json", {})
    if actions_json:
        save_action_items(result["date"], actions_json)
    else:
        # Save empty actions
        save_action_items(result["date"], {
            "content_bot": [],
            "sales_bot": [],
            "flash_sale": [],
            "trial": [],
            "summary": "ไม่มี action items จาก AI วิเคราะห์",
        })

    logger.info("✅ Daily marketing report completed for %s", result["date"])

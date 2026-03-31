"""Marketing Brain — ศูนย์กลางวิเคราะห์การตลาดอัตโนมัติ.

Weekly (Sunday 20:00 Thai):
1. รวบรวม metrics จากทุกระบบ (7 วัน)
2. AI วิเคราะห์ + สร้าง insights
3. Auto-adjust parameters → marketing_config table
4. ส่งรายงานสรุปไป Admin Group
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from telegram import Bot
from telegram.ext import ContextTypes

from shared.api_cost_tracker import call_openrouter
from shared.database import get_session

logger = logging.getLogger(__name__)

TH_TZ = timezone(timedelta(hours=7))
ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_CHAT_ID", "-1003830920430"))
AI_MODEL = "anthropic/claude-haiku-3-5"


async def _ensure_tables() -> None:
    """Create marketing_config table if not exists."""
    async with get_session() as session:
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS marketing_config (
                key VARCHAR(100) PRIMARY KEY,
                value JSONB NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_by VARCHAR(50) NOT NULL DEFAULT 'brain'
            )
        """))


async def _query_rows(sql: str, params: dict | None = None):
    async with get_session() as session:
        result = await session.execute(text(sql), params or {})
        return result.fetchall()


async def _query_scalar(sql: str, params: dict | None = None):
    async with get_session() as session:
        result = await session.execute(text(sql), params or {})
        return result.scalar() or 0


async def collect_weekly_metrics() -> dict:
    """Gather all marketing metrics for the last 7 days."""
    now_th = datetime.now(TH_TZ)
    cutoff = (now_th - timedelta(days=7)).astimezone(timezone.utc).replace(tzinfo=None)
    metrics: dict = {}

    # --- Teaser: clicks per round_time, per caption_style, conversion ---
    try:
        clicks_by_round = await _query_rows(
            """SELECT round_time, COUNT(*) as cnt,
                      SUM(CASE WHEN converted THEN 1 ELSE 0 END) as conv
               FROM teaser_clicks WHERE created_at >= :cutoff
               GROUP BY round_time ORDER BY cnt DESC""",
            {"cutoff": cutoff},
        )
        metrics["teaser_by_round"] = [
            {"round_time": r.round_time, "clicks": r.cnt, "converted": r.conv}
            for r in clicks_by_round
        ]

        style_rows = await _query_rows(
            """SELECT tpl.caption_style, COUNT(tc.id) as clicks,
                      SUM(CASE WHEN tc.converted THEN 1 ELSE 0 END) as conv
               FROM teaser_post_log tpl
               LEFT JOIN teaser_clicks tc ON tc.round_time = tpl.round_time
                    AND tc.group_index = tpl.group_index
                    AND tc.created_at >= :cutoff
               WHERE tpl.posted_at >= :cutoff AND tpl.caption_style IS NOT NULL
               GROUP BY tpl.caption_style""",
            {"cutoff": cutoff},
        )
        metrics["teaser_by_style"] = [
            {"style": r.caption_style, "clicks": r.clicks or 0, "converted": r.conv or 0}
            for r in style_rows
        ]
    except Exception as exc:
        logger.error("Brain — teaser metrics failed: %s", exc)
        metrics["teaser_by_round"] = []
        metrics["teaser_by_style"] = []

    # --- Comeback DM: response/purchase rate per variant, per discount ---
    try:
        comeback_rows = await _query_rows(
            """SELECT variant, discount_pct,
                      COUNT(*) as sent,
                      SUM(CASE WHEN responded THEN 1 ELSE 0 END) as responded,
                      SUM(CASE WHEN purchased THEN 1 ELSE 0 END) as purchased
               FROM comeback_dm_log WHERE sent_at >= :cutoff
               GROUP BY variant, discount_pct""",
            {"cutoff": cutoff},
        )
        metrics["comeback"] = [
            {
                "variant": r.variant,
                "discount_pct": r.discount_pct,
                "sent": r.sent,
                "responded": r.responded,
                "purchased": r.purchased,
            }
            for r in comeback_rows
        ]
    except Exception as exc:
        logger.error("Brain — comeback metrics failed: %s", exc)
        metrics["comeback"] = []

    # --- Lead Follow-up: conversion per round, per segment ---
    try:
        lead_rows = await _query_rows(
            """SELECT round, segment,
                      COUNT(*) as sent,
                      SUM(CASE WHEN purchased THEN 1 ELSE 0 END) as purchased
               FROM lead_followup_log WHERE sent_at >= :cutoff
               GROUP BY round, segment""",
            {"cutoff": cutoff},
        )
        metrics["lead_followup"] = [
            {"round": r.round, "segment": r.segment, "sent": r.sent, "purchased": r.purchased}
            for r in lead_rows
        ]
    except Exception as exc:
        logger.error("Brain — lead followup metrics failed: %s", exc)
        metrics["lead_followup"] = []

    # --- Referral: completion rate ---
    try:
        ref_total = await _query_scalar(
            "SELECT COUNT(*) FROM referrals WHERE created_at >= :cutoff",
            {"cutoff": cutoff},
        )
        ref_completed = await _query_scalar(
            "SELECT COUNT(*) FROM referrals WHERE status IN ('COMPLETED', 'REWARDED') AND completed_at >= :cutoff",
            {"cutoff": cutoff},
        )
        metrics["referral"] = {"total": ref_total, "completed": ref_completed}
    except Exception as exc:
        logger.error("Brain — referral metrics failed: %s", exc)
        metrics["referral"] = {"total": 0, "completed": 0}

    # --- Flash Sale: sold vs slots ---
    try:
        flash_rows = await _query_rows(
            """SELECT name, total_slots, sold_slots, flash_price, starts_at
               FROM flash_sales WHERE starts_at >= :cutoff""",
            {"cutoff": cutoff},
        )
        metrics["flash_sales"] = [
            {
                "name": r.name,
                "total_slots": r.total_slots,
                "sold_slots": r.sold_slots,
                "flash_price": float(r.flash_price) if r.flash_price else 0,
            }
            for r in flash_rows
        ]
    except Exception as exc:
        logger.error("Brain — flash sale metrics failed: %s", exc)
        metrics["flash_sales"] = []

    # --- Revenue: daily trend (7 days) ---
    try:
        rev_rows = await _query_rows(
            """SELECT DATE(created_at) as day, SUM(amount) as rev, COUNT(*) as orders
               FROM payments WHERE status = 'confirmed' AND created_at >= :cutoff
               GROUP BY DATE(created_at) ORDER BY day""",
            {"cutoff": cutoff},
        )
        metrics["revenue_daily"] = [
            {"day": str(r.day), "revenue": float(r.rev or 0), "orders": r.orders}
            for r in rev_rows
        ]
    except Exception as exc:
        logger.error("Brain — revenue metrics failed: %s", exc)
        metrics["revenue_daily"] = []

    return metrics


async def analyze_and_optimize(metrics: dict) -> dict:
    """Send metrics to AI for analysis, then compute optimizations."""
    # --- Compute optimizations from raw data ---
    optimizations: dict = {}

    # A. Caption Style Weights
    style_data = metrics.get("teaser_by_style", [])
    if style_data:
        total_clicks = sum(s["clicks"] for s in style_data)
        if total_clicks > 0:
            weights = {}
            for s in style_data:
                conv_rate = s["converted"] / s["clicks"] if s["clicks"] > 0 else 0
                click_share = s["clicks"] / total_clicks
                weights[s["style"]] = round(conv_rate * 0.6 + click_share * 0.4, 3)
            # Normalize
            total_w = sum(weights.values())
            if total_w > 0:
                weights = {k: round(v / total_w, 3) for k, v in weights.items()}
            optimizations["caption_style_weights"] = weights

    # B. Best Posting Times
    round_data = metrics.get("teaser_by_round", [])
    if round_data:
        sorted_rounds = sorted(round_data, key=lambda x: x["clicks"], reverse=True)
        optimizations["best_round_times"] = [r["round_time"] for r in sorted_rounds if r["round_time"]]

    # C. Optimal Discount Levels
    comeback_data = metrics.get("comeback", [])
    if comeback_data:
        discount_stats: dict[int, dict] = defaultdict(lambda: {"sent": 0, "purchased": 0})
        for c in comeback_data:
            if c["discount_pct"]:
                d = int(c["discount_pct"])
                discount_stats[d]["sent"] += c["sent"]
                discount_stats[d]["purchased"] += c["purchased"]

        best_discounts = {}
        for pct, stats in sorted(discount_stats.items(), key=lambda x: x[1]["purchased"], reverse=True):
            rate = stats["purchased"] / stats["sent"] if stats["sent"] > 0 else 0
            best_discounts[f"discount_{pct}pct"] = round(rate, 3)
        optimizations["optimal_discounts"] = best_discounts

    # D. Lead Quality Scoring
    round_data_teaser = metrics.get("teaser_by_round", [])
    if round_data_teaser:
        lead_scores = {}
        for r in round_data_teaser:
            if r["round_time"] and r["clicks"] > 0:
                score = round(r["converted"] / r["clicks"], 3)
                lead_scores[f"round_{r['round_time']}"] = score
        if lead_scores:
            optimizations["lead_scores"] = lead_scores

    # --- AI Analysis ---
    ai_insights = ""
    try:
        metrics_summary = json.dumps(metrics, ensure_ascii=False, default=str)
        # Truncate if too long
        if len(metrics_summary) > 6000:
            metrics_summary = metrics_summary[:6000] + "..."

        response = await call_openrouter(
            model=AI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a marketing analyst for a Telegram VIP subscription business. "
                        "Analyze these metrics and provide:\n"
                        "1. Top 3 things working well (keep doing)\n"
                        "2. Top 3 things not working (stop or change)\n"
                        "3. Specific parameter changes to make\n"
                        "Reply in Thai, be concise, no fluff. Max 10 lines."
                    ),
                },
                {"role": "user", "content": f"Weekly metrics:\n{metrics_summary}"},
            ],
            caller="marketing_brain",
            temperature=0.5,
            max_tokens=1024,
        )
        ai_insights = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as exc:
        logger.error("Brain — AI analysis failed: %s", exc)
        ai_insights = "⚠️ AI วิเคราะห์ไม่ได้"

    return {"optimizations": optimizations, "ai_insights": ai_insights}


async def apply_optimizations(optimizations: dict) -> list[str]:
    """Write optimizations to marketing_config table."""
    applied: list[str] = []
    await _ensure_tables()

    for key, value in optimizations.items():
        try:
            async with get_session() as session:
                await session.execute(
                    text("""
                        INSERT INTO marketing_config (key, value, updated_at, updated_by)
                        VALUES (:key, :value, NOW(), 'brain')
                        ON CONFLICT (key) DO UPDATE
                        SET value = :value, updated_at = NOW(), updated_by = 'brain'
                    """),
                    {"key": key, "value": json.dumps(value, ensure_ascii=False)},
                )
            applied.append(key)
        except Exception as exc:
            logger.error("Brain — failed to apply %s: %s", key, exc)

    return applied


async def run_brain_weekly_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: วิเคราะห์การตลาดทุกวันอาทิตย์ 20:00 ไทย."""
    now_th = datetime.now(TH_TZ)
    logger.info("🧠 Marketing Brain starting at %s", now_th.strftime("%Y-%m-%d %H:%M"))

    try:
        await _ensure_tables()

        # 1. Collect
        metrics = await collect_weekly_metrics()

        # 2. Analyze
        result = await analyze_and_optimize(metrics)

        # 3. Apply
        applied = await apply_optimizations(result["optimizations"])

        # 4. Build report
        rev_data = metrics.get("revenue_daily", [])
        week_rev = sum(r["revenue"] for r in rev_data)
        week_orders = sum(r["orders"] for r in rev_data)

        teaser_total = sum(r["clicks"] for r in metrics.get("teaser_by_round", []))
        teaser_conv = sum(r["converted"] for r in metrics.get("teaser_by_round", []))

        comeback = metrics.get("comeback", [])
        cb_sent = sum(c["sent"] for c in comeback)
        cb_purchased = sum(c["purchased"] for c in comeback)

        ref = metrics.get("referral", {})

        lines = [
            f"🧠 <b>Marketing Brain — Weekly</b>",
            f"📅 สัปดาห์ {now_th.strftime('%d/%m')}",
            f"",
            f"💰 รายได้ ฿{week_rev:,.0f} ({week_orders} orders)",
            f"📊 Teaser {teaser_total} clicks → {teaser_conv} สมัคร",
            f"📬 Comeback {cb_sent} ส่ง → {cb_purchased} ซื้อ",
            f"🎁 Referral {ref.get('completed', 0)}/{ref.get('total', 0)} สำเร็จ",
            f"⚙️ ปรับค่า: {', '.join(applied) if applied else 'ไม่มี'}",
            f"",
            f"{result['ai_insights'][:500]}",
        ]

        report = "\n".join(lines)

        # Send to admin group
        admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
        if not admin_token:
            logger.error("ADMIN_BOT_TOKEN not set")
            return

        bot = Bot(token=admin_token)
        await bot.initialize()
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=report[:4096],
            parse_mode="HTML",
        )
        logger.info("🧠 Marketing Brain report sent")

    except Exception as exc:
        logger.error("Marketing Brain failed: %s", exc)
        try:
            admin_token = os.environ.get("ADMIN_BOT_TOKEN", "")
            if admin_token:
                bot = Bot(token=admin_token)
                await bot.initialize()
                await bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=f"⚠️ Marketing Brain error: {exc}",
                )
        except Exception:
            pass

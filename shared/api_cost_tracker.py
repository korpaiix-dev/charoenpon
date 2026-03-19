"""API Cost Tracker - wrap OpenRouter calls, log to DB + Google Sheets."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from shared.database import get_session
from shared.models import ApiCostLog

logger = logging.getLogger(__name__)

# --- Price per 1M tokens (input, output) in USD ---
PRICES: dict[str, dict[str, float]] = {
    "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "claude-haiku-4.5": {"input": 0.80, "output": 4.00},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
}

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")

GOOGLE_SHEETS_WEBHOOK: str = os.environ.get("SHEETS_COST_WEBHOOK", "")
GOOGLE_SHEETS_ID: str = os.environ.get("GOOGLE_SHEETS_ID", "")
GOOGLE_SHEETS_CREDS: str = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "credentials/google_sheets_sa.json")

# --- USD/THB rate cache ---
_rate_cache: dict[str, Any] = {"rate": 35.0, "fetched_at": 0.0}
RATE_CACHE_TTL = 3600  # 1 hour


async def get_usd_rate() -> float:
    """Fetch current USD→THB rate. Cached for 1 hour."""
    now = time.time()
    if now - _rate_cache["fetched_at"] < RATE_CACHE_TTL:
        return float(_rate_cache["rate"])

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.exchangerate-data.com/v1/latest",
                params={"base": "USD", "symbols": "THB"},
            )
            if resp.status_code == 200:
                data = resp.json()
                rate = float(data.get("rates", {}).get("THB", 35.0))
                _rate_cache["rate"] = rate
                _rate_cache["fetched_at"] = now
                return rate
    except Exception as exc:
        logger.warning("Failed to fetch USD rate, using cached: %s", exc)

    # Fallback: try exchangerate-api.com
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://open.er-api.com/v6/latest/USD")
            if resp.status_code == 200:
                data = resp.json()
                rate = float(data.get("rates", {}).get("THB", 35.0))
                _rate_cache["rate"] = rate
                _rate_cache["fetched_at"] = now
                return rate
    except Exception as exc:
        logger.warning("Fallback USD rate fetch failed: %s", exc)

    return float(_rate_cache["rate"])


def calculate_cost(
    model: str, prompt_tokens: int, completion_tokens: int
) -> float:
    """Calculate USD cost from token counts."""
    prices = PRICES.get(model)
    if not prices:
        logger.warning("Unknown model %s, using gemini-2.0-flash-lite prices", model)
        prices = PRICES["gemini-2.0-flash-lite"]

    input_cost = (prompt_tokens / 1_000_000) * prices["input"]
    output_cost = (completion_tokens / 1_000_000) * prices["output"]
    return input_cost + output_cost


async def _log_to_sheets(row: dict[str, Any]) -> None:
    """Log cost to Google Sheets 'ค่าใช้จ่าย' sheet via Sheets API directly."""
    # Method 1: Direct Sheets API (preferred)
    if GOOGLE_SHEETS_ID:
        try:
            import google.auth.transport.requests
            from google.oauth2.service_account import Credentials as SACreds

            creds_path = GOOGLE_SHEETS_CREDS
            if not os.path.isabs(creds_path):
                creds_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), creds_path)

            sa_creds = SACreds.from_service_account_file(
                creds_path,
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
            )
            sa_creds.refresh(google.auth.transport.requests.Request())

            # Format: วันที่ | รายการ | หมวด | จำนวนเงิน (THB) | จำนวนเงิน (USD) | Agent | หมายเหตุ
            from datetime import datetime, timezone, timedelta
            bkk = timezone(timedelta(hours=7))
            now_bkk = datetime.now(bkk).strftime("%Y-%m-%d %H:%M:%S")

            tokens_info = f"{row.get('prompt_tokens', 0):,} in / {row.get('completion_tokens', 0):,} out"
            values = [[
                now_bkk,                                    # วันที่
                f"API: {row.get('model', '')}",             # รายการ
                "API Cost",                                  # หมวด
                round(row.get("cost_thb", 0), 2),           # จำนวนเงิน (THB)
                round(row.get("cost_usd", 0), 6),           # จำนวนเงิน (USD)
                row.get("caller", ""),                       # Agent
                tokens_info,                                 # หมายเหตุ
            ]]

            import urllib.request
            url = (
                f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEETS_ID}"
                f"/values/%E0%B8%84%E0%B9%88%E0%B8%B2%E0%B9%83%E0%B8%8A%E0%B9%89%E0%B8%88%E0%B9%88%E0%B8%B2%E0%B8%A2!A:G"
                f":append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
            )
            req = urllib.request.Request(
                url,
                data=json.dumps({"values": values}).encode(),
                headers={
                    "Authorization": f"Bearer {sa_creds.token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            return
        except Exception as exc:
            logger.warning("Failed to log to Google Sheets API: %s", exc)

    # Method 2: Webhook fallback (Apps Script)
    if not GOOGLE_SHEETS_WEBHOOK:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(GOOGLE_SHEETS_WEBHOOK, json=row)
    except Exception as exc:
        logger.warning("Failed to log to Google Sheets webhook: %s", exc)


async def track(
    model: str,
    endpoint: str,
    prompt_tokens: int,
    completion_tokens: int,
    caller: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ApiCostLog:
    """Record an API call's cost to DB and Google Sheets."""
    cost_usd = calculate_cost(model, prompt_tokens, completion_tokens)
    usd_rate = await get_usd_rate()
    cost_thb = cost_usd * usd_rate

    log_entry = ApiCostLog(
        model=model,
        endpoint=endpoint,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=Decimal(str(round(cost_usd, 8))),
        cost_thb=Decimal(str(round(cost_thb, 4))),
        caller=caller,
        metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
    )

    async with get_session() as session:
        session.add(log_entry)
        await session.flush()
        await session.refresh(log_entry)

    # Fire-and-forget Sheets logging
    sheets_row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "endpoint": endpoint,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": float(cost_usd),
        "cost_thb": float(cost_thb),
        "caller": caller or "",
    }
    await _log_to_sheets(sheets_row)

    return log_entry


async def call_openrouter(
    model: str,
    messages: list[dict[str, Any]],
    caller: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call OpenRouter API and automatically track cost.

    Returns the full OpenRouter response dict. Raises httpx.HTTPStatusError on failure.
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://charoenpon.com",
        "X-Title": "Charoenpon Bot",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(OPENROUTER_API_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    await track(
        model=model,
        endpoint="openrouter/chat/completions",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        caller=caller,
        metadata=metadata,
    )

    return data


async def daily_summary() -> dict[str, Any]:
    """Generate a daily cost summary for Discord notification.

    Returns dict with: date, total_usd, total_thb, by_model breakdown, total_calls.
    """
    from sqlalchemy import func, select

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    async with get_session() as session:
        # Total
        total_q = await session.execute(
            select(
                func.count(ApiCostLog.id).label("total_calls"),
                func.coalesce(func.sum(ApiCostLog.cost_usd), 0).label("total_usd"),
                func.coalesce(func.sum(ApiCostLog.cost_thb), 0).label("total_thb"),
                func.coalesce(func.sum(ApiCostLog.prompt_tokens), 0).label("total_prompt"),
                func.coalesce(func.sum(ApiCostLog.completion_tokens), 0).label("total_completion"),
            ).where(
                ApiCostLog.created_at >= today_start,
                ApiCostLog.created_at < today_end,
            )
        )
        totals = total_q.one()

        # Per model breakdown
        model_q = await session.execute(
            select(
                ApiCostLog.model,
                func.count(ApiCostLog.id).label("calls"),
                func.sum(ApiCostLog.cost_usd).label("usd"),
                func.sum(ApiCostLog.cost_thb).label("thb"),
                func.sum(ApiCostLog.prompt_tokens).label("prompt_tokens"),
                func.sum(ApiCostLog.completion_tokens).label("completion_tokens"),
            )
            .where(
                ApiCostLog.created_at >= today_start,
                ApiCostLog.created_at < today_end,
            )
            .group_by(ApiCostLog.model)
            .order_by(func.sum(ApiCostLog.cost_usd).desc())
        )
        by_model = [
            {
                "model": row.model,
                "calls": row.calls,
                "cost_usd": float(row.usd),
                "cost_thb": float(row.thb),
                "prompt_tokens": int(row.prompt_tokens),
                "completion_tokens": int(row.completion_tokens),
            }
            for row in model_q.all()
        ]

    return {
        "date": today_start.strftime("%Y-%m-%d"),
        "total_calls": totals.total_calls,
        "total_usd": float(totals.total_usd),
        "total_thb": float(totals.total_thb),
        "total_prompt_tokens": int(totals.total_prompt),
        "total_completion_tokens": int(totals.total_completion),
        "by_model": by_model,
    }


def format_daily_summary_discord(summary: dict[str, Any]) -> str:
    """Format daily_summary() output as a Discord embed-ready message."""
    lines = [
        f"📊 **API Cost Report — {summary['date']}**",
        f"💰 Total: **${summary['total_usd']:.4f}** (฿{summary['total_thb']:.2f})",
        f"📞 Calls: **{summary['total_calls']}**",
        f"🔤 Tokens: {summary['total_prompt_tokens']:,} in / {summary['total_completion_tokens']:,} out",
        "",
        "**Per Model:**",
    ]
    for m in summary["by_model"]:
        lines.append(
            f"• `{m['model']}`: {m['calls']} calls — ${m['cost_usd']:.4f} (฿{m['cost_thb']:.2f})"
        )
    return "\n".join(lines)

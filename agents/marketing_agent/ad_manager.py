"""Ad Manager (เจมส์) - สร้าง Ad Approval Request ส่ง Discord.

ส่ง Discord #ad-approval พร้อมปุ่ม ✅❌✏️
ห้ามยิง Ad โดยไม่ได้ ✅ approve
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx

from shared.database import get_session
from shared.models import AdCampaign, CampaignStatus

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_AD_APPROVAL: str = os.environ.get("DISCORD_WEBHOOK_AD_APPROVAL", "")
DISCORD_BOT_TOKEN: str = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_AD_APPROVAL_CHANNEL: str = os.environ.get("DISCORD_CH_AD_APPROVAL", "") or os.environ.get("DISCORD_AD_APPROVAL_CHANNEL", "")

DISCORD_API_BASE = "https://discord.com/api/v10"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISION = "revision"


_approval_store: dict[str, dict[str, Any]] = {}


def _build_approval_embed(
    campaign_name: str,
    ad_copy: dict[str, Any],
    request_id: str,
    safety_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """สร้าง Discord embed สำหรับ approval request."""
    parsed = ad_copy.get("parsed", {})
    headline = parsed.get("headline", "-")
    primary_text = parsed.get("primary_text", "-")
    description = parsed.get("description", "-")
    cta = ad_copy.get("cta", "Send Message")
    tone = ad_copy.get("tone", "sexy_safe")

    safety_status = "⏳ รอตรวจ"
    if safety_result:
        if safety_result.get("passed"):
            safety_status = "✅ ผ่าน Safety Check"
        else:
            issues = safety_result.get("issues", [])
            safety_status = "❌ ไม่ผ่าน: " + ", ".join(issues)

    embed = {
        "title": f"📢 Ad Approval Request: {campaign_name}",
        "color": 0xFF6B35,
        "fields": [
            {"name": "📌 Headline", "value": headline, "inline": False},
            {"name": "📝 Primary Text", "value": primary_text, "inline": False},
            {"name": "📋 Description", "value": description, "inline": False},
            {"name": "🔘 CTA Button", "value": cta, "inline": True},
            {"name": "🎨 Tone", "value": tone, "inline": True},
            {"name": "🛡️ Safety Check", "value": safety_status, "inline": False},
            {"name": "🆔 Request ID", "value": f"`{request_id}`", "inline": True},
        ],
        "footer": {"text": "เจมส์ Marketing Agent | บริษัทเจริญพร"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return embed


def _build_approval_components(request_id: str) -> list[dict[str, Any]]:
    """สร้างปุ่ม ✅❌✏️ สำหรับ Discord message."""
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 3,
                    "label": "✅ Approve",
                    "custom_id": f"ad_approve_{request_id}",
                },
                {
                    "type": 2,
                    "style": 4,
                    "label": "❌ Reject",
                    "custom_id": f"ad_reject_{request_id}",
                },
                {
                    "type": 2,
                    "style": 1,
                    "label": "✏️ Request Revision",
                    "custom_id": f"ad_revision_{request_id}",
                },
            ],
        }
    ]


async def send_approval_request(
    campaign_name: str,
    ad_copy: dict[str, Any],
    campaign_id: int | None = None,
    safety_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """ส่ง Ad Approval Request ไปยัง Discord #ad-approval."""
    request_id = f"ad_{int(datetime.now(timezone.utc).timestamp())}_{campaign_id or 0}"

    _approval_store[request_id] = {
        "status": ApprovalStatus.PENDING,
        "campaign_name": campaign_name,
        "campaign_id": campaign_id,
        "ad_copy": ad_copy,
        "safety_result": safety_result,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_by": None,
        "review_note": None,
    }

    embed = _build_approval_embed(campaign_name, ad_copy, request_id, safety_result)
    components = _build_approval_components(request_id)

    message_sent = False

    if DISCORD_BOT_TOKEN and DISCORD_AD_APPROVAL_CHANNEL:
        message_sent = await _send_via_bot(embed, components)

    if not message_sent and DISCORD_WEBHOOK_AD_APPROVAL:
        message_sent = await _send_via_webhook(embed)

    if not message_sent:
        logger.error("Failed to send approval request - no Discord config")

    logger.info(
        "Ad approval request created: %s (campaign: %s, sent: %s)",
        request_id, campaign_name, message_sent,
    )

    return {
        "request_id": request_id,
        "status": ApprovalStatus.PENDING.value,
        "message_sent": message_sent,
    }


async def _send_via_bot(embed: dict, components: list) -> bool:
    """ส่งผ่าน Discord Bot API (รองรับ components/buttons)."""
    try:
        headers = {
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "embeds": [embed],
            "components": components,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{DISCORD_API_BASE}/channels/{DISCORD_AD_APPROVAL_CHANNEL}/messages",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()

        logger.info("Approval request sent via Discord Bot API")
        return True
    except Exception as exc:
        logger.error("Failed to send via Discord Bot API: %s", exc)
        return False


async def _send_via_webhook(embed: dict) -> bool:
    """ส่งผ่าน Discord Webhook (ไม่รองรับ buttons แต่ส่ง embed ได้)."""
    try:
        payload = {
            "embeds": [embed],
            "content": "⚠️ กรุณา react ✅ เพื่อ approve หรือ ❌ เพื่อ reject",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(DISCORD_WEBHOOK_AD_APPROVAL, json=payload)
            resp.raise_for_status()

        logger.info("Approval request sent via Discord Webhook")
        return True
    except Exception as exc:
        logger.error("Failed to send via Discord Webhook: %s", exc)
        return False


async def handle_approval_response(
    request_id: str,
    action: str,
    reviewer: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """จัดการ response จาก Discord (approve/reject/revision)."""
    if request_id not in _approval_store:
        return {"success": False, "error": f"Request ID '{request_id}' not found"}

    request = _approval_store[request_id]

    if action == "approve":
        request["status"] = ApprovalStatus.APPROVED
    elif action == "reject":
        request["status"] = ApprovalStatus.REJECTED
    elif action == "revision":
        request["status"] = ApprovalStatus.REVISION
    else:
        return {"success": False, "error": f"Unknown action: {action}"}

    request["reviewed_by"] = reviewer
    request["review_note"] = note
    request["reviewed_at"] = datetime.now(timezone.utc).isoformat()

    campaign_id = request.get("campaign_id")
    if campaign_id and action == "approve":
        await _activate_campaign(campaign_id)
    elif campaign_id and action == "reject":
        await _pause_campaign(campaign_id)

    logger.info(
        "Approval response: %s -> %s (by %s)", request_id, action, reviewer
    )

    return {
        "success": True,
        "request_id": request_id,
        "status": request["status"].value,
        "action": action,
    }


async def _activate_campaign(campaign_id: int) -> None:
    """เปิดใช้งาน campaign หลัง approve."""
    async with get_session() as session:
        from sqlalchemy import update
        await session.execute(
            update(AdCampaign)
            .where(AdCampaign.id == campaign_id)
            .values(status=CampaignStatus.ACTIVE)
        )
    logger.info("Campaign #%d activated after approval", campaign_id)


async def _pause_campaign(campaign_id: int) -> None:
    """หยุด campaign หลัง reject."""
    async with get_session() as session:
        from sqlalchemy import update
        await session.execute(
            update(AdCampaign)
            .where(AdCampaign.id == campaign_id)
            .values(status=CampaignStatus.PAUSED)
        )
    logger.info("Campaign #%d paused after rejection", campaign_id)


def is_approved(request_id: str) -> bool:
    """ตรวจสอบว่า ad ได้รับ approve แล้วหรือยัง — ห้ามยิงถ้ายังไม่ approve."""
    request = _approval_store.get(request_id)
    if not request:
        return False
    return request["status"] == ApprovalStatus.APPROVED


def get_approval_status(request_id: str) -> dict[str, Any] | None:
    """ดึงสถานะ approval request."""
    return _approval_store.get(request_id)


def get_pending_approvals() -> list[dict[str, Any]]:
    """ดึงรายการ approval request ที่ยังรอ review."""
    pending = []
    for req_id, data in _approval_store.items():
        if data["status"] == ApprovalStatus.PENDING:
            pending.append({"request_id": req_id, **data})
    return pending

"""Telegram Bot API wrapper for dashboard actions."""
import httpx
from ..config import SALES_BOT_TOKEN, GUARDIAN_BOT_TOKEN, ADMIN_BOT_TOKEN

BASE_URL = "https://api.telegram.org/bot{token}/{method}"

async def _call(token: str, method: str, **kwargs):
    url = BASE_URL.format(token=token, method=method)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=kwargs)
        return resp.json()

async def send_dm(telegram_id: int, text: str):
    """Send DM via Sales Bot."""
    return await _call(SALES_BOT_TOKEN, "sendMessage", chat_id=telegram_id, text=text)

async def kick_member(chat_id: int, user_id: int):
    """Kick member via Guardian Bot."""
    return await _call(GUARDIAN_BOT_TOKEN, "banChatMember", chat_id=chat_id, user_id=user_id)

async def unban_member(chat_id: int, user_id: int):
    """Unban member via Guardian Bot."""
    return await _call(GUARDIAN_BOT_TOKEN, "unbanChatMember", chat_id=chat_id, user_id=user_id, only_if_banned=True)

async def create_invite_link(chat_id: int, name: str = "Dashboard Link", member_limit: int = 1):
    """Create invite link via Admin Bot."""
    return await _call(ADMIN_BOT_TOKEN, "createChatInviteLink", 
                       chat_id=chat_id, name=name, member_limit=member_limit)

async def get_chat_member_count(chat_id: int):
    """Get member count."""
    return await _call(ADMIN_BOT_TOKEN, "getChatMemberCount", chat_id=chat_id)

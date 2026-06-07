"""'/where' command — inspect notification routing.

Usage in Telegram admin group:
    /where                       → list all events grouped by category
    /where payment_approved      → show which channels this event goes to
    /where payment*              → filter by prefix
    /where help                  → usage help
"""
import html
from telegram import Update
from telegram.ext import ContextTypes


_GROUP_PREFIX = {
    "payment_":   "💰 Payment",
    "slip_":      "🧾 Slip",
    "truemoney_": "💸 TrueMoney",
    "broadcast_": "📣 Broadcast",
    "member_":    "👥 Membership",
    "content_":   "📺 Content",
    "bot_":       "🤖 Bot health",
    "ai_":        "🧠 AI",
    "spam_":      "🛡 Spam",
    "abuse_":     "🛡 Abuse",
    "daily_":     "📊 Reports",
    "weekly_":    "📊 Reports",
    "manager_":   "🎯 Manager",
    "sheets_":    "📑 Sheets",
    "sos":        "🚨 SOS",
    "slip2go_":   "🧾 Slip",
}


def _group_for(key: str) -> str:
    for pref, label in _GROUP_PREFIX.items():
        if key.startswith(pref) or key == pref:
            return label
    return "🔹 Other"


def _format_route(route: str) -> str:
    """Pretty-print a single route."""
    if route.startswith("discord:"):
        return f"💬 Discord <b>#{route.split(':',1)[1]}</b>"
    if route == "telegram:admin":
        return "📱 Telegram <b>ห้องแอดมิน</b>"
    if route.startswith("log:"):
        return f"📝 Log <code>{route.split(':',1)[1]}</code>"
    return f"<code>{route}</code>"


async def cmd_where(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    from shared.notify import ROUTES, where, list_events

    args = context.args or []

    # /where help
    if args and args[0].lower() in ("help", "?"):
        await update.message.reply_text(
            "🔍 <b>/where — Notification Routing Inspector</b>\n"
            "═══════════════\n\n"
            "<code>/where</code>             ดูทุก event แบ่งหมวด\n"
            "<code>/where payment_approved</code>  ดู channels ของ event นั้น\n"
            "<code>/where payment*</code>    กรองด้วย prefix\n\n"
            "💡 รายชื่อ event ดูใน <code>shared/notify.py</code> ROUTES",
            parse_mode="HTML",
        )
        return

    # /where <event_key>
    if args:
        query = args[0].rstrip("*")
        events = list_events()
        matches = [(k, v) for k, v in events if k.startswith(query)]
        if not matches:
            await update.message.reply_text(
                f"❌ ไม่พบ event ที่ขึ้นต้นด้วย <code>{html.escape(query)}</code>\n"
                "ลอง <code>/where</code> เพื่อดูทั้งหมด",
                parse_mode="HTML",
            )
            return
        # Limit to top 20 to avoid Telegram message-too-long
        matches = matches[:20]
        lines = [f"🔍 <b>Routing ({len(matches)} event)</b>", ""]
        for key, routes in matches:
            lines.append(f"📌 <code>{key}</code>")
            for r in routes:
                lines.append(f"   → {_format_route(r)}")
            lines.append("")
        await update.message.reply_text("\n".join(lines)[:4000], parse_mode="HTML")
        return

    # /where (no args) — list all events grouped
    events = list_events()
    grouped: dict[str, list[str]] = {}
    for key, _ in events:
        grouped.setdefault(_group_for(key), []).append(key)
    lines = [
        f"📋 <b>All notification events</b> ({len(events)} ทั้งหมด)",
        "═══════════════",
    ]
    for group, keys in sorted(grouped.items()):
        lines.append(f"\n<b>{group}</b> ({len(keys)})")
        for k in sorted(keys):
            lines.append(f"  • <code>{k}</code>")
    lines.append("\n💡 พิมพ์ <code>/where payment_approved</code> ดูปลายทาง")
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode="HTML")


__all__ = ["cmd_where"]

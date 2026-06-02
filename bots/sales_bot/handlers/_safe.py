"""Shared safe-edit helper — handles photo-original messages."""
from telegram.error import BadRequest

async def safe_edit(query, text: str, reply_markup=None, parse_mode="HTML",
                     disable_web_page_preview=True) -> None:
    """Edit message text safely. Falls back to delete+send when original is a photo."""
    try:
        await query.edit_message_text(
            text, parse_mode=parse_mode, reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
    except (BadRequest, Exception):
        try:
            await query.message.delete()
        except Exception:
            pass
        try:
            await query.message.chat.send_message(
                text, parse_mode=parse_mode, reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        except Exception:
            try:
                await query.message.reply_text(
                    text, parse_mode=parse_mode, reply_markup=reply_markup,
                )
            except Exception:
                pass

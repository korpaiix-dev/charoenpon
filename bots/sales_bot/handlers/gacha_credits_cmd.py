"""/credits command — show user discount balance from gachapon."""
from __future__ import annotations

from sqlalchemy import text as _t
from shared.database import get_session


async def cmd_credits(update, context) -> None:
    """Show user's gacha discount credit balance."""
    if not update.message or not update.effective_user:
        return
    tg = update.effective_user
    async with get_session() as s:
        r = await s.execute(
            _t("SELECT balance, total_earned, total_used FROM user_discount_credits WHERE telegram_id = :tg"),
            {"tg": tg.id}
        )
        row = r.fetchone()
    bal = float(row[0]) if row else 0
    earned = float(row[1]) if row else 0
    used = float(row[2]) if row else 0
    lines = ["💰 <b>ส่วนลดสะสมของคุณ</b>", ""]
    if bal <= 0 and earned <= 0:
        lines.append("คุณยังไม่มีส่วนลดสะสม")
        lines.append("")
        lines.append("💡 หมุนกาชาปองที่เมนู 🎰 เพื่อลุ้นรางวัล")
        lines.append("   มีโอกาสได้ส่วนลด ฿50 ทุกครั้งที่หมุน")
    else:
        lines.append(f"💵 ยอดคงเหลือ: <b>฿{bal:,.0f}</b>")
        lines.append(f"📥 รวมได้รับ: ฿{earned:,.0f}")
        lines.append(f"📤 ใช้ไปแล้ว: ฿{used:,.0f}")
        lines.append("")
        lines.append("💡 ส่วนลดจะถูกหักอัตโนมัติเมื่อคุณซื้อแพ็คเกจ")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


__all__ = ["cmd_credits"]

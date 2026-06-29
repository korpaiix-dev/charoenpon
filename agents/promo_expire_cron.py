"""promo_expire_cron — Auto-deactivate expired promotions.

Standalone async script. Two ways to run:
  1) Periodic standalone: `python -m agents.promo_expire_cron` (e.g. via cron / docker)
  2) Importable: `from agents.promo_expire_cron import deactivate_expired_promos`
     and call from sales_bot apscheduler (see bots/sales_bot/main.py wiring).

Why: when ends_at < NOW(), promotions table can still have is_active=TRUE, which
makes sales_bot/content_bot keep showing dead promos until an admin manually
toggles. This cron sweeps every hour.
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys

import asyncpg

logger = logging.getLogger(__name__)

DB_DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@charoenpon-postgres:5432/charoenpon",
)


async def deactivate_expired_promos(conn=None) -> int:
    """UPDATE promotions SET is_active=FALSE WHERE is_active AND ends_at<NOW().

    Returns the number of rows updated. Accepts an optional asyncpg connection;
    opens its own if None passed.
    """
    own_conn = conn is None
    if own_conn:
        conn = await asyncpg.connect(DB_DSN)
    try:
        rows = await conn.fetch(
            """
            UPDATE promotions
               SET is_active = FALSE,
                   updated_at = NOW()
             WHERE is_active = TRUE
               AND ends_at IS NOT NULL
               AND ends_at < NOW()
            RETURNING id, code, ends_at
            """
        )
        if rows:
            for r in rows:
                logger.info(
                    "promo_expire_cron: deactivated id=%s code=%s ends_at=%s",
                    r["id"], r["code"], r["ends_at"],
                )
        return len(rows)
    finally:
        if own_conn:
            await conn.close()


async def _main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        n = await deactivate_expired_promos()
        logger.info("promo_expire_cron: deactivated %d expired promo(s)", n)
        # Clear in-process cache if available (best-effort, ignore errors)
        try:
            from shared.promotion_service import clear_cache
            clear_cache()
        except Exception:
            pass
        return 0
    except Exception as exc:
        logger.exception("promo_expire_cron failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))

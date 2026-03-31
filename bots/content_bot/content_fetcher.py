"""Content Auto-Fetcher — ดึงรูปจาก 3 แหล่งอัตโนมัติ (Reddit, Twitter/Nitter, Web 18+).

ดึงรูปใหม่ → ส่งเข้า Telegram เก็บ file_id → ใส่ content_queue
ระบบเช็ค duplicate ด้วย MD5 hash ของไฟล์
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree

import httpx
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, select, func as sqlfunc
from sqlalchemy.orm import Mapped, mapped_column
from telegram import Bot

from shared.database import get_session, engine
from shared.models import Base, ContentQueue

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# DB Model: fetched_content_log
# ──────────────────────────────────────────────

class FetchedContentLog(Base):
    """Log ทุกการดึง content จากแหล่งภายนอก."""

    __tablename__ = "fetched_content_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, comment="reddit / twitter / web")
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="post_id / tweet_id")
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, comment="MD5 hash")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sqlfunc.now(), nullable=False
    )
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

CONTENT_BOT_TOKEN = os.environ.get("CONTENT_BOT_TOKEN", "")

# Reddit subreddits (Asian/Thai content)
DEFAULT_SUBREDDITS = [
    "AsianHotties",
    "AsiansGoneWild",
    "juicyasians",
    "realasians",
    "NextDoorAsians",
]

# Nitter instances to try (fallback chain)
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

# Twitter/X accounts (18+, public)
DEFAULT_TWITTER_ACCOUNTS: list[str] = [
    # เพิ่ม accounts ที่โพสต์ content ฟรีที่นี่
]

# Web gallery URLs to scrape
DEFAULT_WEB_URLS: list[str] = [
    # เพิ่ม URL เว็บ gallery ที่นี่
]

# User-Agent สำหรับ Reddit (ต้องใส่ให้ดูเป็น bot ถูกต้อง)
REDDIT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Delay ระหว่าง request (วินาที)
REQUEST_DELAY = 2.0

# Max file size (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024

# Temp directory for downloads
TEMP_DIR = Path(tempfile.gettempdir()) / "charoenpon_content"
TEMP_DIR.mkdir(exist_ok=True)


# ──────────────────────────────────────────────
# Discord logging (reuse pattern จาก main.py)
# ──────────────────────────────────────────────

async def _send_discord_log(content: str) -> None:
    """Send log to Discord #system-logs."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    ch = os.environ.get("DISCORD_CH_SYSTEM_LOG", os.environ.get("DISCORD_CH_CONTENT_LOG", ""))
    if not token or not ch:
        return
    try:
        from datetime import timedelta
        TH_TZ = timezone(timedelta(hours=7))
        now_th = datetime.now(TH_TZ)
        embed = {
            "title": "🔄 Content Fetcher",
            "description": content,
            "color": 0x3498DB,
            "footer": {"text": f"⊙ เจริญพร | {now_th.strftime('%d/%m/%Y %H:%M')}"},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{ch}/messages",
                headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                json={"embeds": [embed]},
            )
    except Exception as e:
        logger.error("Failed to send Discord log: %s", e)


# ──────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────

def _md5_file(filepath: str) -> str:
    """คำนวณ MD5 hash ของไฟล์."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def _is_duplicate_hash(file_hash: str) -> bool:
    """เช็คว่า hash นี้เคยดึงมาแล้วหรือยัง."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(sqlfunc.count(FetchedContentLog.id))
                .where(FetchedContentLog.file_hash == file_hash)
                .where(FetchedContentLog.is_duplicate == False)
            )
            count = result.scalar() or 0
            return count > 0
    except Exception:
        return False


async def _is_duplicate_source_id(source: str, source_id: str) -> bool:
    """เช็คว่า source_id นี้เคยดึงมาแล้วหรือยัง."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(sqlfunc.count(FetchedContentLog.id))
                .where(FetchedContentLog.source == source)
                .where(FetchedContentLog.source_id == source_id)
            )
            count = result.scalar() or 0
            return count > 0
    except Exception:
        return False


async def _download_image(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """ดาวน์โหลดรูปจาก URL → return local file path หรือ None."""
    try:
        resp = await client.get(url, follow_redirects=True, timeout=30)
        if resp.status_code != 200:
            logger.warning("Download failed (HTTP %d): %s", resp.status_code, url)
            return None

        content_type = resp.headers.get("content-type", "")
        if not any(t in content_type for t in ["image/", "application/octet-stream"]):
            logger.warning("Not an image (content-type: %s): %s", content_type, url)
            return None

        if len(resp.content) > MAX_FILE_SIZE:
            logger.warning("File too large (%d bytes): %s", len(resp.content), url)
            return None

        if len(resp.content) < 5000:
            logger.warning("File too small (%d bytes), likely placeholder: %s", len(resp.content), url)
            return None

        # Determine extension
        ext = ".jpg"
        if "png" in content_type:
            ext = ".png"
        elif "gif" in content_type:
            ext = ".gif"
        elif "webp" in content_type:
            ext = ".webp"

        filepath = TEMP_DIR / f"fetch_{int(time.time() * 1000)}{ext}"
        filepath.write_bytes(resp.content)
        return str(filepath)

    except Exception as e:
        logger.error("Download error for %s: %s", url, e)
        return None


async def _upload_to_telegram(bot: Bot, filepath: str) -> Optional[str]:
    """ส่งรูปเข้า Telegram bot → return file_id."""
    try:
        # ส่งให้ตัว bot เอง (Saved Messages / private chat กับ admin)
        admin_id = int(os.environ.get("ADMIN_TELEGRAM_IDS", "8502597269").split(",")[0].strip())
        with open(filepath, "rb") as f:
            msg = await bot.send_photo(
                chat_id=admin_id,
                photo=f,
                caption="📥 Auto-fetched content",
                disable_notification=True,
            )
        if msg and msg.photo:
            return msg.photo[-1].file_id
        return None
    except Exception as e:
        logger.error("Telegram upload failed: %s", e)
        return None


async def _save_to_content_queue(file_id: str, source: str) -> bool:
    """บันทึก file_id ลง content_queue."""
    try:
        async with get_session() as session:
            item = ContentQueue(
                file_id=file_id,
                file_type="photo",
                sent_by=0,  # 0 = auto-fetched
            )
            session.add(item)
        return True
    except Exception as e:
        logger.error("Failed to save to content_queue: %s", e)
        return False


async def _log_fetch(source: str, source_id: str | None, source_url: str,
                     file_id: str | None, file_hash: str | None,
                     is_duplicate: bool, error: str | None = None) -> None:
    """บันทึก log ใน fetched_content_log."""
    try:
        async with get_session() as session:
            log = FetchedContentLog(
                source=source,
                source_id=source_id,
                source_url=source_url,
                file_id=file_id,
                file_hash=file_hash,
                is_duplicate=is_duplicate,
                error=error,
            )
            session.add(log)
    except Exception as e:
        logger.error("Failed to save fetch log: %s", e)


# ──────────────────────────────────────────────
# ContentFetcher class
# ──────────────────────────────────────────────

class ContentFetcher:
    """ดึงรูปจาก 3 แหล่ง → ส่งเข้า content_queue."""

    def __init__(self, bot_token: str | None = None):
        self.bot_token = bot_token or CONTENT_BOT_TOKEN
        self._bot: Bot | None = None

    @property
    def bot(self) -> Bot:
        if self._bot is None:
            self._bot = Bot(token=self.bot_token)
            await self._bot.initialize()
        return self._bot

    # ────────────── Reddit ──────────────

    async def fetch_reddit(self, subreddits: list[str] | None = None, limit: int = 5) -> list[str]:
        """ดึงรูปจาก Reddit → return list of local file paths."""
        subs = subreddits or DEFAULT_SUBREDDITS
        downloaded: list[str] = []

        headers = {"User-Agent": REDDIT_USER_AGENT}

        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            for sub in subs:
                if len(downloaded) >= limit:
                    break

                try:
                    url = f"https://www.reddit.com/r/{sub}/hot.json?limit=15&raw_json=1"
                    resp = await client.get(url, timeout=15)

                    if resp.status_code == 403:
                        logger.warning("Reddit blocked (403) for r/%s — trying old.reddit.com", sub)
                        url = f"https://old.reddit.com/r/{sub}/hot.json?limit=15&raw_json=1"
                        resp = await client.get(url, timeout=15)

                    if resp.status_code != 200:
                        logger.warning("Reddit API returned %d for r/%s", resp.status_code, sub)
                        await asyncio.sleep(REQUEST_DELAY)
                        continue

                    data = resp.json()
                    posts = data.get("data", {}).get("children", [])

                    for post_data in posts:
                        if len(downloaded) >= limit:
                            break

                        post = post_data.get("data", {})
                        post_id = post.get("id", "")
                        post_hint = post.get("post_hint", "")
                        domain = post.get("domain", "")
                        img_url = post.get("url", "")

                        # ดึงเฉพาะรูป (ไม่ดึงวิดีโอ)
                        is_image = (
                            post_hint == "image"
                            or domain in ("i.redd.it", "i.imgur.com")
                            or img_url.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))
                        )

                        if not is_image or not img_url:
                            continue

                        # ข้ามถ้า imgur album link
                        if "imgur.com" in img_url and "/a/" in img_url:
                            continue

                        # Fix imgur URL ให้เป็น direct image
                        if "imgur.com" in img_url and not img_url.endswith((".jpg", ".png", ".gif")):
                            img_url = img_url + ".jpg"

                        # เช็คซ้ำ source_id
                        if await _is_duplicate_source_id("reddit", post_id):
                            logger.debug("Skipping duplicate Reddit post: %s", post_id)
                            continue

                        # ดาวน์โหลด
                        filepath = await _download_image(img_url, client)
                        if filepath:
                            file_hash = _md5_file(filepath)

                            # เช็คซ้ำ hash
                            if await _is_duplicate_hash(file_hash):
                                logger.info("Duplicate hash found, skipping: %s", img_url)
                                await _log_fetch("reddit", post_id, img_url, None, file_hash, True)
                                os.remove(filepath)
                                continue

                            downloaded.append(filepath)
                            await _log_fetch("reddit", post_id, img_url, None, file_hash, False)
                            logger.info("Downloaded from Reddit r/%s: %s", sub, post_id)

                        await asyncio.sleep(REQUEST_DELAY)

                except Exception as e:
                    logger.error("Reddit fetch error for r/%s: %s", sub, e)
                    await _log_fetch("reddit", None, f"r/{sub}", None, None, False, str(e))

                await asyncio.sleep(REQUEST_DELAY)

        return downloaded

    # ────────────── Twitter/Nitter ──────────────

    async def fetch_twitter(self, accounts: list[str] | None = None, limit: int = 5) -> list[str]:
        """ดึงรูปจาก Twitter/Nitter → return list of local file paths."""
        accts = accounts or DEFAULT_TWITTER_ACCOUNTS
        if not accts:
            logger.info("No Twitter accounts configured, skipping")
            return []

        downloaded: list[str] = []

        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            for account in accts:
                if len(downloaded) >= limit:
                    break

                rss_fetched = False
                for instance in NITTER_INSTANCES:
                    if rss_fetched:
                        break

                    try:
                        rss_url = f"{instance}/{account}/media/rss"
                        resp = await client.get(rss_url)

                        if resp.status_code != 200 or len(resp.content) < 100:
                            continue

                        # Parse RSS
                        root = ElementTree.fromstring(resp.text)
                        items = root.findall(".//item")

                        for item in items:
                            if len(downloaded) >= limit:
                                break

                            link = item.findtext("link", "")
                            description = item.findtext("description", "")

                            # Extract image URLs from description
                            img_urls = re.findall(r'src="([^"]+\.(jpg|jpeg|png|gif|webp))"', description, re.I)

                            for img_url, _ in img_urls:
                                if len(downloaded) >= limit:
                                    break

                                # Replace nitter image proxy with original
                                if "/pic/" in img_url:
                                    img_url = img_url.replace(f"{instance}/pic/", "https://pbs.twimg.com/")

                                tweet_id = link.split("/")[-1].split("#")[0] if link else None

                                if tweet_id and await _is_duplicate_source_id("twitter", tweet_id):
                                    continue

                                filepath = await _download_image(img_url, client)
                                if filepath:
                                    file_hash = _md5_file(filepath)

                                    if await _is_duplicate_hash(file_hash):
                                        await _log_fetch("twitter", tweet_id, img_url, None, file_hash, True)
                                        os.remove(filepath)
                                        continue

                                    downloaded.append(filepath)
                                    await _log_fetch("twitter", tweet_id, img_url, None, file_hash, False)
                                    logger.info("Downloaded from Twitter @%s", account)

                                await asyncio.sleep(REQUEST_DELAY)

                        rss_fetched = True

                    except Exception as e:
                        logger.warning("Nitter %s error for @%s: %s", instance, account, e)
                        continue

                if not rss_fetched:
                    logger.warning("All Nitter instances failed for @%s", account)

        return downloaded

    # ────────────── Web Scraping ──────────────

    async def fetch_web(self, urls: list[str] | None = None, limit: int = 5) -> list[str]:
        """ดึงรูปจากเว็บ 18+ gallery pages → return list of local file paths."""
        gallery_urls = urls or DEFAULT_WEB_URLS
        if not gallery_urls:
            logger.info("No web gallery URLs configured, skipping")
            return []

        downloaded: list[str] = []

        headers = {
            "User-Agent": REDDIT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*;q=0.8",
        }

        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20) as client:
            for gallery_url in gallery_urls:
                if len(downloaded) >= limit:
                    break

                try:
                    resp = await client.get(gallery_url)
                    if resp.status_code != 200:
                        logger.warning("Web gallery returned %d: %s", resp.status_code, gallery_url)
                        continue

                    html = resp.text

                    # Extract image URLs ทั่วไป
                    img_pattern = re.compile(
                        r'(?:src|data-src|href)=["\']'
                        r'(https?://[^"\']+\.(?:jpg|jpeg|png|gif|webp))'
                        r'["\']',
                        re.I,
                    )
                    img_urls = list(set(img_pattern.findall(html)))

                    # Filter: เอาเฉพาะรูปที่น่าจะเป็น content (ไม่ใช่ icon/logo)
                    filtered_urls = [
                        u for u in img_urls
                        if not any(skip in u.lower() for skip in [
                            "logo", "icon", "avatar", "banner", "favicon",
                            "sprite", "thumb_small", "pixel", "1x1",
                        ])
                    ]

                    for img_url in filtered_urls[:limit * 2]:
                        if len(downloaded) >= limit:
                            break

                        url_hash = hashlib.md5(img_url.encode()).hexdigest()[:16]
                        if await _is_duplicate_source_id("web", url_hash):
                            continue

                        filepath = await _download_image(img_url, client)
                        if filepath:
                            file_hash = _md5_file(filepath)

                            if await _is_duplicate_hash(file_hash):
                                await _log_fetch("web", url_hash, img_url, None, file_hash, True)
                                os.remove(filepath)
                                continue

                            downloaded.append(filepath)
                            await _log_fetch("web", url_hash, img_url, None, file_hash, False)
                            logger.info("Downloaded from web: %s", img_url[:80])

                        await asyncio.sleep(REQUEST_DELAY)

                except Exception as e:
                    logger.error("Web fetch error for %s: %s", gallery_url, e)
                    await _log_fetch("web", None, gallery_url, None, None, False, str(e))

        return downloaded

    # ────────────── Fetch All ──────────────

    async def fetch_all(self) -> int:
        """ดึงจากทุกแหล่ง → ส่งเข้า content_queue → return จำนวนรูปใหม่."""
        logger.info("🔄 Starting content auto-fetch...")

        # สร้างตาราง fetched_content_log ถ้ายังไม่มี
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        total_new = 0
        all_files: list[tuple[str, str]] = []  # (filepath, source)

        # ── แหล่ง 1: Reddit ──
        try:
            reddit_files = await self.fetch_reddit(limit=5)
            for f in reddit_files:
                all_files.append((f, "reddit"))
            logger.info("Reddit: ดึงได้ %d รูป", len(reddit_files))
        except Exception as e:
            logger.error("Reddit fetch failed entirely: %s", e)

        # ── แหล่ง 2: Twitter/Nitter ──
        try:
            twitter_files = await self.fetch_twitter(limit=3)
            for f in twitter_files:
                all_files.append((f, "twitter"))
            logger.info("Twitter: ดึงได้ %d รูป", len(twitter_files))
        except Exception as e:
            logger.error("Twitter fetch failed entirely: %s", e)

        # ── แหล่ง 3: Web ──
        try:
            web_files = await self.fetch_web(limit=3)
            for f in web_files:
                all_files.append((f, "web"))
            logger.info("Web: ดึงได้ %d รูป", len(web_files))
        except Exception as e:
            logger.error("Web fetch failed entirely: %s", e)

        # ── Upload to Telegram + Save to content_queue ──
        if not all_files:
            logger.info("ไม่มีรูปใหม่จากทุกแหล่ง")
            await _send_discord_log("📭 **Content Fetch: ไม่พบรูปใหม่จากทุกแหล่ง**")
            return 0

        bot = self.bot
        for filepath, source in all_files:
            try:
                file_id = await _upload_to_telegram(bot, filepath)
                if file_id:
                    saved = await _save_to_content_queue(file_id, source)
                    if saved:
                        total_new += 1
                        logger.info("✅ Added to content_queue from %s: %s", source, file_id[:20])
                else:
                    logger.warning("Failed to upload to Telegram: %s", filepath)
            except Exception as e:
                logger.error("Upload/save error: %s", e)
            finally:
                # ลบไฟล์ local หลังส่ง
                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                except Exception:
                    pass

        # ── Log to Discord ──
        sources_summary = {}
        for _, source in all_files:
            sources_summary[source] = sources_summary.get(source, 0) + 1

        source_str = " / ".join(f"{k}: {v}" for k, v in sources_summary.items())
        await _send_discord_log(
            f"📥 **Content Fetch Complete**\n"
            f"รูปใหม่ทั้งหมด: **{total_new}** รูป\n"
            f"แหล่ง: {source_str}\n"
            f"ส่งเข้า content_queue: {total_new} รูป"
        )

        logger.info("🔄 Content auto-fetch done: %d new images added", total_new)
        return total_new


# ──────────────────────────────────────────────
# Scheduled function (เรียกจาก main.py)
# ──────────────────────────────────────────────

async def fetch_new_content() -> int:
    """Entry point สำหรับ scheduled job — ดึงรูปใหม่ 5-10 รูป → ใส่ content_queue."""
    fetcher = ContentFetcher()
    return await fetcher.fetch_all()

"""Twitter/X Poster — โพสต์ tweet, tweet+รูป, thread ผ่าน tweepy.

ใช้ Twitter API v2 (tweepy.Client) สำหรับ create_tweet
ใช้ Twitter API v1.1 (tweepy.API) สำหรับ media_upload

Environment Variables ที่ต้องการ:
  - TWITTER_API_KEY          — OAuth 1.0a API Key (Consumer Key)
  - TWITTER_API_SECRET       — OAuth 1.0a API Secret (Consumer Secret)
  - TWITTER_ACCESS_TOKEN     — User Access Token
  - TWITTER_ACCESS_TOKEN_SECRET — User Access Token Secret

  หรือ (fallback เดิม):
  - TWITTER_API_KEY        → ใช้แทน API Key
  - TWITTER_API_SECRET    → ใช้แทน API Secret

⚠️ หมายเหตุ: TWITTER_API_KEY ใน .env ปัจจุบันเป็น OAuth 2.0 Client ID
   ต้องเปลี่ยนเป็น API Key (Consumer Key) จาก Developer Portal ถึงจะโพสต์ได้
   ไปที่: https://developer.x.com/en/portal/projects → Keys and tokens
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import tweepy

logger = logging.getLogger(__name__)


def _get_consumer_keys() -> tuple[str, str]:
    """Get consumer key/secret — ลอง TWITTER_API_KEY ก่อน, fallback TWITTER_API_KEY."""
    consumer_key = os.getenv("TWITTER_API_KEY") or os.getenv("TWITTER_API_KEY", "")
    consumer_secret = os.getenv("TWITTER_API_SECRET") or os.getenv("TWITTER_API_SECRET", "")
    return consumer_key, consumer_secret


def get_twitter_client() -> tweepy.Client:
    """สร้าง tweepy.Client สำหรับ Twitter API v2."""
    consumer_key, consumer_secret = _get_consumer_keys()
    client = tweepy.Client(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
        access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET"),
    )
    return client


def get_twitter_api() -> tweepy.API:
    """สร้าง tweepy.API สำหรับ Twitter API v1.1 (media upload)."""
    consumer_key, consumer_secret = _get_consumer_keys()
    auth = tweepy.OAuth1UserHandler(
        consumer_key,
        consumer_secret,
        os.getenv("TWITTER_ACCESS_TOKEN"),
        os.getenv("TWITTER_ACCESS_TOKEN_SECRET"),
    )
    return tweepy.API(auth)


def post_tweet(text: str) -> Optional[dict]:
    """โพสต์ tweet ข้อความอย่างเดียว.

    Returns:
        dict with tweet data on success, None on failure.
    """
    if len(text) > 280:
        logger.warning("Tweet text exceeds 280 chars (%d), truncating...", len(text))
        text = text[:277] + "..."

    try:
        client = get_twitter_client()
        response = client.create_tweet(text=text)
        tweet_data = response.data
        logger.info("✅ Tweet posted: id=%s", tweet_data.get("id"))
        return tweet_data
    except tweepy.TweepyException as exc:
        logger.error("❌ Failed to post tweet: %s", exc)
        return None


def post_tweet_with_image(text: str, image_path: str) -> Optional[dict]:
    """โพสต์ tweet พร้อมรูปภาพ.

    ใช้ v1.1 API upload media แล้ว v2 API create tweet พร้อม media_id.

    Args:
        text: ข้อความ tweet (≤ 280 chars)
        image_path: path ไปยังไฟล์รูป (jpg/png/gif)

    Returns:
        dict with tweet data on success, None on failure.
    """
    if len(text) > 280:
        logger.warning("Tweet text exceeds 280 chars (%d), truncating...", len(text))
        text = text[:277] + "..."

    try:
        # Step 1: Upload media via v1.1
        api = get_twitter_api()
        media = api.media_upload(filename=image_path)
        media_id = media.media_id
        logger.info("Media uploaded: media_id=%s", media_id)

        # Step 2: Create tweet with media via v2
        client = get_twitter_client()
        response = client.create_tweet(text=text, media_ids=[media_id])
        tweet_data = response.data
        logger.info("✅ Tweet with image posted: id=%s", tweet_data.get("id"))
        return tweet_data
    except tweepy.TweepyException as exc:
        logger.error("❌ Failed to post tweet with image: %s", exc)
        return None


def post_thread(tweets: list[str]) -> list[dict]:
    """โพสต์ thread (หลาย tweet ต่อกัน).

    Args:
        tweets: list ของข้อความ tweet แต่ละ tweet ≤ 280 chars

    Returns:
        list of tweet data dicts for successful posts.
    """
    if not tweets:
        logger.warning("Empty tweet list for thread")
        return []

    client = get_twitter_client()
    results = []
    previous_tweet_id = None

    for i, text in enumerate(tweets):
        if len(text) > 280:
            logger.warning("Thread tweet #%d exceeds 280 chars, truncating...", i + 1)
            text = text[:277] + "..."

        try:
            kwargs = {"text": text}
            if previous_tweet_id:
                kwargs["in_reply_to_tweet_id"] = previous_tweet_id

            response = client.create_tweet(**kwargs)
            tweet_data = response.data
            previous_tweet_id = tweet_data.get("id")
            results.append(tweet_data)
            logger.info("✅ Thread tweet #%d posted: id=%s", i + 1, previous_tweet_id)
        except tweepy.TweepyException as exc:
            logger.error("❌ Failed to post thread tweet #%d: %s", i + 1, exc)
            break

    return results

"""ทดสอบ Twitter API — run: python -m bots.twitter_bot.test_api

⚠️ ก่อนทดสอบ ต้องตั้ง env vars ให้ถูก:
  TWITTER_API_KEY       = OAuth 1.0a API Key (Consumer Key)
  TWITTER_API_SECRET    = OAuth 1.0a API Secret (Consumer Secret)
  TWITTER_ACCESS_TOKEN  = User Access Token
  TWITTER_ACCESS_TOKEN_SECRET = User Access Token Secret

ไปดึงได้ที่: https://developer.x.com/en/portal/projects → Keys and tokens
"""

import os
import sys

# Load .env if python-dotenv available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def test_credentials():
    """ตรวจสอบว่ามี credentials ครบ."""
    keys = {
        "consumer_key": os.getenv("TWITTER_API_KEY") or os.getenv("TWITTER_CLIENT_ID"),
        "consumer_secret": os.getenv("TWITTER_API_SECRET") or os.getenv("TWITTER_CLIENT_SECRET"),
        "access_token": os.getenv("TWITTER_ACCESS_TOKEN"),
        "access_token_secret": os.getenv("TWITTER_ACCESS_TOKEN_SECRET"),
    }

    print("=== Twitter API Credentials Check ===")
    all_ok = True
    for name, val in keys.items():
        status = "✅" if val else "❌ MISSING"
        preview = f"{val[:15]}..." if val else "N/A"
        print(f"  {name}: {status} ({preview})")
        if not val:
            all_ok = False

    # Check if CLIENT_ID is OAuth 2.0 (base64-encoded with ':')
    client_id = os.getenv("TWITTER_CLIENT_ID", "")
    api_key = os.getenv("TWITTER_API_KEY", "")
    if client_id and not api_key:
        import base64
        try:
            decoded = base64.b64decode(client_id).decode()
            if ":" in decoded:
                print(f"\n⚠️  TWITTER_CLIENT_ID ดูเหมือนเป็น OAuth 2.0 Client ID (decoded: {decoded})")
                print("   ต้องใช้ TWITTER_API_KEY (OAuth 1.0a Consumer Key) แทน!")
                print("   ไปดึงที่: https://developer.x.com/en/portal/projects → Keys and tokens")
                all_ok = False
        except Exception:
            pass

    return all_ok


def test_post():
    """ทดสอบโพสต์ tweet จริง."""
    from bots.twitter_bot.poster import post_tweet

    test_text = (
        "🔥 VIP เจริญพร — คลิปเต็มไม่เบลอ ทุกวัน\n\n"
        "✅ รวมกว่า 10,000 คลิป\n"
        "✅ Exclusive ก่อนใคร\n\n"
        "สมัครเลย 👇\n"
        "https://t.me/NamwarnJarern_bot\n\n"
        "#VIPเจริญพร #18plus #คลิปไทย"
    )

    print(f"\n=== Test Tweet ({len(test_text)} chars) ===")
    print(test_text)
    print("=" * 40)

    result = post_tweet(test_text)
    if result:
        print(f"\n✅ SUCCESS! Tweet posted: {result}")
        return True
    else:
        print("\n❌ FAILED — ดู error ด้านบน")
        return False


if __name__ == "__main__":
    ok = test_credentials()
    if not ok:
        print("\n❌ Credentials ไม่ครบ/ไม่ถูก — แก้ .env ก่อนทดสอบ")
        sys.exit(1)

    print("\n" + "=" * 40)
    confirm = input("จะทดสอบโพสต์ tweet จริงหรือไม่? (y/N): ").strip().lower()
    if confirm == "y":
        test_post()
    else:
        print("ข้าม — ใช้ 'y' เพื่อทดสอบโพสต์จริง")

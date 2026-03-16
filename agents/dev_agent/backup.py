"""Database Backup (บิ๊ก) - Backup PostgreSQL ไป DigitalOcean Spaces.

Pure Python, ไม่ใช้ AI
- ทุกวัน 03:00: pg_dump → DigitalOcean Spaces
- เก็บ 30 วัน ลบ backup เก่า
- Test restore ทุกอาทิตย์
- แจ้ง Discord #system-logs
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DB_HOST: str = os.environ.get("DB_HOST", "localhost")
DB_PORT: str = os.environ.get("DB_PORT", "5432")
DB_NAME: str = os.environ.get("DB_NAME", "charoenpon")
DB_USER: str = os.environ.get("DB_USER", "postgres")
DB_PASSWORD: str = os.environ.get("DB_PASSWORD", "postgres")

DO_SPACES_ENDPOINT: str = os.environ.get("DO_SPACES_ENDPOINT", "")
DO_SPACES_REGION: str = os.environ.get("DO_SPACES_REGION", "sgp1")
DO_SPACES_BUCKET: str = os.environ.get("DO_SPACES_BUCKET", "charoenpon-backups")
DO_SPACES_KEY: str = os.environ.get("DO_SPACES_KEY", "")
DO_SPACES_SECRET: str = os.environ.get("DO_SPACES_SECRET", "")

DISCORD_WEBHOOK_SYSTEM_LOGS: str = os.environ.get("DISCORD_WEBHOOK_SYSTEM_LOGS", "")

BACKUP_RETENTION_DAYS = 30
BACKUP_DIR = Path("/tmp/charoenpon_backups")
BACKUP_PREFIX = "charoenpon_db"

TEST_RESTORE_DB = "charoenpon_restore_test"


def _get_backup_filename() -> str:
    """สร้างชื่อไฟล์ backup ตามวันเวลา."""
    now = datetime.now(timezone.utc)
    return f"{BACKUP_PREFIX}_{now.strftime('%Y%m%d_%H%M%S')}.sql.gz"


def _get_pg_env() -> dict[str, str]:
    """สร้าง environment variables สำหรับ pg_dump."""
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD
    return env


async def run_pg_dump(output_path: Path) -> bool:
    """รัน pg_dump แล้ว gzip ผลลัพธ์."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = (
        f"pg_dump -h {DB_HOST} -p {DB_PORT} -U {DB_USER} -d {DB_NAME} "
        f"--format=custom --compress=6 -f {output_path}"
    )

    logger.info("Running pg_dump: %s", cmd)

    proc = await asyncio.create_subprocess_shell(
        cmd,
        env=_get_pg_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        logger.error("pg_dump failed (exit %d): %s", proc.returncode, stderr.decode())
        return False

    if not output_path.exists():
        logger.error("pg_dump output file not found: %s", output_path)
        return False

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("pg_dump completed: %s (%.1f MB)", output_path, size_mb)
    return True


async def upload_to_spaces(local_path: Path, remote_key: str) -> bool:
    """อัปโหลดไฟล์ไปยัง DigitalOcean Spaces ผ่าน S3-compatible API."""
    if not all([DO_SPACES_ENDPOINT, DO_SPACES_KEY, DO_SPACES_SECRET]):
        logger.warning("DigitalOcean Spaces credentials not configured")
        return False

    try:
        import boto3
        from botocore.config import Config

        session = boto3.session.Session()
        client = session.client(
            "s3",
            region_name=DO_SPACES_REGION,
            endpoint_url=DO_SPACES_ENDPOINT,
            aws_access_key_id=DO_SPACES_KEY,
            aws_secret_access_key=DO_SPACES_SECRET,
            config=Config(signature_version="s3v4"),
        )

        client.upload_file(
            str(local_path),
            DO_SPACES_BUCKET,
            remote_key,
        )

        logger.info("Uploaded %s to spaces://%s/%s", local_path.name, DO_SPACES_BUCKET, remote_key)
        return True

    except ImportError:
        logger.error("boto3 not installed — cannot upload to DigitalOcean Spaces")
        return False
    except Exception as exc:
        logger.error("Failed to upload to Spaces: %s", exc)
        return False


async def cleanup_old_backups() -> int:
    """ลบ backup เก่ากว่า BACKUP_RETENTION_DAYS วัน จาก Spaces."""
    if not all([DO_SPACES_ENDPOINT, DO_SPACES_KEY, DO_SPACES_SECRET]):
        logger.warning("DigitalOcean Spaces not configured, skipping cleanup")
        return 0

    try:
        import boto3
        from botocore.config import Config

        session = boto3.session.Session()
        client = session.client(
            "s3",
            region_name=DO_SPACES_REGION,
            endpoint_url=DO_SPACES_ENDPOINT,
            aws_access_key_id=DO_SPACES_KEY,
            aws_secret_access_key=DO_SPACES_SECRET,
            config=Config(signature_version="s3v4"),
        )

        cutoff = datetime.now(timezone.utc) - timedelta(days=BACKUP_RETENTION_DAYS)
        deleted_count = 0

        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=DO_SPACES_BUCKET, Prefix=f"backups/{BACKUP_PREFIX}"):
            for obj in page.get("Contents", []):
                if obj["LastModified"].replace(tzinfo=timezone.utc) < cutoff:
                    client.delete_object(Bucket=DO_SPACES_BUCKET, Key=obj["Key"])
                    deleted_count += 1
                    logger.info("Deleted old backup: %s", obj["Key"])

        logger.info("Cleanup completed: %d old backups deleted", deleted_count)
        return deleted_count

    except ImportError:
        logger.error("boto3 not installed — cannot cleanup Spaces")
        return 0
    except Exception as exc:
        logger.error("Cleanup failed: %s", exc)
        return 0


async def cleanup_local_backups() -> int:
    """ลบ backup เก่าจาก local disk."""
    if not BACKUP_DIR.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    deleted_count = 0

    for f in BACKUP_DIR.glob(f"{BACKUP_PREFIX}_*"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            f.unlink()
            deleted_count += 1
            logger.info("Deleted local backup: %s", f.name)

    return deleted_count


async def test_restore() -> dict[str, Any]:
    """Test restore backup ลง test database เพื่อตรวจสอบความสมบูรณ์."""
    logger.info("Starting backup restore test")

    latest_backup = _find_latest_local_backup()
    if not latest_backup:
        return {"success": False, "error": "No local backup found for restore test"}

    env = _get_pg_env()

    drop_cmd = f"dropdb -h {DB_HOST} -p {DB_PORT} -U {DB_USER} --if-exists {TEST_RESTORE_DB}"
    proc = await asyncio.create_subprocess_shell(
        drop_cmd, env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    create_cmd = f"createdb -h {DB_HOST} -p {DB_PORT} -U {DB_USER} {TEST_RESTORE_DB}"
    proc = await asyncio.create_subprocess_shell(
        create_cmd, env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return {"success": False, "error": f"Failed to create test DB: {stderr.decode()}"}

    restore_cmd = (
        f"pg_restore -h {DB_HOST} -p {DB_PORT} -U {DB_USER} "
        f"-d {TEST_RESTORE_DB} --no-owner --no-privileges {latest_backup}"
    )
    proc = await asyncio.create_subprocess_shell(
        restore_cmd, env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    restore_success = proc.returncode == 0

    drop_cmd = f"dropdb -h {DB_HOST} -p {DB_PORT} -U {DB_USER} --if-exists {TEST_RESTORE_DB}"
    proc = await asyncio.create_subprocess_shell(
        drop_cmd, env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    result = {
        "success": restore_success,
        "backup_file": str(latest_backup),
        "backup_size_mb": round(latest_backup.stat().st_size / (1024 * 1024), 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if not restore_success:
        result["error"] = stderr.decode()

    logger.info("Restore test %s: %s", "PASSED" if restore_success else "FAILED", latest_backup.name)
    return result


def _find_latest_local_backup() -> Path | None:
    """หา backup file ล่าสุดใน local directory."""
    if not BACKUP_DIR.exists():
        return None

    backups = sorted(BACKUP_DIR.glob(f"{BACKUP_PREFIX}_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return backups[0] if backups else None


async def send_discord_log(message: str) -> bool:
    """ส่ง log ไป Discord #system-logs."""
    if not DISCORD_WEBHOOK_SYSTEM_LOGS:
        logger.warning("No Discord webhook for system logs")
        return False

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                DISCORD_WEBHOOK_SYSTEM_LOGS,
                json={"content": message},
            )
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Failed to send Discord log: %s", exc)
        return False


async def run_daily_backup() -> dict[str, Any]:
    """รัน daily backup routine (03:00)."""
    logger.info("Starting daily backup routine")

    filename = _get_backup_filename()
    local_path = BACKUP_DIR / filename

    dump_ok = await run_pg_dump(local_path)
    if not dump_ok:
        msg = "❌ **Backup Failed**: pg_dump failed"
        await send_discord_log(msg)
        return {"success": False, "error": "pg_dump failed"}

    size_mb = round(local_path.stat().st_size / (1024 * 1024), 1)

    remote_key = f"backups/{filename}"
    upload_ok = await upload_to_spaces(local_path, remote_key)

    deleted = await cleanup_old_backups()
    local_deleted = await cleanup_local_backups()

    result = {
        "success": dump_ok and upload_ok,
        "filename": filename,
        "size_mb": size_mb,
        "uploaded": upload_ok,
        "old_backups_deleted": deleted,
        "local_backups_deleted": local_deleted,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    status = "✅" if result["success"] else "⚠️"
    msg = (
        f"{status} **Daily Backup** — {filename}\n"
        f"📦 Size: {size_mb} MB\n"
        f"☁️ Uploaded: {'Yes' if upload_ok else 'No'}\n"
        f"🗑️ Old backups cleaned: {deleted} (remote) + {local_deleted} (local)"
    )
    await send_discord_log(msg)

    logger.info("Daily backup completed: %s", result)
    return result


async def run_weekly_restore_test() -> dict[str, Any]:
    """รัน weekly restore test."""
    logger.info("Starting weekly restore test")

    result = await test_restore()

    status = "✅" if result["success"] else "❌"
    msg = (
        f"{status} **Weekly Restore Test**\n"
        f"📄 File: {result.get('backup_file', '-')}\n"
        f"📦 Size: {result.get('backup_size_mb', '?')} MB\n"
        f"Result: {'PASSED' if result['success'] else 'FAILED'}"
    )
    if not result["success"]:
        msg += f"\nError: {result.get('error', 'unknown')}"

    await send_discord_log(msg)

    logger.info("Weekly restore test completed: %s", "PASSED" if result["success"] else "FAILED")
    return result


async def run_backup_scheduler() -> None:
    """Main backup scheduler loop."""
    from shared.utils import TH_TZ

    logger.info("Backup scheduler started")

    while True:
        try:
            now = datetime.now(TH_TZ)

            if now.hour == 3 and now.minute < 5:
                await run_daily_backup()
                await asyncio.sleep(300)

            if now.weekday() == 6 and now.hour == 4 and now.minute < 5:
                await run_weekly_restore_test()
                await asyncio.sleep(300)

        except Exception as exc:
            logger.error("Backup scheduler error: %s", exc, exc_info=True)

        await asyncio.sleep(60)

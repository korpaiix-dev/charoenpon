#!/usr/bin/env python3
"""ลบ backup เก่าเกิน N วัน จาก DO Spaces"""
import sys
import os
import boto3
from botocore.client import Config
from datetime import datetime, timezone, timedelta

# Load .env manually
with open('/root/charoenpon/.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

def cleanup(retain_days=30):
    s3 = boto3.client(
        's3',
        region_name=os.getenv('DO_SPACES_REGION', 'sgp1'),
        endpoint_url=os.getenv('DO_SPACES_ENDPOINT', 'https://sgp1.digitaloceanspaces.com'),
        aws_access_key_id=os.getenv('DO_SPACES_KEY'),
        aws_secret_access_key=os.getenv('DO_SPACES_SECRET'),
        config=Config(signature_version='s3v4')
    )

    bucket = os.getenv('DO_SPACES_BUCKET', 'charoenpon-backup')
    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)

    response = s3.list_objects_v2(Bucket=bucket, Prefix='db/')
    if 'Contents' not in response:
        print("  ไม่มีไฟล์เก่าให้ลบ")
        return

    deleted = 0
    for obj in response['Contents']:
        if obj['LastModified'] < cutoff:
            print(f"  🗑️  ลบ: {obj['Key']} (อายุ: {obj['LastModified'].strftime('%Y-%m-%d')})")
            s3.delete_object(Bucket=bucket, Key=obj['Key'])
            deleted += 1

    print(f"  ✅ ลบทั้งหมด {deleted} ไฟล์")

if __name__ == '__main__':
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    cleanup(days)

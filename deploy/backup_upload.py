#!/usr/bin/env python3
"""Upload backup file to DigitalOcean Spaces"""
import sys
import os
import boto3
from botocore.client import Config

# Load .env manually
with open('/root/charoenpon/.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

def upload(local_path, remote_name):
    s3 = boto3.client(
        's3',
        region_name=os.getenv('DO_SPACES_REGION', 'sgp1'),
        endpoint_url=os.getenv('DO_SPACES_ENDPOINT', 'https://sgp1.digitaloceanspaces.com'),
        aws_access_key_id=os.getenv('DO_SPACES_KEY'),
        aws_secret_access_key=os.getenv('DO_SPACES_SECRET'),
        config=Config(signature_version='s3v4')
    )

    bucket = os.getenv('DO_SPACES_BUCKET', 'charoenpon-backup')

    print(f"  Uploading to s3://{bucket}/db/{remote_name}")
    s3.upload_file(
        local_path,
        bucket,
        f"db/{remote_name}",
        ExtraArgs={'ACL': 'private'}
    )
    print(f"  ✅ Upload สำเร็จ")

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: backup_upload.py <local_path> <remote_name>")
        sys.exit(1)
    upload(sys.argv[1], sys.argv[2])

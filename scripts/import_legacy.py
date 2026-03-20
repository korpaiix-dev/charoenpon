#!/usr/bin/env python3
"""Import legacy CSV customers into charoenpon DB."""

import csv
import subprocess
import sys
from datetime import datetime

# === Config ===
CSV1 = "/root/.openclaw/media/inbound/JaroenPorn_DB_-_Sheet1---ba5ecb72-3d5f-4088-a418-896f5c7fddf6.csv"
CSV2 = "/root/charoenpon/data/members2_latest.csv"

# Package price → package_id mapping
PRICE_TO_PKG = {
    "300": 1,   # VIP 30 วัน
    "500": 2,   # OnlyFans + VIP 30 วัน
    "999": 3,   # GOD MODE 90 วัน
    "1299": 3,  # GOD MODE 90 วัน
    "2499": 4,  # GOD MODE ถาวร
}

# Members2 status → subscription status
STATUS_MAP_M2 = {
    "Paid": "ACTIVE",
    "Renewed/Paid": "ACTIVE",
    "Active/Updated": "ACTIVE",
    "Permanent": "ACTIVE",
    "Expired": "EXPIRED",
    "Migrated": None,       # skip subscription
    "Free Trial 7 Days": None,  # skip subscription
}

# Sheet1 status mapping
STATUS_MAP_S1 = {
    "Active": "ACTIVE",
    "New": None,  # no subscription
}

def psql(sql):
    """Execute SQL via docker exec and return output."""
    cmd = [
        "docker", "exec", "charoenpon-postgres",
        "psql", "-U", "postgres", "-d", "charoenpon",
        "-t", "-A", "-c", sql
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise Exception(f"SQL error: {result.stderr.strip()}\nSQL: {sql}")
    return result.stdout.strip()

def psql_check(sql):
    """Execute SQL, return True if it returns any rows."""
    out = psql(sql)
    return bool(out)

def parse_date(date_str):
    """Parse various date formats, return YYYY-MM-DD or None."""
    if not date_str or date_str.strip() == "-" or date_str.strip() == "":
        return None
    date_str = date_str.strip()
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]:
        try:
            dt = datetime.strptime(date_str.split(".")[0].strip(), fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None

def is_permanent_date(date_str):
    """Check if end_date indicates permanent (year >= 2099)."""
    d = parse_date(date_str)
    if d and int(d[:4]) >= 2099:
        return True
    return False

def escape_sql(s):
    """Escape single quotes for SQL."""
    if s is None:
        return ""
    return s.replace("'", "''")

def main():
    stats = {"users_created": 0, "users_skipped": 0, "subs_created": 0, "subs_skipped": 0, "errors": 0}
    
    # 1. Get existing telegram_ids from DB
    print("Loading existing users from DB...")
    existing_raw = psql("SELECT telegram_id FROM users;")
    existing_tids = set()
    if existing_raw:
        existing_tids = set(existing_raw.split("\n"))
    print(f"  Existing users in DB: {len(existing_tids)}")
    
    # 2. Read CSV2 (Members2) - newer, takes priority
    print("\nReading Members2 CSV...")
    m2_data = {}
    with open(CSV2, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if len(row) < 5 or not row[0].strip():
                continue
            uid = row[0].strip()
            if not uid.isdigit():
                continue
            name = row[1].strip() if len(row) > 1 else ""
            join_date = row[2].strip() if len(row) > 2 else ""
            expiry_date = row[3].strip() if len(row) > 3 else ""
            status = row[4].strip() if len(row) > 4 else ""
            m2_data[uid] = {
                "telegram_id": uid,
                "name": name,
                "start_date": join_date,
                "end_date": expiry_date,
                "status": status,
                "source": "members2",
            }
    print(f"  Members2 records: {len(m2_data)}")
    
    # 3. Read CSV1 (Sheet1)
    print("\nReading Sheet1 CSV...")
    s1_data = {}
    with open(CSV1, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if len(row) < 8 or not row[0].strip():
                continue
            uid = row[0].strip()
            if not uid.isdigit():
                continue
            name = row[1].strip() if len(row) > 1 else ""
            package = row[2].strip() if len(row) > 2 else "-"
            price = row[3].strip() if len(row) > 3 else ""
            start_date = row[5].strip() if len(row) > 5 else ""
            end_date = row[6].strip() if len(row) > 6 else ""
            status = row[7].strip() if len(row) > 7 else ""
            s1_data[uid] = {
                "telegram_id": uid,
                "name": name,
                "package": package,
                "price": price,
                "start_date": start_date,
                "end_date": end_date,
                "status": status,
                "source": "sheet1",
            }
    print(f"  Sheet1 records: {len(s1_data)}")
    
    # 4. Merge — Members2 takes priority for overlapping users
    all_uids = set(list(s1_data.keys()) + list(m2_data.keys()))
    print(f"\nTotal unique User IDs: {len(all_uids)}")
    overlap = set(s1_data.keys()) & set(m2_data.keys())
    print(f"  Overlapping in both files: {len(overlap)}")
    
    merged = {}
    for uid in all_uids:
        if uid in m2_data and uid in s1_data:
            # Use Members2 status/dates, Sheet1 package info
            rec = {
                "telegram_id": uid,
                "name": m2_data[uid]["name"] or s1_data[uid]["name"],
                "package": s1_data[uid]["package"],
                "price": s1_data[uid]["price"],
                "start_date": m2_data[uid]["start_date"],
                "end_date": m2_data[uid]["end_date"],
                "status": m2_data[uid]["status"],
                "source": "merged",
            }
            merged[uid] = rec
        elif uid in m2_data:
            # Members2 only — no package info from Sheet1
            rec = m2_data[uid].copy()
            rec["package"] = None
            rec["price"] = None
            # Permanent → GOD MODE ถาวร
            if rec["status"] == "Permanent":
                rec["package"] = "2499"
                rec["price"] = "2499"
            merged[uid] = rec
        else:
            # Sheet1 only
            merged[uid] = s1_data[uid].copy()
    
    # 5. Import
    print("\n=== Starting Import ===\n")
    
    # Batch: collect all user inserts
    batch_size = 100
    records = list(merged.values())
    
    for i, rec in enumerate(records):
        tid = rec["telegram_id"]
        name = rec.get("name", "") or ""
        
        # --- Create user if not exists ---
        if tid in existing_tids:
            stats["users_skipped"] += 1
        else:
            try:
                fname = escape_sql(name[:255]) if name else ""
                sql = (
                    f"INSERT INTO users (telegram_id, first_name, is_admin, is_banned, total_spent) "
                    f"VALUES ({tid}, '{fname}', false, false, 0) "
                    f"ON CONFLICT (telegram_id) DO NOTHING;"
                )
                psql(sql)
                existing_tids.add(tid)
                stats["users_created"] += 1
            except Exception as e:
                print(f"  ERROR creating user {tid}: {e}")
                stats["errors"] += 1
                continue
        
        # --- Determine subscription ---
        status_raw = rec.get("status", "")
        
        # Determine sub_status
        if rec.get("source") in ("members2", "merged"):
            sub_status = STATUS_MAP_M2.get(status_raw)
        else:
            sub_status = STATUS_MAP_S1.get(status_raw)
        
        # Skip if no subscription needed (New, None status, etc.)
        if sub_status is None:
            stats["subs_skipped"] += 1
            continue
        
        # Determine package_id
        pkg_str = rec.get("package") or rec.get("price") or "-"
        if pkg_str == "-" or pkg_str not in PRICE_TO_PKG:
            # For Members2-only non-Permanent with no package → skip sub
            if rec.get("source") == "members2" and not rec.get("package"):
                stats["subs_skipped"] += 1
                continue
            # Unknown package from Sheet1
            if pkg_str not in PRICE_TO_PKG:
                stats["subs_skipped"] += 1
                continue
        
        package_id = PRICE_TO_PKG[pkg_str]
        
        # Parse dates
        start_date = parse_date(rec.get("start_date"))
        end_date = parse_date(rec.get("end_date"))
        
        if not start_date:
            start_date = "2026-01-19"  # default from earliest records
        
        # Handle permanent
        if is_permanent_date(rec.get("end_date", "")):
            end_date = "2099-12-31"
            package_id = 4  # GOD MODE ถาวร
            sub_status = "ACTIVE"
        
        if not end_date:
            # Calculate from package duration
            durations = {1: 30, 2: 30, 3: 90, 4: 36500}
            from datetime import timedelta
            try:
                sd = datetime.strptime(start_date, "%Y-%m-%d")
                ed = sd + timedelta(days=durations.get(package_id, 30))
                end_date = ed.strftime("%Y-%m-%d")
            except:
                end_date = "2026-04-19"
        
        # Get user_id from DB
        try:
            user_id = psql(f"SELECT id FROM users WHERE telegram_id = {tid};")
            if not user_id:
                stats["subs_skipped"] += 1
                continue
            
            # Check duplicate subscription (user_id + package_id + start_date)
            dup_check = psql(
                f"SELECT 1 FROM subscriptions WHERE user_id = {user_id} "
                f"AND package_id = {package_id} AND start_date::date = '{start_date}'::date LIMIT 1;"
            )
            if dup_check:
                stats["subs_skipped"] += 1
                continue
            
            sql = (
                f"INSERT INTO subscriptions (user_id, package_id, status, start_date, end_date, auto_renew) "
                f"VALUES ({user_id}, {package_id}, '{sub_status}', '{start_date}', '{end_date}', false);"
            )
            psql(sql)
            stats["subs_created"] += 1
        except Exception as e:
            print(f"  ERROR creating sub for {tid}: {e}")
            stats["errors"] += 1
        
        # Progress
        if (i + 1) % 500 == 0:
            print(f"  Progress: {i+1}/{len(records)} | Users: +{stats['users_created']} | Subs: +{stats['subs_created']}")
    
    # 6. Summary
    print("\n" + "=" * 50)
    print("📊 IMPORT SUMMARY")
    print("=" * 50)
    print(f"Total records processed: {len(records)}")
    print(f"✅ Users created:        {stats['users_created']}")
    print(f"⏭️  Users skipped (exist): {stats['users_skipped']}")
    print(f"✅ Subscriptions created: {stats['subs_created']}")
    print(f"⏭️  Subscriptions skipped: {stats['subs_skipped']}")
    print(f"❌ Errors:               {stats['errors']}")
    print("=" * 50)

if __name__ == "__main__":
    main()

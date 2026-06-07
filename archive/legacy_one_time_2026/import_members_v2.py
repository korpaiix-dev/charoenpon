"""Import members from Members CSV v2 — proper status mapping."""
import csv
import asyncio
from datetime import datetime
from decimal import Decimal

from shared.database import init_db, get_session
from shared.models import User, Subscription, SubscriptionStatus
from sqlalchemy import select

CSV_PATH = "/app/members.csv"

# All paid/active statuses → ACTIVE
ACTIVE_STATUSES = {"Permanent", "Active/Updated", "Paid", "Renewed/Paid", "Migrated", "Free Trial 7 Days"}

def guess_package_id(expiry_date, start_date):
    """Guess package from expiry date.
    
    GOD ถาวร (id=4): expiry >= 2099
    GOD 90 วัน (id=3): duration ~90 days
    OF+VIP 30 วัน (id=2): can't tell from date alone
    VIP 30 วัน (id=1): duration ~30 days or default
    """
    if expiry_date.year >= 2099:
        return 4  # GOD MODE ถาวร
    
    duration = (expiry_date - start_date).days
    if duration >= 80:  # ~90 days
        return 3  # GOD MODE 90 วัน
    else:
        return 1  # VIP 30 วัน (default for monthly)

async def main():
    await init_db()
    
    stats = {"users": 0, "active_subs": 0, "expired_subs": 0, "skipped": 0}
    pkg_count = {1: 0, 2: 0, 3: 0, 4: 0}
    
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    print(f"📊 Total rows: {len(rows)}")
    
    for row in rows:
        user_id_str = row.get("User ID", "").strip()
        name = row.get("Name", "").strip()
        status = row.get("Status", "").strip()
        join_str = row.get("Join Date", "").strip()
        expiry_str = row.get("Expiry Date", "").strip()
        
        if not user_id_str or not user_id_str.isdigit():
            stats["skipped"] += 1
            continue
        
        telegram_id = int(user_id_str)
        
        # Parse dates
        def parse_dt(s):
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
                try:
                    return datetime.strptime(s[:len(fmt.replace('%','x'))], fmt)
                except:
                    continue
            return None
        
        start_date = parse_dt(join_str) or datetime.utcnow()
        end_date = parse_dt(expiry_str) or datetime(2099, 12, 31)
        
        # Determine status
        if status in ACTIVE_STATUSES:
            sub_status = SubscriptionStatus.ACTIVE
        elif status == "Expired":
            sub_status = SubscriptionStatus.EXPIRED
        else:
            stats["skipped"] += 1
            continue
        
        # Guess package
        pkg_id = guess_package_id(end_date, start_date)
        
        async with get_session() as session:
            # Create user
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            db_user = result.scalar_one_or_none()
            
            if not db_user:
                db_user = User(
                    telegram_id=telegram_id,
                    first_name=name or "ลูกค้า",
                )
                session.add(db_user)
                await session.flush()
                stats["users"] += 1
            
            # Check duplicate
            sub_check = await session.execute(
                select(Subscription).where(Subscription.user_id == db_user.id)
            )
            if sub_check.scalar_one_or_none():
                continue
            
            sub = Subscription(
                user_id=db_user.id,
                package_id=pkg_id,
                status=sub_status,
                start_date=start_date,
                end_date=end_date,
            )
            session.add(sub)
            pkg_count[pkg_id] += 1
            
            if sub_status == SubscriptionStatus.ACTIVE:
                stats["active_subs"] += 1
            else:
                stats["expired_subs"] += 1
    
    print(f"\n✅ Import complete!")
    print(f"   👤 Users: {stats['users']}")
    print(f"   🟢 Active: {stats['active_subs']}")
    print(f"   🔴 Expired: {stats['expired_subs']}")
    print(f"   ⏭️ Skipped: {stats['skipped']}")
    print(f"\n📦 แพ็กเกจ:")
    print(f"   VIP 30 วัน (300): {pkg_count[1]}")
    print(f"   OF+VIP 30 วัน (500): {pkg_count[2]}")
    print(f"   GOD 90 วัน (1299): {pkg_count[3]}")
    print(f"   GOD ถาวร (2499): {pkg_count[4]}")

asyncio.run(main())

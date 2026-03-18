"""Import members from Members CSV into charoenpon database."""
import csv
import asyncio
from datetime import datetime
from decimal import Decimal

from shared.database import init_db, get_session
from shared.models import User, Subscription, SubscriptionStatus
from sqlalchemy import select

CSV_PATH = "/app/members.csv"

# Active statuses
ACTIVE_STATUSES = {"Permanent", "Active/Updated", "Paid", "Renewed/Paid", "Migrated", "Free Trial 7 Days"}
EXPIRED_STATUSES = {"Expired"}

async def main():
    await init_db()
    
    imported_users = 0
    imported_subs = 0
    skipped = 0
    
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    print(f"Total rows: {len(rows)}")
    
    for row in rows:
        user_id_str = row.get("User ID", "").strip()
        name = row.get("Name", "").strip()
        status = row.get("Status", "").strip()
        join_date_str = row.get("Join Date", "").strip()
        expiry_date_str = row.get("Expiry Date", "").strip()
        
        if not user_id_str or not user_id_str.isdigit():
            skipped += 1
            continue
        
        telegram_id = int(user_id_str)
        
        async with get_session() as session:
            # Create or get user
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
                imported_users += 1
            
            # Parse dates
            try:
                start_date = datetime.strptime(join_date_str[:19], "%Y-%m-%d %H:%M:%S") if join_date_str else datetime.utcnow()
            except ValueError:
                try:
                    start_date = datetime.strptime(join_date_str[:10], "%Y-%m-%d")
                except:
                    start_date = datetime.utcnow()
            
            try:
                end_date = datetime.strptime(expiry_date_str[:19], "%Y-%m-%d %H:%M:%S") if expiry_date_str else datetime(2099, 12, 31)
            except ValueError:
                try:
                    end_date = datetime.strptime(expiry_date_str[:10], "%Y-%m-%d")
                except:
                    end_date = datetime(2099, 12, 31)
            
            # Determine subscription status
            if status in ACTIVE_STATUSES:
                sub_status = SubscriptionStatus.ACTIVE
            elif status in EXPIRED_STATUSES:
                sub_status = SubscriptionStatus.EXPIRED
            else:
                skipped += 1
                continue
            
            # Use package_id=1 (VIP 300) as default — actual package unknown from this CSV
            # Permanent = GOD MODE (package_id=4)
            if status == "Permanent" or end_date.year >= 2099:
                pkg_id = 4  # GOD MODE ถาวร
            else:
                pkg_id = 1  # VIP 300 as default
            
            # Check if sub already exists
            sub_check = await session.execute(
                select(Subscription).where(
                    Subscription.user_id == db_user.id,
                )
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
            imported_subs += 1
    
    print(f"\n✅ Import complete!")
    print(f"   Users imported: {imported_users}")
    print(f"   Subscriptions: {imported_subs}")
    print(f"   Skipped: {skipped}")

asyncio.run(main())

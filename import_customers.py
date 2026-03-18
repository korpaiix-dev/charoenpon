"""Import customer data from CSV into charoenpon database."""
import csv
import asyncio
from datetime import datetime
from decimal import Decimal

from shared.database import init_db, get_session
from shared.models import User, Package, Payment, PaymentMethod, PaymentStatus, Subscription, SubscriptionStatus

CSV_PATH = "/root/.openclaw/media/inbound/JaroenPorn_DB_-_Sheet1---ba5ecb72-3d5f-4088-a418-896f5c7fddf6.csv"

# Map price to tier
PRICE_TO_TIER = {
    "300": "300",
    "500": "500", 
    "999": "1299",  # old price maps to 1299 tier
    "1299": "1299",
    "2499": "2499",
    "459": "500",   # old price maps to 500 tier
}

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
        name = row.get("ชื่อลูกค้า", "").strip()
        price_str = row.get("ยอดเงิน", "").strip()
        status = row.get("สถานะ", "").strip()
        start_date_str = row.get("วันที่เริ่ม", "").strip()
        end_date_str = row.get("วันที่หมดอายุ", "").strip()
        
        if not user_id_str or not user_id_str.isdigit():
            skipped += 1
            continue
        
        telegram_id = int(user_id_str)
        
        async with get_session() as session:
            from sqlalchemy import select
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
            
            # Only create subscription for Active users with valid data
            if status == "Active" and price_str and start_date_str and end_date_str:
                tier = PRICE_TO_TIER.get(price_str)
                if not tier:
                    skipped += 1
                    continue
                
                # Find package
                from shared.models import PackageTier
                pkg_result = await session.execute(
                    select(Package).where(Package.tier == PackageTier(tier))
                )
                package = pkg_result.scalar_one_or_none()
                if not package:
                    skipped += 1
                    continue
                
                # Parse dates
                try:
                    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
                    end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
                except ValueError:
                    skipped += 1
                    continue
                
                # Check if sub already exists
                sub_check = await session.execute(
                    select(Subscription).where(
                        Subscription.user_id == db_user.id,
                        Subscription.package_id == package.id,
                    )
                )
                if sub_check.scalar_one_or_none():
                    continue  # already imported
                
                # Create subscription
                sub = Subscription(
                    user_id=db_user.id,
                    package_id=package.id,
                    status=SubscriptionStatus.ACTIVE,
                    start_date=start_date,
                    end_date=end_date,
                )
                session.add(sub)
                
                # Update total spent
                db_user.total_spent = (db_user.total_spent or Decimal("0")) + Decimal(price_str)
                
                imported_subs += 1
    
    print(f"\n✅ Import complete!")
    print(f"   Users imported: {imported_users}")
    print(f"   Subscriptions: {imported_subs}")
    print(f"   Skipped: {skipped}")

asyncio.run(main())

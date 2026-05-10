import asyncio, asyncpg, os
async def main():
    conn=await asyncpg.connect(host='postgres',database='charoenpon',user='postgres',password=os.getenv('POSTGRES_PASSWORD'))
    for q,name in [
        ("select status::text, count(*) c from payments group by 1 order by 1", 'payment statuses'),
        ("select status::text, count(*) c from subscriptions group by 1 order by 1", 'subscription statuses'),
        ("select min(created_at), max(created_at), count(*) from payments", 'payments date range'),
        ("select p.id,p.amount,p.status::text,p.created_at, pkg.name from payments p left join packages pkg on p.package_id=pkg.id order by p.created_at desc limit 5", 'recent payments')
    ]:
        print('---',name)
        rows=await conn.fetch(q)
        for r in rows: print(dict(r))
    await conn.close()
asyncio.run(main())

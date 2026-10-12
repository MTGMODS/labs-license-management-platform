"""Synthetic Lab 4 dataset; refuses to run outside the explicit lab profile."""
import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, insert, select

from app.infrastructure.cache_aside import CacheAsideStats, STATS_KEY
from app.infrastructure.repository import LicenseModel, TransactionModel
from app.shared.config import settings
from app.shared.database import AsyncSessionLocal, engine


async def seed(rows):
    if os.environ.get("LAB4_DEMO") != "true" or not settings.STATS_CACHE_ASIDE:
        raise RuntimeError("Use only docker-compose.cache.yml cache-tools profile")
    if not 1 <= rows <= 60000:
        raise ValueError("rows must be between 1 and 60000")
    cache = CacheAsideStats.from_url(settings.REDIS_URL, STATS_KEY)
    token = await cache.begin_write()
    try:
        async with AsyncSessionLocal() as db:
            existing = await db.scalar(select(func.count()).select_from(LicenseModel).where(LicenseModel.key.like("L4DB-%")))
            if existing:
                print(f"Dataset already exists: {existing} synthetic licenses; no changes")
                return
            now = datetime.now(timezone.utc)
            licenses = [{"key": f"L4DB-{i:04X}-0000-0000", "user_id": 100000 + i,
                         "duration_days": 30, "max_devices": 2, "reset_limit": 0,
                         "status": "ACTIVE" if i % 3 else "EXPIRED",
                         "activated_at": now - timedelta(days=i % 365),
                         "expires_at": now + timedelta(days=30) if i % 3 else now - timedelta(days=1)}
                        for i in range(1, rows + 1)]
            result = await db.execute(insert(LicenseModel).returning(LicenseModel.id, LicenseModel.user_id), licenses)
            values = list(result)
            await db.execute(insert(TransactionModel), [{"license_id": row.id, "user_id": row.user_id,
                                                         "amount": 3, "payment_method": "Stars",
                                                         "status": "COMPLETED",
                                                         "purchased_at": now - timedelta(days=i % 365)}
                                                        for i, row in enumerate(values)])
            await db.commit()
            print(f"Created {rows} synthetic licenses + purchases")
    finally:
        await cache.end_write(token)
        await cache.aclose()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=2000)
    asyncio.run(seed(parser.parse_args().rows))

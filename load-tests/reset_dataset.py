"""Reset ONLY the explicit synthetic Lab 5 DB; used between controlled runs."""
import asyncio
import os

from sqlalchemy import text
from app.shared.config import settings
from app.shared.database import engine


async def reset():
    if os.environ.get('LAB5_DEMO') != 'true' or not settings.STATS_CACHE_ASIDE:
        raise RuntimeError('Only cache-tools in docker-compose.load.yml may reset this dataset')
    async with engine.begin() as conn:
        await conn.execute(text('TRUNCATE license_activations, transaction_purchases, licenses RESTART IDENTITY CASCADE'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS pg_stat_statements'))
    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(reset())

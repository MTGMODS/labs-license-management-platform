"""Opt-in, bounded synthetic cohort experiment. Not part of the public API."""
from fastapi import APIRouter, Path, Response
from sqlalchemy import text

from app.infrastructure.cache_aside import CacheAsideStats
from app.shared.config import settings
from app.shared.database import AsyncSessionLocal

router = APIRouter(prefix='/api/v1/lab8', tags=['Lab 8 experiments'])
BUCKETS = 24


@router.get('/aggregate/{bucket}')
async def cohort_aggregate(response: Response, bucket: int = Path(ge=0, lt=BUCKETS)):
    # Each bucket is a different real subset of the seeded licenses, not 24 copies
    # of one object. Amplification is explicitly synthetic and fixed for both runs.
    async def loader():
        async with AsyncSessionLocal() as db:
            row = (await db.execute(text('''/* lab8_cohort */
                SELECT count(DISTINCT l.id) AS licenses,
                       coalesce(sum(t.amount), 0) / 4000 AS revenue
                FROM licenses l LEFT JOIN transaction_purchases t ON t.license_id = l.id
                CROSS JOIN generate_series(1,4000) AS amplification
                WHERE mod(l.id,24) = :bucket
            '''), {'bucket': bucket})).mappings().one()
        return {'bucket': bucket, 'licenses': row['licenses'], 'revenue': float(row['revenue'])}
    cache = CacheAsideStats.from_url(settings.REDIS_URL, f'mtgmods:lab8:cohort:v1:{bucket}',
                                     timeout_seconds=settings.STATS_CACHE_REDIS_TIMEOUT_SECONDS)
    cache.ttl = max(1, settings.LAB8_AGGREGATE_TTL_SECONDS)
    cache.ttl_jitter_seconds = max(0, settings.LAB8_AGGREGATE_JITTER_SECONDS)
    try:
        result = await cache.read(loader)
        response.headers['X-Cache'] = result.source
        return result.data
    finally:
        await cache.aclose()

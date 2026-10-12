"""Show actual dispersed Redis TTLs on synthetic Lab 8 demonstration keys."""
import asyncio
import json
import os

from app.infrastructure.cache_aside import CacheAsideStats
from app.shared.config import settings


async def demonstrate():
    if os.environ.get('LAB8_DEMO') != 'true' or not settings.STATS_CACHE_ASIDE:
        raise RuntimeError('Use only cache-tools in the explicit Lab 8 profile')
    observed = []
    for index in range(20):
        cache = CacheAsideStats.from_url(settings.REDIS_URL, f'mtgmods:lab8:ttl-demo:{index}')
        cache.ttl_jitter_seconds = 30
        try:
            await cache.invalidate()
            async def value(i=index):
                return {'demo_aggregate': i}
            await cache.read(value)
            observed.append(await cache.redis.ttl(cache.key))
        finally:
            await cache.aclose()
    assert all(269 <= ttl <= 330 for ttl in observed)
    assert len(set(observed)) > 1
    print(json.dumps({'base_seconds': 300, 'jitter_seconds': 30, 'observed_ttls_seconds': observed}))


if __name__ == '__main__':
    asyncio.run(demonstrate())

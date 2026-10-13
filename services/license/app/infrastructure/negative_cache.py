"""Short-lived absence markers; positive/private license data is never cached."""
from redis.exceptions import RedisError

from app.infrastructure.cache_aside import STATS_KEY

_READ = """
local revision = redis.call('GET', KEYS[1]) or '0'
return {revision, redis.call('GET', KEYS[2]) or '', redis.call('SCARD', KEYS[3])}
"""
_STORE = """
if (redis.call('GET', KEYS[1]) or '0') == ARGV[1]
   and redis.call('SCARD', KEYS[3]) == 0 then
    redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
    return 1
end
return 0
"""


class NegativeLicenseCache:
    def __init__(self, redis, ttl=15, enabled=True):
        self.redis = redis  # Borrow the lifespan-owned Redis client.
        self.ttl = max(1, min(ttl, 60))
        self.enabled = enabled

    @staticmethod
    def key(license_id):
        return f'mtgmods:license:absent:v1:{license_id}'

    async def read(self, license_id, loader):
        if not self.enabled:
            return await loader(), 'BYPASS'
        keys = (STATS_KEY + ':revision', self.key(license_id), STATS_KEY + ':writers')
        try:
            revision, marker, writers = await self.redis.eval(_READ, 3, *keys)
            if not writers and marker and marker == revision:
                return None, 'HIT'
        except RedisError:
            return await loader(), 'BYPASS'
        result = await loader()  # DB errors must propagate, never become cached 404s.
        if result is not None or writers:
            return result, 'BYPASS'
        try:
            stored = await self.redis.eval(_STORE, 3, *keys, revision, self.ttl)
            if stored:
                return None, 'MISS'
            # A concurrent mutation may have created the missing entity.
            return await loader(), 'BYPASS'
        except RedisError:
            return None, 'BYPASS'

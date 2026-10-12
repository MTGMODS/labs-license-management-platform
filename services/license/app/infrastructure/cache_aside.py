"""Strict cache-aside for Lab 4. No process-local data, locks or stale reads."""
import asyncio
import json
import logging
import secrets
from dataclasses import dataclass

from fastapi import BackgroundTasks, HTTPException
from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError

from app.infrastructure.stats_cache import PublicStatsCache, StatsLoader

logger = logging.getLogger(__name__)
STATS_KEY = "mtgmods:license:public_stats:v2"

_READ = """
return {redis.call('GET', KEYS[1]) or '0', redis.call('GET', KEYS[2]) or '', redis.call('SCARD', KEYS[3])}
"""
_INVALIDATE = """
local revision = redis.call('INCR', KEYS[1])
redis.call('DEL', KEYS[2])
return revision
"""
_STORE = """
if redis.call('GET', KEYS[1]) == ARGV[1]
   and (redis.call('GET', KEYS[2]) or '0') == ARGV[2]
   and redis.call('SCARD', KEYS[4]) == 0 then
    redis.call('SET', KEYS[3], ARGV[3], 'EX', ARGV[4])
    return 1
end
return 0
"""
_BEGIN_WRITE = """
redis.call('SADD', KEYS[3], ARGV[1])
local revision = redis.call('INCR', KEYS[1])
redis.call('DEL', KEYS[2])
return revision
"""
_END_WRITE = """
local revision = redis.call('INCR', KEYS[1])
redis.call('DEL', KEYS[2])
redis.call('SREM', KEYS[3], ARGV[1])
return revision
"""
_UNLOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class CacheResult:
    data: dict
    source: str


class CacheAsideStats(PublicStatsCache):
    """TTL expiration + revision-fenced invalidation across all API instances."""

    def __init__(self, redis, key=STATS_KEY, ttl=300, enabled=True):
        super().__init__(redis, key)
        self.revision_key = f"{key}:revision"
        self.writers_key = f"{key}:writers"
        self.ttl = ttl
        self.enabled = enabled

    async def read(self, loader: StatsLoader) -> CacheResult:
        if not self.enabled:
            return CacheResult(await self._compute(loader), "BYPASS")
        deadline = asyncio.get_running_loop().time() + self.GENERATION_TIMEOUT_SECONDS
        while True:
            token = secrets.token_hex(16)
            acquired = False
            try:
                revision, raw, writers = await self.redis.eval(_READ, 3, self.revision_key, self.key, self.writers_key)
                if writers:
                    return CacheResult(await self._compute(loader), "BYPASS")
                if raw:
                    try:
                        data = json.loads(raw)
                        if isinstance(data, dict):
                            return CacheResult(data, "HIT")
                    except (ValueError, TypeError):
                        logger.warning("Malformed cache entry: %s", self.key)
                acquired = await self.redis.set(self.lock_key, token, nx=True, ex=self.LOCK_SECONDS)
                if acquired:
                    # Recheck after acquiring: another owner may have just filled it.
                    revision, raw, writers = await self.redis.eval(_READ, 3, self.revision_key, self.key, self.writers_key)
                    if writers:
                        return CacheResult(await self._compute(loader), "BYPASS")
                    if raw:
                        try:
                            data = json.loads(raw)
                            if isinstance(data, dict):
                                return CacheResult(data, "HIT")
                        except (ValueError, TypeError):
                            pass
                    data = await self._compute(loader)
                    stored = await self.redis.eval(
                        _STORE, 4, self.lock_key, self.revision_key, self.key, self.writers_key,
                        token, revision, json.dumps(jsonable_encoder(data), allow_nan=False), self.ttl,
                    )
                    if stored:
                        logger.info("Cache MISS filled: %s revision=%s", self.key, revision)
                        return CacheResult(data, "MISS")
                    # A write invalidated this calculation or the lease expired.
                    # Do not return/publish the old snapshot; read again.
                    logger.info("Discarded outdated cache calculation: %s", self.key)
            except RedisError:
                logger.warning("Redis unavailable: direct DB fallback for %s", self.key)
                return CacheResult(await self._compute(loader), "BYPASS")
            finally:
                if acquired:
                    try:
                        await self.redis.eval(_UNLOCK, 1, self.lock_key, token)
                    except RedisError:
                        pass  # Lease expires even if the owner cannot release it.
            if asyncio.get_running_loop().time() >= deadline:
                raise HTTPException(503, "Statistics are being prepared", headers={"Retry-After": "2"})
            await asyncio.sleep(self.POLL_SECONDS)

    async def get(self, loader: StatsLoader, background_tasks: BackgroundTasks) -> dict:
        # Compatibility with the existing service API/tests. No background refresh.
        return (await self.read(loader)).data

    async def invalidate(self) -> int:
        revision = await self.redis.eval(_INVALIDATE, 2, self.revision_key, self.key)
        logger.info("Cache INVALIDATE: %s revision=%s", self.key, revision)
        return int(revision)

    async def begin_write(self) -> str:
        """Register before DB writes. Fail closed if invalidation cannot be assured."""
        token = secrets.token_hex(16)
        await self.redis.eval(_BEGIN_WRITE, 3, self.revision_key, self.key, self.writers_key, token)
        return token

    async def end_write(self, token: str):
        # A failed release leaves a durable dirty marker: future reads bypass
        # cache instead of trusting an old snapshot. No unsafe expiring marker.
        await self.redis.eval(_END_WRITE, 3, self.revision_key, self.key, self.writers_key, token)
        logger.info("Cache INVALIDATE after mutation: %s", self.key)

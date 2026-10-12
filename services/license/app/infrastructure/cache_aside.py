"""Strict cache-aside for Lab 4. No process-local data, locks or stale reads."""
import asyncio
import json
import logging
import secrets
from dataclasses import dataclass

from fastapi import BackgroundTasks, HTTPException
from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

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
_STORE_UNPROTECTED = """
if (redis.call('GET', KEYS[1]) or '0') == ARGV[1]
   and redis.call('SCARD', KEYS[3]) == 0 then
    redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
    return 1
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
        self.coalesce = True
        self.ttl_jitter_seconds = 0
        self.generation_delay_ms = 0
        self.instrument = False

    @classmethod
    def from_url(cls, url, key=STATS_KEY, timeout_seconds=0.5):
        return cls(Redis.from_url(url, decode_responses=True,
                                  socket_connect_timeout=timeout_seconds, socket_timeout=timeout_seconds,
                                  retry=Retry(NoBackoff(), 0)), key)

    def expiration_seconds(self):
        jitter = max(0, min(self.ttl_jitter_seconds, self.ttl - 1))
        return self.ttl + secrets.randbelow(2 * jitter + 1) - jitter if jitter else self.ttl

    async def _compute(self, loader: StatsLoader):
        async def measured_loader():
            if self.instrument:
                try:
                    await self.redis.incr(f"{self.key}:computations")
                    await self.redis.expire(f"{self.key}:computations", 3600)
                except RedisError:
                    logger.warning("Lab computation counter unavailable")
            if self.generation_delay_ms:
                await asyncio.sleep(self.generation_delay_ms / 1000)
            return await loader()
        return await super()._compute(measured_loader)

    async def read(self, loader: StatsLoader) -> CacheResult:
        try:
            async with asyncio.timeout(self.GENERATION_TIMEOUT_SECONDS):
                return await self._read_loop(loader)
        except TimeoutError as exc:
            raise HTTPException(503, "Statistics request timed out", headers={"Retry-After": "2"}) from exc

    async def _read_loop(self, loader: StatsLoader) -> CacheResult:
        if not self.enabled:
            return CacheResult(await self._compute(loader), "BYPASS")
        deadline = asyncio.get_running_loop().time() + self.GENERATION_TIMEOUT_SECONDS
        while True:
            token = secrets.token_hex(16)
            acquired = False
            computed = None
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
                if not self.coalesce:
                    # Teaching-only unprotected mode keeps revision/write safety,
                    # but intentionally does not coordinate concurrent misses.
                    if acquired:
                        await self.redis.eval(_UNLOCK, 1, self.lock_key, token)
                        acquired = False
                    computed = await self._compute(loader)
                    stored = await self.redis.eval(
                        _STORE_UNPROTECTED, 3, self.revision_key, self.key, self.writers_key,
                        revision, json.dumps(jsonable_encoder(computed), allow_nan=False), self.expiration_seconds(),
                    )
                    if stored:
                        return CacheResult(computed, "MISS")
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
                    computed = data
                    stored = await self.redis.eval(
                        _STORE, 4, self.lock_key, self.revision_key, self.key, self.writers_key,
                        token, revision, json.dumps(jsonable_encoder(data), allow_nan=False), self.expiration_seconds(),
                    )
                    if stored:
                        logger.info("Cache MISS filled: %s revision=%s", self.key, revision)
                        return CacheResult(data, "MISS")
                    # A write invalidated this calculation or the lease expired.
                    # Do not return/publish the old snapshot; read again.
                    logger.info("Discarded outdated cache calculation: %s", self.key)
            except RedisError as exc:
                logger.warning("Redis unavailable (%s): direct DB fallback for %s", type(exc).__name__, self.key)
                return CacheResult(computed if computed is not None else await self._compute(loader), "BYPASS")
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

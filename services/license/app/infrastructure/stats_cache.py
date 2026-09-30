"""Shared Redis cache for public statistics.

Kept in each service's Docker build context; the contract tests exercise both copies.
"""
import asyncio
import json
import logging
import math
import secrets
import time
from collections.abc import Awaitable, Callable

from fastapi import BackgroundTasks, HTTPException
from fastapi.encoders import jsonable_encoder
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)
StatsLoader = Callable[[], Awaitable[dict]]

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_PUBLISH = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
    return 1
end
return 0
"""


class PublicStatsCache:
    FRESH_SECONDS = 300
    RETENTION_SECONDS = 86400
    LOCK_SECONDS = 120
    GENERATION_TIMEOUT_SECONDS = 90
    COLD_WAIT_SECONDS = 4
    POLL_SECONDS = 0.1

    def __init__(self, redis: Redis, key: str):
        self.redis = redis
        self.key = key
        self.lock_key = f"{key}:lock"

    @classmethod
    def from_url(cls, url: str, key: str):
        return cls(Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=0.5, socket_timeout=0.5,
            retry=Retry(NoBackoff(), 0),
        ), key)

    async def aclose(self):
        await self.redis.aclose()

    async def _read(self) -> dict | None:
        raw = await self.redis.get(self.key)
        if raw is None:
            return None
        try:
            entry = json.loads(raw)
            fresh_until = entry["fresh_until"]
            if (
                not isinstance(entry["data"], dict)
                or isinstance(fresh_until, bool)
                or not isinstance(fresh_until, (int, float))
                or not math.isfinite(fresh_until)
            ):
                raise ValueError("Invalid cache envelope")
            return entry
        except (ValueError, TypeError, KeyError):
            logger.warning("Ignoring malformed stats cache: %s", self.key)
            return None

    @staticmethod
    def _fresh(entry: dict) -> bool:
        return time.time() < entry["fresh_until"]

    async def get(self, loader: StatsLoader, background_tasks: BackgroundTasks) -> dict:
        deadline = asyncio.get_running_loop().time() + self.COLD_WAIT_SECONDS
        entry = None
        try:
            while True:
                entry = await self._read()
                if entry is not None and self._fresh(entry):
                    return entry["data"]

                token = secrets.token_hex(16)
                acquired = await self.redis.set(
                    self.lock_key, token, nx=True, ex=self.LOCK_SECONDS,
                )
                if acquired:
                    if entry is not None:
                        background_tasks.add_task(self._refresh, loader, token, True)
                        return entry["data"]
                    return await self._refresh(loader, token, False)

                if entry is not None:
                    return entry["data"]
                if asyncio.get_running_loop().time() >= deadline:
                    raise HTTPException(
                        status_code=503,
                        detail="Statistics are being prepared. Please retry shortly.",
                        headers={"Retry-After": "2"},
                    )
                await asyncio.sleep(self.POLL_SECONDS)
        except RedisError as exc:
            logger.warning("Stats cache unavailable (%s): %s", type(exc).__name__, self.key)
            if entry is not None:
                return entry["data"]
            # Cache outages must not prevent reading statistics from the primary DB.
            # This deliberately allows uncached DB reads while Redis is unavailable.
            return await self._compute(loader)

    async def _compute(self, loader: StatsLoader) -> dict:
        try:
            async with asyncio.timeout(self.GENERATION_TIMEOUT_SECONDS):
                return await loader()
        except TimeoutError as exc:
            raise HTTPException(
                status_code=503,
                detail="Statistics generation timed out. Please retry shortly.",
                headers={"Retry-After": "2"},
            ) from exc

    async def _refresh(self, loader: StatsLoader, token: str, background: bool) -> dict | None:
        started = time.monotonic()
        try:
            # Another worker may have published between our first GET and SET NX.
            try:
                entry = await self._read()
                if entry is not None and self._fresh(entry):
                    return entry["data"]
            except RedisError:
                pass

            stats = await self._compute(loader)
            encoded = json.dumps({
                "fresh_until": time.time() + self.FRESH_SECONDS,
                "data": jsonable_encoder(stats),
            }, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            try:
                # A worker whose lease expired must not overwrite a newer result.
                published = await self.redis.eval(
                    _PUBLISH, 2, self.lock_key, self.key,
                    token, encoded, self.RETENTION_SECONDS,
                )
                if published:
                    logger.info(
                        "Stats refreshed: %s, %.2fs, %d bytes",
                        self.key, time.monotonic() - started, len(encoded.encode("utf-8")),
                    )
                else:
                    logger.warning("Stats refresh lost its lock: %s", self.key)
            except RedisError as exc:
                logger.warning("Could not cache stats (%s): %s", type(exc).__name__, self.key)
            return stats
        except Exception:
            if not background:
                raise
            logger.exception("Background stats refresh failed: %s", self.key)
            return None
        finally:
            try:
                await self.redis.eval(_RELEASE, 1, self.lock_key, token)
            except RedisError as exc:
                # The lease still expires even if this worker cannot release it.
                logger.warning("Could not release stats lock (%s): %s", type(exc).__name__, self.key)


"""Lab 4 strict Cache-Aside contracts, isolated DB and shared fake Redis."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import fakeredis.aioredis
import jwt
import pytest
from redis.exceptions import ConnectionError

pytestmark = [pytest.mark.component("services/license"), pytest.mark.anyio]


@pytest.fixture
async def cache(component, monkeypatch):
    module = component("app.infrastructure.cache_aside")
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    instance = module.CacheAsideStats(redis)
    main = component("app.main")
    monkeypatch.setattr(main.app.state, "public_stats_cache", instance, raising=False)
    yield instance
    await instance.aclose()


async def test_hit_avoids_loader_and_instances_share_entry(cache, component):
    loader = AsyncMock(return_value={"total": 12})
    assert (await cache.read(loader)).source == "MISS"
    second = component("app.infrastructure.cache_aside").CacheAsideStats(cache.redis)
    assert (await second.read(loader)).source == "HIT"
    loader.assert_awaited_once()
    assert 0 < await cache.redis.ttl(cache.key) <= 300


async def test_expiration_is_real_ttl_no_stale_response(cache):
    await cache.redis.set(cache.key, json.dumps({"total": 1}), px=1)
    await asyncio.sleep(0.02)
    result = await cache.read(AsyncMock(return_value={"total": 2}))
    assert result.source == "MISS" and result.data == {"total": 2}


async def test_concurrent_cold_read_is_single_flight(cache):
    async def slow():
        await asyncio.sleep(0.02)
        return {"total": 10}
    loader = AsyncMock(side_effect=slow)
    results = await asyncio.gather(*(cache.read(loader) for _ in range(12)))
    assert all(r.data == {"total": 10} for r in results)
    assert sum(r.source == "MISS" for r in results) == 1
    loader.assert_awaited_once()


async def test_invalidation_discards_in_flight_snapshot(cache):
    started, resume = asyncio.Event(), asyncio.Event()
    calls = 0
    async def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await resume.wait()
            return {"total": 1}
        return {"total": 2}
    task = asyncio.create_task(cache.read(loader))
    await started.wait()
    await cache.invalidate()
    resume.set()
    assert (await task).data == {"total": 2}
    assert json.loads(await cache.redis.get(cache.key)) == {"total": 2}


async def test_write_marker_prevents_caching_and_survives_failed_cleanup(cache, monkeypatch):
    await cache.read(AsyncMock(return_value={"total": 1}))
    token = await cache.begin_write()
    assert await cache.redis.get(cache.key) is None
    assert (await cache.read(AsyncMock(return_value={"total": 2}))).source == "BYPASS"
    original = cache.redis.eval
    monkeypatch.setattr(cache.redis, "eval", AsyncMock(side_effect=ConnectionError()))
    with pytest.raises(ConnectionError):
        await cache.end_write(token)
    monkeypatch.setattr(cache.redis, "eval", original)
    assert (await cache.read(AsyncMock(return_value={"total": 3}))).source == "BYPASS"
    await cache.end_write(token)
    assert (await cache.read(AsyncMock(return_value={"total": 3}))).source == "MISS"


async def test_multiple_writers_do_not_clear_each_others_marker(cache):
    first, second = await cache.begin_write(), await cache.begin_write()
    await cache.end_write(first)
    assert (await cache.read(AsyncMock(return_value={}))).source == "BYPASS"
    await cache.end_write(second)
    assert (await cache.read(AsyncMock(return_value={}))).source == "MISS"


async def test_redis_down_fallback_and_disabled_mode(cache, monkeypatch):
    monkeypatch.setattr(cache.redis, "eval", AsyncMock(side_effect=ConnectionError()))
    loader = AsyncMock(return_value={"total": 42})
    assert (await cache.read(loader)).source == "BYPASS"
    cache.enabled = False
    assert (await cache.read(loader)).data == {"total": 42}


async def test_bad_entry_rebuilt_and_loader_error_releases_lock(cache):
    await cache.redis.set(cache.key, "not-json")
    assert (await cache.read(AsyncMock(return_value={"total": 1}))).source == "MISS"
    await cache.invalidate()
    with pytest.raises(ValueError):
        await cache.read(AsyncMock(side_effect=ValueError("DB failure")))
    assert await cache.redis.get(cache.lock_key) is None


async def test_lost_lease_cannot_publish_or_release_successor(cache):
    calls = 0
    async def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            await cache.redis.set(cache.lock_key, "successor", ex=120)
            await cache.redis.set(cache.key, json.dumps({"total": 99}), ex=300)
            return {"total": 1}
        raise AssertionError("Should reuse successor's result")
    assert (await cache.read(loader)).data == {"total": 99}
    assert await cache.redis.get(cache.lock_key) == "successor"


async def test_busy_cache_has_bounded_wait_and_cancelled_owner_releases(cache):
    from fastapi import HTTPException
    cache.GENERATION_TIMEOUT_SECONDS = 0.01
    await cache.redis.set(cache.lock_key, "another-owner", ex=120)
    loader = AsyncMock(return_value={})
    with pytest.raises(HTTPException) as caught:
        await cache.read(loader)
    assert caught.value.status_code == 503
    loader.assert_not_awaited()
    await cache.redis.delete(cache.lock_key)
    cache.GENERATION_TIMEOUT_SECONDS = 90
    started = asyncio.Event()
    async def wait():
        started.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(cache.read(wait))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await cache.redis.get(cache.lock_key) is None


async def test_stats_headers_mutation_and_fresh_read(api, cache, component, monkeypatch):
    service_module = component("app.application.service")
    loader = AsyncMock(return_value={"updated_at": "2026-10-13T00:00:00Z", "total": 1})
    monkeypatch.setattr(service_module.LicenseStatsService, "_load_stats", loader)
    url = "/api/v1/license/stats/public"
    assert (await api.get(url)).headers["X-Cache"] == "MISS"
    assert (await api.get(url)).headers["X-Cache"] == "HIT"
    settings = component("app.shared.config").settings
    token = jwt.encode({"sub": "1", "role": "ADMIN", "type": "access",
                        "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                       settings.JWT_SECRET, algorithm="HS256")
    response = await api.post("/api/v1/license/generate", headers={"Authorization": f"Bearer {token}"},
                              json={"amount": 3, "method": "Stars", "duration_days": 30,
                                    "max_devices": 2, "reset_limit": 0})
    assert response.status_code == 200, response.text
    assert response.headers["X-Cache-Invalidation"] == "INVALIDATED"
    loader.return_value = {"total": 2}
    fresh = await api.get(url)
    assert fresh.headers["X-Cache"] == "MISS" and fresh.json() == {"total": 2}


async def test_redis_down_rejects_mutation_before_db_change(api, cache, component, monkeypatch, db):
    from sqlalchemy import func, select
    model = component("app.infrastructure.repository").LicenseModel
    monkeypatch.setattr(cache.redis, "eval", AsyncMock(side_effect=ConnectionError()))
    response = await api.post("/api/v1/license/generate", json={})
    assert response.status_code == 503
    assert await db.scalar(select(func.count()).select_from(model)) == 0

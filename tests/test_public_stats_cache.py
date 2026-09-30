"""Run the same cache contract against both independent service build contexts."""
import asyncio
import importlib.util
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import fakeredis
import pytest
from fastapi import BackgroundTasks, HTTPException
from redis.exceptions import ConnectionError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["license", "usage"])
def cache_class(request):
    path = ROOT / "services" / request.param / "app/infrastructure/stats_cache.py"
    spec = importlib.util.spec_from_file_location(f"{request.param}_stats_cache", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PublicStatsCache


def run(scenario):
    asyncio.run(scenario())


async def seed(redis, key, data, fresh=True):
    await redis.set(key, json.dumps({
        "fresh_until": time.time() + (300 if fresh else -1),
        "data": data,
    }), ex=86400)


def test_fresh_shared_cache_avoids_database(cache_class):
    async def scenario():
        async with fakeredis.aioredis.FakeRedis() as redis:
            cache = cache_class(redis, "stats")
            await seed(redis, cache.key, {"updated_at": "old", "total": 12})
            loader = AsyncMock()
            assert await cache.get(loader, BackgroundTasks()) == {"updated_at": "old", "total": 12}
            loader.assert_not_awaited()
    run(scenario)


def test_stale_requests_schedule_one_refresh_across_clients(cache_class):
    async def scenario():
        server = fakeredis.FakeServer()
        clients = [fakeredis.aioredis.FakeRedis(server=server) for _ in range(5)]
        try:
            await seed(clients[0], "stats", {"total": 1}, fresh=False)
            caches = [cache_class(client, "stats") for client in clients]
            tasks = [BackgroundTasks() for _ in caches]
            loader = AsyncMock(return_value={"total": 2})
            result = await asyncio.gather(*(c.get(loader, t) for c, t in zip(caches, tasks)))
            assert result == [{"total": 1}] * 5
            loader.assert_not_awaited()
            assert sum(len(t.tasks) for t in tasks) == 1
            await asyncio.gather(*(t() for t in tasks))
            loader.assert_awaited_once()
            assert await caches[-1].get(loader, BackgroundTasks()) == {"total": 2}
            assert 86390 < await clients[0].ttl("stats") <= 86400
            assert not await clients[0].exists("stats:lock")
        finally:
            for client in clients:
                await client.aclose()
    run(scenario)


def test_cold_requests_share_single_computation(cache_class):
    async def scenario():
        server = fakeredis.FakeServer()
        clients = [fakeredis.aioredis.FakeRedis(server=server) for _ in range(5)]
        async def load():
            await asyncio.sleep(0.03)
            return {"total": 7}
        loader = AsyncMock(side_effect=load)
        try:
            results = await asyncio.gather(*(
                cache_class(client, "stats").get(loader, BackgroundTasks()) for client in clients
            ))
            assert results == [{"total": 7}] * 5
            loader.assert_awaited_once()
        finally:
            for client in clients:
                await client.aclose()
    run(scenario)


def test_busy_cold_cache_returns_bounded_retry_response(cache_class):
    async def scenario():
        async with fakeredis.aioredis.FakeRedis() as redis:
            cache = cache_class(redis, "stats")
            cache.COLD_WAIT_SECONDS = 0
            await redis.set(cache.lock_key, "another-worker", ex=120)
            loader = AsyncMock()
            with pytest.raises(HTTPException) as error:
                await cache.get(loader, BackgroundTasks())
            assert error.value.status_code == 503
            assert error.value.headers == {"Retry-After": "2"}
            loader.assert_not_awaited()
    run(scenario)


def test_failed_background_refresh_keeps_stale_and_releases_lock(cache_class):
    async def scenario():
        async with fakeredis.aioredis.FakeRedis() as redis:
            cache = cache_class(redis, "stats")
            await seed(redis, cache.key, {"total": 1}, fresh=False)
            tasks = BackgroundTasks()
            assert await cache.get(AsyncMock(side_effect=RuntimeError("DB unavailable")), tasks) == {"total": 1}
            await tasks()
            assert json.loads(await redis.get(cache.key))["data"] == {"total": 1}
            assert not await redis.exists(cache.lock_key)
    run(scenario)


def test_worker_that_lost_lease_cannot_overwrite_or_unlock_successor(cache_class):
    async def scenario():
        async with fakeredis.aioredis.FakeRedis() as redis:
            cache = cache_class(redis, "stats")
            async def load():
                # Simulate the lease expiring and another worker acquiring it.
                await redis.set(cache.lock_key, "successor", ex=120)
                await seed(redis, cache.key, {"total": 99})
                return {"total": 1}
            assert await cache.get(load, BackgroundTasks()) == {"total": 1}
            assert json.loads(await redis.get(cache.key))["data"] == {"total": 99}
            assert await redis.get(cache.lock_key) == b"successor"
    run(scenario)


def test_cache_publication_failure_returns_computed_data(cache_class):
    async def scenario():
        async with fakeredis.aioredis.FakeRedis() as redis:
            cache = cache_class(redis, "stats")
            redis.eval = AsyncMock(side_effect=ConnectionError("Redis unavailable"))
            loader = AsyncMock(return_value={"total": 3})
            assert await cache.get(loader, BackgroundTasks()) == {"total": 3}
            loader.assert_awaited_once()
    run(scenario)


def test_generation_timeout_releases_lock(cache_class):
    async def scenario():
        async with fakeredis.aioredis.FakeRedis() as redis:
            cache = cache_class(redis, "stats")
            cache.GENERATION_TIMEOUT_SECONDS = 0.01
            async def load():
                await asyncio.sleep(10)
            with pytest.raises(HTTPException) as error:
                await cache.get(load, BackgroundTasks())
            assert error.value.status_code == 503
            assert not await redis.exists(cache.lock_key)
    run(scenario)


def test_cancelled_generation_releases_lock(cache_class):
    async def scenario():
        async with fakeredis.aioredis.FakeRedis() as redis:
            cache = cache_class(redis, "stats")
            entered = asyncio.Event()
            async def load():
                entered.set()
                await asyncio.sleep(10)
            task = asyncio.create_task(cache.get(load, BackgroundTasks()))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not await redis.exists(cache.lock_key)
    run(scenario)


@pytest.mark.parametrize("raw", ["not-json", "null", '[]', '{"data": {}, "fresh_until": "wrong"}'])
def test_malformed_cache_is_rebuilt(cache_class, raw):
    async def scenario():
        async with fakeredis.aioredis.FakeRedis() as redis:
            await redis.set("stats", raw)
            cache = cache_class(redis, "stats")
            assert await cache.get(AsyncMock(return_value={"total": 4}), BackgroundTasks()) == {"total": 4}
            assert json.loads(await redis.get("stats"))["data"] == {"total": 4}
    run(scenario)


def test_real_client_connection_failure_falls_back_to_db(cache_class):
    async def scenario():
        async def close_connection(reader, writer):
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(close_connection, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        cache = cache_class.from_url(f"redis://127.0.0.1:{port}/0", "stats")
        try:
            loader = AsyncMock(return_value={"total": 42})
            assert await cache.get(loader, BackgroundTasks()) == {"total": 42}
            loader.assert_awaited_once()
        finally:
            await cache.aclose()
            server.close()
            await server.wait_closed()
    run(scenario)

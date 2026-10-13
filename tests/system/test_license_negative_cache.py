"""Negative markers never bypass auth or preserve stale absence after writes."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import fakeredis.aioredis
import jwt
import pytest
from redis.exceptions import ConnectionError

pytestmark = [pytest.mark.component('services/license'), pytest.mark.anyio]


@pytest.fixture
async def caches(component, monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    strict = component('app.infrastructure.cache_aside').CacheAsideStats(redis)
    negative = component('app.infrastructure.negative_cache').NegativeLicenseCache(redis)
    main = component('app.main')
    monkeypatch.setattr(main.app.state, 'public_stats_cache', strict, raising=False)
    monkeypatch.setattr(main.app.state, 'negative_license_cache', negative, raising=False)
    yield strict, negative
    await redis.aclose()


def admin(component):
    settings = component('app.shared.config').settings
    token = jwt.encode({'sub': '1', 'role': 'ADMIN', 'type': 'access',
                        'exp': datetime.now(timezone.utc) + timedelta(minutes=5)},
                       settings.JWT_SECRET, algorithm='HS256')
    return {'Authorization': 'Bearer ' + token}


async def test_absence_shared_and_positive_data_never_cached(caches, component):
    _, cache = caches
    loader = AsyncMock(return_value=None)
    assert await cache.read(999, loader) == (None, 'MISS')
    second = component('app.infrastructure.negative_cache').NegativeLicenseCache(cache.redis)
    assert await second.read(999, loader) == (None, 'HIT')
    loader.assert_awaited_once()
    assert 0 < await cache.redis.ttl(cache.key(999)) <= 15
    found = AsyncMock(return_value={'key': 'private'})
    assert (await cache.read(1, found))[1] == 'BYPASS'
    assert await cache.redis.get(cache.key(1)) is None


async def test_expiration_and_dirty_writers_bypass(caches):
    strict, cache = caches
    loader = AsyncMock(return_value=None)
    await cache.read(999, loader)
    await cache.redis.pexpire(cache.key(999), 1)
    await asyncio.sleep(.02)
    assert (await cache.read(999, loader))[1] == 'MISS'
    token = await strict.begin_write()
    assert (await cache.read(999, loader))[1] == 'BYPASS'
    await strict.end_write(token)
    assert (await cache.read(999, loader))[1] == 'MISS'


async def test_revision_fences_inflight_absence(caches):
    strict, cache = caches
    calls = 0
    async def lookup():
        nonlocal calls
        calls += 1
        if calls == 1:
            token = await strict.begin_write()
            await strict.end_write(token)
            return None
        return {'id': 99}
    assert await cache.read(99, lookup) == ({'id': 99}, 'BYPASS')
    assert await cache.redis.get(cache.key(99)) is None


async def test_redis_failure_disabled_mode_and_database_errors(caches, monkeypatch):
    _, cache = caches
    monkeypatch.setattr(cache.redis, 'eval', AsyncMock(side_effect=ConnectionError()))
    assert await cache.read(99, AsyncMock(return_value=None)) == (None, 'BYPASS')
    cache.enabled = False
    assert await cache.read(99, AsyncMock(return_value={'id': 99})) == ({'id': 99}, 'BYPASS')
    cache.enabled = True
    with pytest.raises(RuntimeError, match='database'):
        await cache.read(99, AsyncMock(side_effect=RuntimeError('database unavailable')))


async def test_store_failure_does_not_repeat_sql(caches, monkeypatch):
    _, cache = caches
    original = cache.redis.eval
    async def fail_store(script, *args):
        if "'SET'" in script:
            raise ConnectionError()
        return await original(script, *args)
    monkeypatch.setattr(cache.redis, 'eval', fail_store)
    loader = AsyncMock(return_value=None)
    assert await cache.read(99, loader) == (None, 'BYPASS')
    loader.assert_awaited_once()


async def test_actual_http_404_auth_then_creation_invalidates(api, caches, component):
    _, cache = caches
    headers = admin(component)
    path = '/api/v1/license/1'
    missing = await api.get(path, headers=headers)
    assert missing.status_code == 404 and missing.headers['X-Negative-Cache'] == 'MISS'
    repeated = await api.get(path, headers=headers)
    assert repeated.status_code == 404 and repeated.headers['X-Negative-Cache'] == 'HIT'
    assert repeated.json()['error_code'] == missing.json()['error_code'] == 'NOT_FOUND'
    unauthorized = await api.get(path)
    assert unauthorized.status_code in (401, 403)
    assert 'X-Negative-Cache' not in unauthorized.headers
    created = await api.post('/api/v1/license/generate', headers=headers,
                             json={'amount': 3, 'method': 'Stars', 'duration_days': 30,
                                   'max_devices': 2, 'reset_limit': 0})
    assert created.status_code == 200, created.text
    found = await api.get(path, headers=headers)
    assert found.status_code == 200 and found.headers['X-Negative-Cache'] == 'BYPASS'
    # Physical marker may remain until TTL, but its revision is no longer accepted.
    assert await cache.redis.get(cache.key(1)) is not None


async def test_lab_routes_disabled_by_default(api):
    assert (await api.get('/api/v1/lab8/aggregate/0')).status_code == 404


async def test_lookup_id_range_is_bounded_before_sql(api, caches, component):
    for identifier in (0, -1, 2147483648):
        response = await api.get(f'/api/v1/license/{identifier}', headers=admin(component))
        assert response.status_code == 422


async def test_negative_mode_requires_mutation_fencing(component, monkeypatch):
    main = component('app.main')
    monkeypatch.setattr(main.settings, 'NEGATIVE_CACHE_ENABLED', True)
    monkeypatch.setattr(main.settings, 'STATS_CACHE_ASIDE', False)
    with pytest.raises(RuntimeError, match='mutation fencing'):
        async with main.lifespan(main.app):
            pass

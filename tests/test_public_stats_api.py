"""HTTP contract and cache reuse with real repositories on an isolated SQLite DB."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = """
import asyncio
import importlib
import os
from unittest.mock import AsyncMock, patch

import fakeredis
from fastapi.testclient import TestClient

from app.infrastructure.stats_cache import PublicStatsCache

service = os.environ["STATS_TEST_SERVICE"]
main = importlib.import_module("app.main")
application = importlib.import_module("app.application.service")
stats_class = getattr(application, "LicenseStatsService" if service == "license" else "UsageStatsService")
async def idle_worker():
    await asyncio.Event().wait()
if service == "license":
    main.check_expired_licenses_task = idle_worker

server = fakeredis.FakeServer()
key = f"mtgmods:{service}:public_stats:v1"
loader = AsyncMock(wraps=stats_class._load_stats)
with patch.object(stats_class, "_load_stats", loader):
    for boot in range(2):
        with TestClient(main.app) as client:
            assert main.app.state.public_stats_cache.key == key
            main.app.state.public_stats_cache = PublicStatsCache(
                fakeredis.aioredis.FakeRedis(server=server), key,
            )
            response = client.get(f"/api/v1/{service}/stats/public")
            assert response.status_code == 200, response.text
            body = response.json()
            assert "updated_at" in body
            assert "status" not in body and "data" not in body
            if service == "license":
                assert "subscriptions" in body and "forever" in body
            else:
                assert "overview" in body and "analytics" in body
            if boot == 0:
                first = body
            assert body == first
            assert client.get("/health").status_code == 200
            loader.assert_awaited_once()
"""


@pytest.mark.parametrize("service", ["license", "usage"])
def test_stats_api_and_cache_reuse_after_application_restart(service, tmp_path):
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(ROOT / "services" / service),
        "STATS_TEST_SERVICE": service,
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "DATABASE_POSTGRES_URL": "sqlite+aiosqlite:///:memory:",
        "DEBUG_MODE": "True",
        "API_VERSION": "v1",
        "APP_VERSION": "test",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "RABBITMQ_URL": "amqp://guest:guest@127.0.0.1/",
        "JWT_SECRET": "test-only-secret",
        "BOT_SECRET_TOKEN": "test-only-token",
        "INTERNAL_SECRET_TOKEN": "test-only-internal-token",
        "USER_SERVICE_URL": "http://127.0.0.1:1",
    })
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

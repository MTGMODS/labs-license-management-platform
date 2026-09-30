"""Each component owns an `app` package; never import two into the same namespace.

Tests use synthetic configuration, temporary working directories and databases.
No production .env files are loaded and outbound network connections are blocked.
"""
import importlib
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", autouse=True)
def component(request, tmp_path_factory):
    marker = request.node.get_closest_marker("component")
    assert marker, "System test modules must declare their component"
    workdir = tmp_path_factory.mktemp(marker.args[0].replace("/", "_"))
    patch = pytest.MonkeyPatch()
    # Clear imported `app` packages before and after switching components.
    saved = {k: v for k, v in sys.modules.items() if k == "app" or k.startswith("app.")}
    for name in saved:
        del sys.modules[name]
    patch.syspath_prepend(str(ROOT / marker.args[0]))
    patch.chdir(workdir)
    values = {
        "DATABASE_URL": f"sqlite+aiosqlite:///{workdir.as_posix()}/test.db",
        "DATABASE_POSTGRES_URL": "sqlite+aiosqlite:///:memory:",
        "DEBUG_MODE": "True", "API_VERSION": "v1", "APP_VERSION": "test",
        "JWT_SECRET": "test-only-jwt-secret-at-least-32-characters",
        "BOT_SECRET_TOKEN": "test-bot-secret", "INTERNAL_SECRET_TOKEN": "test-internal-secret",
        "REDIS_URL": "redis://127.0.0.1:1/0", "RABBITMQ_URL": "amqp://guest:guest@127.0.0.1:1/",
        "USER_SERVICE_URL": "http://127.0.0.1:1", "FRONTEND_URL": "https://web.invalid",
        "DOWNLOAD_BASE_URL": "https://files.invalid/api/v1/files/downloads/vip",
        "DISCORD_CLIENT_ID": "123", "DISCORD_CLIENT_SECRET": "test",
        "DISCORD_REDIRECT_URI": "https://web.invalid/callback",
        "TELEGRAM_CLIENT_ID": "123", "TELEGRAM_CLIENT_SECRET": "test",
        "TELEGRAM_CALLBACK_URL": "https://web.invalid/callback",
        "TELEGRAM_BOT_TOKEN": "123:test-token", "TELEGRAM_VIP_CHAT_ID": "-100123",
        "DISCORD_BOT_TOKEN": "test-token", "DISCORD_GUILD_ID": "123",
        "VIP_ROLE_ID": "456", "VIP_CHANNEL_ID": "789", "CHAT_CHANNEL_ID": "987",
        "WEB_APP_URL": "https://web.invalid", "BACKEND_API_URL": "http://127.0.0.1:1/check/info",
        "API_ENDPOINT_URL": "http://127.0.0.1:1/check/info", "PYTHON_DOTENV_DISABLED": "1",
    }
    for key, value in values.items():
        patch.setenv(key, value)
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"External network forbidden in tests: {address}")
        return original_connect(sock, address)

    def local_only_ex(sock, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"External network forbidden in tests: {address}")
        return original_connect_ex(sock, address)

    patch.setattr(socket.socket, "connect", local_only)
    patch.setattr(socket.socket, "connect_ex", local_only_ex)
    try:
        yield importlib.import_module
    finally:
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        sys.modules.update(saved)
        patch.undo()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db(component):
    database = component("app.shared.database")
    component("app.infrastructure.repository")
    async with database.engine.begin() as connection:
        await connection.run_sync(database.Base.metadata.drop_all)
        await connection.run_sync(database.Base.metadata.create_all)
    try:
        async with database.AsyncSessionLocal() as session:
            yield session
    finally:
        await database.engine.dispose()


@pytest.fixture
async def api(component, db):
    """Actual routers/handlers, no background workers or external startup effects."""
    import httpx
    main = component("app.main")
    database = component("app.shared.database")

    async def test_db():
        yield db

    main.app.dependency_overrides[database.get_db] = test_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            yield client
    finally:
        main.app.dependency_overrides.clear()

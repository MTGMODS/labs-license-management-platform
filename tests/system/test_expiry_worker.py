import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.component("services/license"), pytest.mark.anyio, pytest.mark.unit]


@pytest.mark.parametrize("socials,expected", [
    ({"telegram_id": 123, "discord_id": 456}, ["discord.remove_vip_role", "discord.send_message", "telegram.kick_from_vip_chat", "telegram.send_message"]),
    ({"telegram_id": 123}, ["telegram.kick_from_vip_chat", "telegram.send_message"]),
    ({"discord_id": 456}, ["discord.remove_vip_role", "discord.send_message"]),
    (None, []),
])
async def test_expiry_publishes_commands_after_commit(component, monkeypatch, socials, expected):
    worker = component("app.application.worker")
    db = AsyncMock()
    session = MagicMock()
    session.return_value.__aenter__ = AsyncMock(return_value=db)
    session.return_value.__aexit__ = AsyncMock(return_value=False)
    repo = SimpleNamespace(deactivate_expired_licenses=AsyncMock(return_value=[{"user_id": 42}, {"user_id": None}]))
    client = SimpleNamespace(get_social_ids_by_user_id=AsyncMock(return_value=socials))
    async def publish(**kwargs):
        db.commit.assert_awaited_once()
    publisher = AsyncMock(side_effect=publish)
    monkeypatch.setattr(worker, "AsyncSessionLocal", session)
    monkeypatch.setattr(worker, "LicenseRepository", lambda db: repo)
    monkeypatch.setattr(worker, "UserServiceClient", lambda: client)
    monkeypatch.setattr(worker, "publish_bot_command", publisher)
    monkeypatch.setattr(worker.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await worker.check_expired_licenses_task()
    assert [call.kwargs["routing_key"] for call in publisher.await_args_list] == expected
    client.get_social_ids_by_user_id.assert_awaited_once_with(42)
    for call in publisher.await_args_list:
        payload = call.kwargs["payload"]
        if call.kwargs["routing_key"].startswith("telegram"):
            assert payload["telegram_id"] == 123
        else:
            assert payload["discord_id"] == 456

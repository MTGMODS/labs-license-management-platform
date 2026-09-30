import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.component("bots/discord"), pytest.mark.anyio, pytest.mark.unit]


@pytest.fixture
def handlers(component, monkeypatch):
    module = component("app.handlers.vip")
    commands = {}
    def command(**kwargs):
        def register(callback):
            commands[kwargs["name"]] = callback
            return callback
        return register
    bot = MagicMock()
    bot.tree.command = command
    module.register_vip_handlers(bot)
    check = AsyncMock(return_value={"is_vip": True, "license": {"expires_at": None, "purchased_at": "2026-01-01T00:00:00Z"}})
    monkeypatch.setattr(module.api_client, "check_vip_status", check)
    interaction = SimpleNamespace(user=SimpleNamespace(id=123), response=AsyncMock(), followup=AsyncMock())
    return commands, bot, check, interaction


async def test_vip_is_public_with_local_discord_timestamp(handlers):
    commands, _, _, interaction = handlers
    await commands["vip"](interaction)
    interaction.response.defer.assert_awaited_once_with()
    text = interaction.followup.send.await_args.kwargs["embed"].description
    assert "<t:1767225600:f>" in text and "FOREVER" in text


@pytest.mark.parametrize("response", [{"is_vip": False}, {"error": "unavailable"}])
async def test_no_vip_or_api_failure_never_grants_role(handlers, response):
    commands, bot, check, interaction = handlers
    check.return_value = response
    await commands["role"](interaction)
    bot.get_guild.assert_not_called()
    interaction.followup.send.assert_awaited_once()


@pytest.mark.parametrize("has_role", [False, True])
async def test_role_granted_only_when_missing(handlers, has_role):
    commands, bot, _, interaction = handlers
    role = SimpleNamespace(mention="@VIP")
    member = SimpleNamespace(roles=[role] if has_role else [], add_roles=AsyncMock(), display_avatar=None, mention="@user")
    guild = bot.get_guild.return_value
    guild.get_member.return_value = member
    guild.get_role.return_value = role
    bot.get_channel.return_value = None
    await commands["role"](interaction)
    if has_role:
        member.add_roles.assert_not_awaited()
    else:
        member.add_roles.assert_awaited_once_with(role)


@pytest.mark.parametrize("value,expected", [(None, "—"), ("broken", "broken"), ("2026-01-01T03:00:00+03:00", "<t:1767225600:f>")])
async def test_datetime_format(component, value, expected):
    assert component("app.formatting").format_discord_datetime(value) == expected


@pytest.mark.parametrize("cached", [True, False])
async def test_rabbit_role_removal_and_direct_message(component, monkeypatch, cached):
    module = component("app.rabbitmq")
    queue, channel, connection = AsyncMock(), AsyncMock(), AsyncMock()
    connection.channel.return_value = channel
    channel.declare_queue.return_value = queue
    monkeypatch.setattr(module.aio_pika, "connect_robust", AsyncMock(return_value=connection))
    role = object()
    member = SimpleNamespace(roles=[role], remove_roles=AsyncMock())
    guild = MagicMock()
    guild.get_member.return_value = member if cached else None
    guild.fetch_member = AsyncMock(return_value=member)
    guild.get_role.return_value = role
    bot = SimpleNamespace(wait_until_ready=AsyncMock(), get_guild=MagicMock(return_value=guild), fetch_user=AsyncMock())
    assert await module.start_rabbitmq_consumer(bot) is connection
    queue.bind.assert_awaited_once_with(channel.declare_exchange.return_value, routing_key="discord.#")
    callback = queue.consume.await_args.args[0]
    for route, payload in [("discord.remove_vip_role", {"discord_id": 123, "reason": "vip_expired"}), ("discord.send_message", {"discord_id": 123, "text": "test"}), ("discord.remove_vip_role", {})]:
        message = MagicMock(body=json.dumps(payload).encode(), routing_key=route)
        message.process.return_value.__aexit__.return_value = False
        await callback(message)
    member.remove_roles.assert_awaited_once_with(role, reason="vip_expired")
    bot.fetch_user.assert_awaited_once_with(123)
    bot.fetch_user.return_value.send.assert_awaited_once_with("test")
    if not cached:
        guild.fetch_member.assert_awaited_once_with(123)

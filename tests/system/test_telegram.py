import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.component("bots/telegram"), pytest.mark.anyio, pytest.mark.unit]

PLAN = {"duration_days": 30, "telegram_stars_price": 250, "price": 5, "max_devices": 2, "reset_limit": 1}
INVOICE = {"duration": 30, "price": 5, "max_devices": 2, "reset_limit": 1}


@pytest.fixture
def payments(component, monkeypatch):
    module = component("app.handlers.payments")
    monkeypatch.setattr(module.api_client, "get_tariffs", AsyncMock(return_value={"status": "success", "data": {"plans": [PLAN]}}))
    monkeypatch.setattr(module.api_client, "generate_license", AsyncMock(return_value={"status": "success", "data": {"key": "AAAA-BBBB-CCCC-DDDD"}}))
    return module


@pytest.mark.parametrize("change", [None, {"price": 10}, {"duration": 90}, {"max_devices": 5}, {"reset_limit": 2}])
async def test_precheckout_rechecks_current_tariff(payments, change):
    query = SimpleNamespace(invoice_payload=json.dumps({**INVOICE, **(change or {})}), total_amount=250, answer=AsyncMock())
    await payments.precheckout_handler(SimpleNamespace(pre_checkout_query=query), None)
    assert query.answer.await_args.kwargs["ok"] is (change is None)
    payments.api_client.get_tariffs.assert_awaited_once()
    payments.api_client.generate_license.assert_not_awaited()


@pytest.mark.parametrize("failure", ["backend", "empty", "bad-json", "amount"])
async def test_precheckout_rejects_unavailable_or_invalid_payment(payments, failure):
    if failure == "backend":
        payments.api_client.get_tariffs.return_value = {"error": "unavailable"}
    elif failure == "empty":
        payments.api_client.get_tariffs.return_value = {"status": "success", "data": {"plans": []}}
    query = SimpleNamespace(invoice_payload="invalid" if failure == "bad-json" else json.dumps(INVOICE), total_amount=1 if failure == "amount" else 250, answer=AsyncMock())
    await payments.precheckout_handler(SimpleNamespace(pre_checkout_query=query), None)
    assert query.answer.await_args.kwargs["ok"] is False


async def test_invoice_uses_stars_and_current_plan(payments):
    query = SimpleNamespace(data="buy_30", answer=AsyncMock(), edit_message_text=AsyncMock())
    context = SimpleNamespace(bot=AsyncMock())
    await payments.pay_callback(SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123)), context)
    args = context.bot.send_invoice.await_args.kwargs
    assert args["chat_id"] == 123 and args["currency"] == "XTR"
    assert args["prices"][0].amount == 250 and json.loads(args["payload"]) == INVOICE


@pytest.mark.parametrize("success", [True, False])
async def test_successful_payment_generates_key_or_reports_failure(payments, success):
    if not success:
        payments.api_client.generate_license.return_value = {"error": "unavailable"}
    message = SimpleNamespace(successful_payment=SimpleNamespace(invoice_payload=json.dumps(INVOICE)), reply_text=AsyncMock())
    await payments.successful_payment_handler(SimpleNamespace(message=message), None)
    payments.api_client.generate_license.assert_awaited_once_with(duration_days=30, amount=5, max_devices=2, reset_limit=1)
    assert ("AAAA-BBBB-CCCC-DDDD" in message.reply_text.await_args.args[0]) is success


@pytest.mark.parametrize("vip", [True, False])
async def test_join_request_approves_only_vip(component, monkeypatch, vip):
    module = component("app.handlers.vip_chat")
    monkeypatch.setattr(module.api_client, "check_vip_status", AsyncMock(return_value={"is_vip": vip, "license": {"expires_at": None, "purchase_price": 5, "purchase_method": "Stars"}}))
    member = SimpleNamespace(id=123, mention_html=lambda: "Tester")
    update = SimpleNamespace(chat_join_request=SimpleNamespace(chat=SimpleNamespace(id=module.TELEGRAM_VIP_CHAT_ID), from_user=member))
    bot = AsyncMock()
    await module.handle_join_request(update, SimpleNamespace(bot=bot))
    if vip:
        bot.approve_chat_join_request.assert_awaited_once_with(module.TELEGRAM_VIP_CHAT_ID, 123)
        bot.decline_chat_join_request.assert_not_awaited()
        assert "FOREVER" in bot.send_message.await_args.kwargs["text"]
    else:
        bot.decline_chat_join_request.assert_awaited_once_with(module.TELEGRAM_VIP_CHAT_ID, 123)
        bot.approve_chat_join_request.assert_not_awaited()


async def test_unrelated_chat_request_is_ignored(component, monkeypatch):
    module = component("app.handlers.vip_chat")
    check = AsyncMock()
    monkeypatch.setattr(module.api_client, "check_vip_status", check)
    update = SimpleNamespace(chat_join_request=SimpleNamespace(chat=SimpleNamespace(id=0), from_user=SimpleNamespace(id=123)))
    await module.handle_join_request(update, SimpleNamespace(bot=AsyncMock()))
    check.assert_not_awaited()


@pytest.mark.parametrize("value", [None, "FOREVER", ""])
async def test_forever_format(component, value):
    assert component("app.formatting").format_vip_access(value) == "FOREVER"


async def test_rabbit_consumer_dispatches_message_and_soft_kick(component, monkeypatch):
    module = component("app.rabbitmq")
    queue, channel, connection, bot = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
    connection.channel.return_value = channel
    channel.declare_queue.return_value = queue
    monkeypatch.setattr(module.aio_pika, "connect_robust", AsyncMock(return_value=connection))
    assert await module.start_rabbitmq_consumer(bot) is connection
    queue.bind.assert_awaited_once_with(channel.declare_exchange.return_value, routing_key="telegram.#")
    callback = queue.consume.await_args.args[0]
    for route, payload in [("telegram.send_message", {"telegram_id": 123, "text": "test"}), ("telegram.kick_from_vip_chat", {"telegram_id": 123}), ("telegram.kick_from_vip_chat", {})]:
        message = MagicMock(body=json.dumps(payload).encode(), routing_key=route)
        message.process.return_value.__aexit__.return_value = False
        await callback(message)
    bot.send_message.assert_awaited_once_with(chat_id=123, text="test", parse_mode="HTML")
    bot.ban_chat_member.assert_awaited_once_with(chat_id=module.TELEGRAM_VIP_CHAT_ID, user_id=123)
    bot.unban_chat_member.assert_awaited_once_with(chat_id=module.TELEGRAM_VIP_CHAT_ID, user_id=123)
    assert [call[0] for call in bot.mock_calls] == ["send_message", "ban_chat_member", "unban_chat_member"]

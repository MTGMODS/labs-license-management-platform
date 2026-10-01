from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ContextTypes

from app.api import api_client
from app.config import TELEGRAM_VIP_CHAT_ID, WEB_APP_URL
from app.formatting import format_datetime, format_price, format_vip_access


async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = update.chat_join_request
    chat_id = request.chat.id
    telegram_id = request.from_user.id

    if chat_id != TELEGRAM_VIP_CHAT_ID:
        return

    vip_data = await api_client.check_vip_status(telegram_id)

    if vip_data.get("is_vip"):
        await context.bot.approve_chat_join_request(chat_id, telegram_id)
        await context.bot.send_message(chat_id=telegram_id, text="✅ Вы добавлены в VIP чат ✅")

        license_info = vip_data.get("license")
        vip_access = format_vip_access(license_info.get("expires_at"))
        price = format_price(license_info.get("purchase_price"))
        method = license_info.get("purchase_method")

        welcome_text = (
            f"👋 <b>{request.from_user.mention_html()} ({telegram_id}), добро пожаловать!</b>\n\n"
            f"🔒 <b>Доступ к VIP:</b> {vip_access}\n"
            f"ℹ️ <b>Оплата:</b> ${price} через {method}"
        )
        await context.bot.send_message(chat_id=chat_id, text=welcome_text, parse_mode="HTML")
    else:
        await context.bot.decline_chat_join_request(chat_id, telegram_id)
        decline_text = (
            "❌ <b>У вас нет активного VIP.</b>\n\n"
            "Чтобы приобрести VIP, перейдите на сайт хелпера по кнопке ниже или используйте команду /pay"
        )
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("Открыть VIP кабинет", web_app=WebAppInfo(url=WEB_APP_URL))
        ]])
        await context.bot.send_message(chat_id=telegram_id, text=decline_text, parse_mode="HTML", reply_markup=markup)


async def vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id

    vip_data = await api_client.check_vip_status(telegram_id)

    if vip_data.get("error"):
        await update.message.reply_text("⚠️ <b>Не удалось проверить VIP. Попробуйте позже.</b>", parse_mode="HTML")
        return

    if not vip_data.get("is_vip"):
        await update.message.reply_text("❌ <b>У вас нет активного VIP.</b>", parse_mode="HTML")
        return

    license_info = vip_data.get("license", {})
    activated_at = format_datetime(license_info.get("activated_at"))
    vip_access = format_vip_access(license_info.get("expires_at"))
    method = license_info.get("purchase_method")
    price = format_price(license_info.get("purchase_price"))

    text = (
        f"📅 <b>Активация VIP:</b> {activated_at}\n"
        f"🔒 <b>Доступ к VIP:</b> {vip_access}\n"
        f"ℹ️ <b>Оплата:</b> ${price} через {method}"
    )

    await update.message.reply_text(text, parse_mode="HTML")

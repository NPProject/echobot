import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Union

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher.filters import Command
from aiogram.types import ParseMode, BotCommand
from aiogram.utils.exceptions import BotBlocked, UserDeactivated
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pydantic import BaseModel

from config import API_TOKEN, admin_id, mongourl

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

mongo_client = AsyncIOMotorClient(mongourl)
db: AsyncIOMotorDatabase = mongo_client['bot_db']


commands = {"start": "Старт",
            "settings": "Настройки",
            "del": "Удалить сообщение"}


class User(BaseModel):
    user_id: int
    admin: bool = False
    vip: bool = False


async def get_user(user_id: int) -> Union[User, None]:
    user_data = await db.users.find_one({"user_id": user_id})
    if user_data:
        return User(id=user_data["user_id"], admin=user_data.get("admin"),
                    vip=user_data.get("vip"))
    return None


async def save_user(user: User, admin: bool = False, vip: bool = False) -> None:
    await db.users.update_one({"user_id": user.user_id}, {
        "$set": {"user_id": user.user_id, "admin": admin, "vip": vip}}, upsert=True)


@dp.message_handler(Command('admin'))
async def admin_handler(message: types.Message):
    if message.from_user.id == admin_id:
        await save_user(User(user_id=int(message.get_args())), admin=True)
        await message.reply('Готово!')
    else:
        await message.reply('У вас нет прав для выполнения этой команды.')


@dp.message_handler(Command('vip'))
async def vip_handler(message: types.Message):
    if message.from_user.id == admin_id:
        await save_user(User(user_id=int(message.get_args())), vip=True)
        await message.reply('Готово!')
    else:
        await message.reply('У вас нет прав для выполнения этой команды.')


async def is_admin(user_id: int) -> bool:
    user_data = await db.users.find_one({"user_id": user_id})
    return user_data["admin"] if user_data else False


async def is_vip(user_id: int) -> bool:
    user_data = await db.users.find_one({"user_id": user_id})
    return user_data["vip"] if user_data else False


async def get_settings(user_id: int) -> dict:
    settings = await db.settings.find_one({"user_id": user_id})
    if settings is None:
        default_settings = {
            "user_id": user_id,
            "show_nickname_inline": False,
        }
        await db.settings.insert_one(default_settings)
        return default_settings
    return settings


async def update_settings(user_id: int, field: str, value: bool) -> None:
    await db.settings.update_one({"user_id": user_id}, {"$set": {field: value}})


async def cooldown_check(user_id: int) -> bool:
    last_message = await db.cooldown.find_one({"user_id": user_id})
    if last_message:
        if (datetime.now() - last_message["sent_at"]).total_seconds() < 60:
            return False
    return True


async def update_cooldown(user_id: int) -> None:
    await db.cooldown.update_one({"user_id": user_id}, {"$set": {"sent_at": datetime.now()}}, upsert=True)


@dp.message_handler(Command('start'))
async def start_handler(message: types.Message):
    if not await db.users.find_one({"user_id": message.from_user.id}):
        await save_user(User(user_id=message.from_user.id))
    await message.reply('Привет! Добро пожаловать в наш бот.')


@dp.message_handler(Command('settings'))
async def settings_handler(message: types.Message):
    settings = await get_settings(message.from_user.id)
    inline_btn_show_nickname_inline = types.InlineKeyboardButton(
        'Показывать никнейм: ' + ("✅" if settings["show_nickname_inline"] else "❌"),
        callback_data="toggle_show_nickname_inline"
    )
    reply_markup = types.InlineKeyboardMarkup().add(inline_btn_show_nickname_inline)

    await message.reply('Настройки отображения:', reply_markup=reply_markup)


@dp.callback_query_handler(lambda query: query.data in ["toggle_show_nickname_inline"])
async def settings_callback_handler(query: types.CallbackQuery):
    setting_to_toggle = query.data[7:]
    current_setting = (await get_settings(query.from_user.id))[setting_to_toggle]
    await update_settings(query.from_user.id, setting_to_toggle, not current_setting)

    settings = await get_settings(query.from_user.id)
    inline_btn_show_nickname_inline = types.InlineKeyboardButton(
        'Показывать никнейм: ' + ("✅" if settings["show_nickname_inline"] else "❌"),
        callback_data="toggle_show_nickname_inline"
    )
    reply_markup = types.InlineKeyboardMarkup().add(inline_btn_show_nickname_inline)

    await query.message.edit_text('Настройки отображения:', reply_markup=reply_markup)
    await query.answer('Настройка обновлена.')


async def save_sent_message(sender_id: int, sender_message_id: int, receiver_id: int, receiver_message_id: int,
                            original_message_id: int) -> None:
    await db.sent_messages.insert_one(
        {"sender_id": sender_id, "sender_message_id": sender_message_id, "receiver_id": receiver_id,
         "receiver_message_id": receiver_message_id, "original_message_id": original_message_id})


async def get_sent_messages(sender_id: int, sender_message_id: int) -> list:
    return await db.sent_messages.find({"sender_id": sender_id, "original_message_id": sender_message_id}
                                       ).to_list(length=10000)


async def get_sent_message_id(sender_message_id: int = None, original_message_id: int = None,
                              receiver_id: int = None):
    if sender_message_id:
        if receiver_id:
            sent_message = await db.sent_messages.find_one(
                {"sender_message_id": sender_message_id, "receiver_id": receiver_id}
            )
            if sent_message:
                return sent_message["receiver_message_id"]
        else:
            sent_message = await db.sent_messages.find_one(
                {"sender_message_id": sender_message_id}
            )
            if sent_message:
                return sent_message
    elif original_message_id:
        if receiver_id:
            sent_message = await db.sent_messages.find_one(
                {"receiver_id": receiver_id, "original_message_id": original_message_id}
            )
            if sent_message:
                return sent_message["receiver_message_id"]
        else:
            sent_message = await db.sent_messages.find_one(
                {"original_message_id": original_message_id}
            )
            if sent_message:
                return sent_message


async def delete_sent_messages(sender_id: int, sender_message_id: int) -> None:
    await db.sent_messages.delete_many({"sender_id": sender_id, "sender_message_id": sender_message_id})


async def send_msg(message: types.Message, user, reply_markup):
    try:
        if message.reply_to_message:
            if message.reply_to_message.from_user.is_bot:
                reply_to_message_id = await get_sent_message_id(
                    sender_message_id=message.reply_to_message.message_id,
                    receiver_id=user["user_id"]
                )
            else:
                reply_to_message_id = await get_sent_message_id(
                    original_message_id=message.reply_to_message.message_id,
                    receiver_id=user["user_id"]
                )
        else:
            reply_to_message_id = None
        sent_message = await bot.copy_message(chat_id=user["user_id"], from_chat_id=message.from_user.id,
                                              message_id=message.message_id, reply_markup=reply_markup,
                                              reply_to_message_id=reply_to_message_id)
        await save_sent_message(message.from_user.id, sent_message.message_id, user["user_id"],
                                sent_message.message_id, message.message_id)
    except BotBlocked:
        await db.users.delete_one(dict(user_id=user["user_id"]))
    except UserDeactivated:
        await db.users.delete_one(dict(user_id=user["user_id"]))
    except Exception as e:
        logging.exception(e)


async def broadcast_message(message: types.Message) -> None:
    users = db.users.find()
    user = User(**(await db.users.find_one({"user_id": message.from_user.id})))
    settings = await get_settings(user.user_id)
    show_nickname_inline = settings["show_nickname_inline"]
    reply_markup = None
    if show_nickname_inline:
        nickname = message.from_user.username if message.from_user.username else "id" + str(message.from_user.id)
        inline_btn_nickname = types.InlineKeyboardButton(
            ('VIP' if user.vip else '') + ('ADMIN' if user.admin else '') + ' ' + nickname,
            callback_data="user"
        )
        reply_markup = types.InlineKeyboardMarkup().add(inline_btn_nickname)
    time_start = time.time()
    cor = [asyncio.ensure_future(send_msg(message, user, reply_markup)) async for user in users]
    count = len(cor)
    await asyncio.gather(*cor)
    time_end = str(time.time() - time_start)[:5]
    await message.reply("<b>Ваше сообщение было отправлено {} пользователям за {} секунд!</b>".format(
        count, time_end
    ))


async def edit_cor(message, sent_message, reply_markup):
    try:
        await bot.edit_message_text(chat_id=sent_message["receiver_id"],
                                    message_id=sent_message["receiver_message_id"],
                                    text=f"{message.text} (edited)",
                                    reply_markup=reply_markup)
    except BotBlocked:
        await db.users.delete_one(dict(user_id=sent_message["receiver_id"]))
    except UserDeactivated:
        await db.users.delete_one(dict(user_id=sent_message["receiver_id"]))
    except Exception as e:
        logging.exception(e)


@dp.edited_message_handler(content_types=types.ContentType.ANY)
async def edit_handler(message: types.Message):
    user = User(**(await db.users.find_one({"user_id": message.from_user.id})))
    settings = await get_settings(user.user_id)
    show_nickname_inline = settings["show_nickname_inline"]
    reply_markup = None
    if show_nickname_inline:
        nickname = message.from_user.username if message.from_user.username else "id" + str(message.from_user.id)
        inline_btn_nickname = types.InlineKeyboardButton(
            ('VIP' if user.vip else '') + ('ADMIN' if user.admin else '') + ' ' + nickname,
            callback_data="user"
        )
        reply_markup = types.InlineKeyboardMarkup().add(inline_btn_nickname)
    sent_messages = await get_sent_messages(message.from_user.id, message.message_id)
    await asyncio.gather(*[edit_cor(message, sent_message, reply_markup) for sent_message in sent_messages])


@dp.message_handler(Command('del'), is_reply=True)
async def delete_handler(message: types.Message):
    if message.reply_to_message:
        if message.reply_to_message.from_user.is_bot:
            msgs = db.sent_messages.find(dict(sender_message_id=message.reply_to_message.message_id))
        else:
            msgs = db.sent_messages.find(dict(original_message_id=message.reply_to_message.message_id))
        async for msg in msgs:
            if not await is_admin(message.from_user.id) and not message.from_user.id == msg["sender_id"]:
                await message.reply("У вас нет прав для удаления сообщений.")
                return

            msg_check = db.sent_messages.find(dict(original_message_id=msg["original_message_id"]))

            async for msg_need in msg_check:
                try:
                    await bot.delete_message(msg_need["receiver_id"], msg_need["receiver_message_id"])
                except Exception as e:
                    logging.exception(e)

        await message.reply("Сообщение было удалено у всех пользователей.")


@dp.message_handler(Command('ban'), is_reply=True)
async def ban_handler(message: types.Message):
    user_id: int = 0
    if message.reply_to_message:
        if message.reply_to_message.from_user.is_bot:
            msgs = db.sent_messages.find(dict(sender_message_id=message.reply_to_message.message_id))
        else:
            msgs = db.sent_messages.find(dict(original_message_id=message.reply_to_message.message_id))
        async for msg in msgs:
            if not await is_admin(message.from_user.id):
                await message.reply("У вас нет прав для блокировки пользователя.")
                return

            user_id = msg["sender_id"]

            user = User(**(await db.users.find_one({"user_id": user_id})))
            if user:
                if user.vip:
                    await save_user(User(user_id=user_id), vip=False)
                if user.admin:
                    await save_user(User(user_id=user_id), admin=False)

                await db.cooldown.update_one({"user_id": user_id},
                                             {"$set": {"sent_at": datetime.now() + timedelta(days=365)}},
                                             upsert=True)

        if user_id:
            await message.reply("Пользователь был заблокирован!\nАйди: {}".format(user_id))
        else:
            await message.reply("Что-то пошло не так")


@dp.message_handler(Command('unban'), is_reply=True)
async def unban_handler(message: types.Message):
    user_id: int = 0
    if message.reply_to_message:
        if message.reply_to_message.from_user.is_bot:
            msgs = db.sent_messages.find(dict(sender_message_id=message.reply_to_message.message_id))
        else:
            msgs = db.sent_messages.find(dict(original_message_id=message.reply_to_message.message_id))
        async for msg in msgs:
            if not await is_admin(message.from_user.id):
                await message.reply("У вас нет прав для разблокировки пользователя.")
                return

            user_id = msg["sender_id"]

            user = User(**(await db.users.find_one({"user_id": user_id})))
            if user:
                await db.cooldown.update_one({"user_id": user_id},
                                             {"$set": {"sent_at": datetime.now() - timedelta(seconds=60)}},
                                             upsert=True)

        if user_id:
            await message.reply("Пользователь был разблокирован!\nАйди: {}".format(user_id))
        else:
            await message.reply("Что-то пошло не так")


@dp.message_handler(content_types=[types.ContentType.ANY])
async def text_handler(message: types.Message):
    user_id = message.from_user.id
    if await is_admin(user_id) or await is_vip(user_id):
        await broadcast_message(message)
    elif await cooldown_check(user_id):
        await update_cooldown(user_id)
        await broadcast_message(message)
    else:
        await message.reply('Пожалуйста, подождите 1 минуту перед отправкой следующего сообщения.')


async def on_startup(disp):
    await bot.send_message(chat_id=admin_id, text='Бот запущен')
    await disp.bot.set_my_commands(
        [BotCommand(command, description) for command, description in commands.items()]
    )


async def on_shutdown(disp):
    await bot.send_message(chat_id=admin_id, text='Бот остановлен')
    await disp.storage.close()
    await disp.storage.wait_closed()


if __name__ == '__main__':
    from aiogram import executor

    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown)

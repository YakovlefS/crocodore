import os
import logging
import random
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
)
from aiogram.client.default import DefaultBotProperties

# ========= ЛОГИ =========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========= ENV =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
THREAD_ID = int(os.getenv("THREAD_ID", "0"))

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN не задан")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)

dp = Dispatcher()

# ========= ИГРА =========
game = {
    "active": False,
    "word": None,
    "leader_id": None,
    "attempts": 0,
}

scores: dict[int, int] = {}

# ========= УТИЛИТЫ =========

def normalize(text: str) -> str:
    """ Оставляем только буквы (кириллица/латиница), приводим к нижнему регистру """
    return "".join(ch.lower() for ch in text if ch.isalpha())


def mention(user) -> str:
    """ HTML-упоминание """
    name = (user.full_name or "игрок").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def in_target_topic(message: Message) -> bool:
    """ Проверяем, что сообщение именно в НУЖНОЙ теме """
    if not message.chat or message.chat.id != CHAT_ID:
        return False

    # Если нет привязки к теме — работаем везде
    if THREAD_ID == 0:
        return True

    thread = getattr(message, "message_thread_id", None)

    # Если тема указана явно — сверяем
    if thread is not None:
        return thread == THREAD_ID

    # Telegram иногда НЕ присылает message_thread_id
    # Но бот работает только в 1 теме → считаем, что всё ок
    return True


async def is_admin(user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(CHAT_ID, user_id)
        return m.status in ("creator", "administrator", "owner")
    except:
        return False


async def load_words() -> list[str]:
    try:
        with open("words.txt", "r", encoding="utf-8") as f:
            return [w.strip().lower() for w in f if w.strip()]
    except:
        return ["крокодил", "машина", "лампа", "река"]


def leader_keyboard(leader_id: int) -> InlineKeyboardMarkup:
    """ Кнопки, видимые только ведущему """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👁 Показать слово",
                    callback_data=f"show:{leader_id}"
                ),
                InlineKeyboardButton(
                    text="🔄 Новое слово",
                    callback_data=f"replace:{leader_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⛔ Остановить игру",
                    callback_data=f"stop:{leader_id}"
                )
            ]
        ]
    )


async def setup_bot_commands(bot: Bot):
    """ Команды в меню Telegram """
    commands = [
        BotCommand(command="start", description="Описание бота"),
        BotCommand(command="startgame", description="Начать игру"),
        BotCommand(command="score", description="Рейтинг игроков"),
        BotCommand(command="top", description="Топ-10 игроков"),
        BotCommand(command="hint", description="Подсказка (ведущий)"),
        BotCommand(command="resetgame", description="Сброс игры (админ)"),
        BotCommand(command="info", description="Показать chat_id и thread_id"),
    ]

    await bot.set_my_commands(commands)


# ========= КОМАНДЫ =========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🐊 <b>Крокодил Бот готов!</b>\n\n"
        "/startgame — начать игру и стать ведущим\n"
        "/score — рейтинг игроков\n"
        "/top — топ-10\n"
        "/hint — подсказка (только ведущий)\n"
        "/resetgame — сброс игры и очков (админ)\n"
        "/info — chat_id & thread_id\n"
    )


@dp.message(Command("info"))
async def cmd_info(message: Message):
    thread = getattr(message, "message_thread_id", None)
    await message.answer(
        f"<b>chat_id:</b> <code>{message.chat.id}</code>\n"
        f"<b>thread_id:</b> <code>{thread}</code>"
    )


@dp.message(Command("startgame"))
async def cmd_startgame(message: Message):
    if not in_target_topic(message):
        return

    if game["active"]:
        await message.answer("⚠️ Игра уже идёт.")
        return

    words = await load_words()
    leader = message.from_user

    game["active"] = True
    game["leader_id"] = leader.id
    game["word"] = random.choice(words)
    game["attempts"] = 0

    await message.answer(
        f"🎮 Новый раунд!\nВедущий: {mention(leader)}",
        reply_markup=leader_keyboard(leader.id)
    )


@dp.message(Command("score"))
async def cmd_score(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        await message.answer("📊 Пока нет очков.")
        return

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    text = "\n".join(f"{i+1}. <code>{uid}</code> — {pts}" for i, (uid, pts) in enumerate(rating))

    await message.answer("📊 <b>Рейтинг:</b>\n" + text)


@dp.message(Command("top"))
async def cmd_top(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        await message.answer("🏆 Тут пока пусто.")
        return

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
    text = "\n".join(f"{i+1}. <code>{uid}</code> — {pts}" for i, (uid, pts) in enumerate(rating))

    await message.answer("🏆 <b>Топ-10:</b>\n" + text)


@dp.message(Command("resetgame"))
async def cmd_reset(message: Message):
    if not in_target_topic(message):
        return

    if not await is_admin(message.from_user.id):
        await message.answer("⛔ Только админ может сбросить игру.")
        return

    scores.clear()
    game.update(active=False, leader_id=None, word=None, attempts=0)

    await message.answer("♻️ Игра и рейтинг полностью сброшены.")


@dp.message(Command("hint"))
async def cmd_hint(message: Message):
    if not in_target_topic(message):
        return

    if not game["active"]:
        await message.answer("Сейчас не идёт игра.")
        return

    if game["leader_id"] != message.from_user.id:
        await message.answer("Подсказку может дать только ведущий.")
        return

    word = game["word"]
    hint = word[0] + " _" * (len(word) - 1)

    await message.answer(
        f"💡 Подсказка:\n"
        f"Слово из {len(word)} букв\n"
        f"<code>{hint}</code>"
    )


# ========= CALLBACK-КНОПКИ =========

@dp.callback_query()
async def process_buttons(call: CallbackQuery):
    if call.message.chat.id != CHAT_ID:
        return

    # мягкая проверка темы
    if THREAD_ID != 0:
        thread = getattr(call.message, "message_thread_id", None)
        if thread is not None and thread != THREAD_ID:
            return

    if not game["active"]:
        await call.answer("Игра уже остановлена.", show_alert=True)
        return

    data = call.data or ""
    action, leader_str = data.split(":")
    leader_id = int(leader_str)

    if call.from_user.id != leader_id:
        await call.answer("Вы не ведущий.", show_alert=True)
        return

    if action == "show":
        await call.answer(f"Слово: {game['word']}", show_alert=True)
        return

    if action == "replace":
        words = await load_words()
        game["word"] = random.choice(words)
        game["attempts"] = 0
        await call.answer(f"Новое слово: {game['word']}", show_alert=True)
        return

    if action == "stop":
        if not await is_admin(call.from_user.id):
            await call.answer("⛔ Только админ может остановить игру.", show_alert=True)
            return

        game.update(active=False, leader_id=None, word=None, attempts=0)
        await call.message.answer("⛔ Игра остановлена.")
        await call.answer("Готово.", show_alert=True)
        return


# ========= УГАДЫВАНИЕ =========

@dp.message()
async def guessing(message: Message):
    if not in_target_topic(message):
        return

    if not game["active"] or not game["word"]:
        return

    if message.from_user.id == game["leader_id"]:
        return

    if not message.text:
        return

    guess = normalize(message.text)
    answer = normalize(game["word"])

    if answer not in guess:
        game["attempts"] += 1
        return

    # Угадано
    uid = message.from_user.id
    scores[uid] = scores.get(uid, 0) + 1

    await message.answer(
        f"🎉 {mention(message.from_user)} угадал слово <b>{game['word']}</b>!\n"
        f"Теперь у него {scores[uid]} очков."
    )

    # Передаём ход
    words = await load_words()
    new_word = random.choice(words)

    game["leader_id"] = uid
    game["word"] = new_word
    game["attempts"] = 0

    await message.answer(
        f"👉 Новый ведущий: {mention(message.from_user)}",
        reply_markup=leader_keyboard(uid)
    )


# ========= ЗАПУСК =========

async def main():
    logger.info("🚀 Бот запущен!")
    await setup_bot_commands(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

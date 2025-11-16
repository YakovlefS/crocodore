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

# ========= СОСТОЯНИЕ ИГРЫ =========

game = {
    "active": False,
    "word": None,
    "leader_id": None,
    "attempts": 0,
}

scores: dict[int, int] = {}

# ========= УТИЛИТЫ =========

def normalize(text: str) -> str:
    """Оставляем только буквы, приводим к нижнему регистру."""
    return "".join(ch.lower() for ch in text if ch.isalpha())


def mention(user) -> str:
    """Тег пользователя в HTML."""
    name = user.full_name.replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def in_target_topic(message: Message) -> bool:
    # 1. Проверяем чат
    if not message.chat or message.chat.id != CHAT_ID:
        return False

    # 2. Если разрешено без темы
    if THREAD_ID == 0:
        return True

    # 3. Если поле есть — строго сравниваем
    thread = getattr(message, "message_thread_id", None)
    if thread is not None:
        return thread == THREAD_ID

    # 4. Если поле пропало — считаем, что это нужная тема
    # (Потому что бот работает только в ОДНОЙ)
    return True


async def is_admin(user_id: int) -> bool:
    """Проверка статуса в чате."""
    try:
        m = await bot.get_chat_member(CHAT_ID, user_id)
        return m.status in ("administrator", "creator", "owner")
    except:
        return False


async def load_words() -> list[str]:
    """Загрузка слов."""
    try:
        with open("words.txt", "r", encoding="utf-8") as f:
            return [w.strip().lower() for w in f if w.strip()]
    except:
        return ["яблоко", "машина", "крокодил", "лампа", "река"]


def leader_keyboard(leader_id: int) -> InlineKeyboardMarkup:
    """Кнопки ведущего."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👁 Показать слово", callback_data=f"show:{leader_id}"),
                InlineKeyboardButton(text="🔄 Новое слово", callback_data=f"replace:{leader_id}")
            ],
            [
                InlineKeyboardButton(text="⛔ Остановить игру", callback_data=f"stop:{leader_id}")
            ]
        ]
    )


async def setup_bot_commands(bot: Bot):
    """Команды для меню."""
    commands = [
        BotCommand(command="start", description="Описание бота"),
        BotCommand(command="startgame", description="Начать игру"),
        BotCommand(command="score", description="Рейтинг игроков"),
        BotCommand(command="top", description="Топ-10"),
        BotCommand(command="hint", description="Подсказка (ведущему)"),
        BotCommand(command="resetgame", description="Сброс (только админ)"),
        BotCommand(command="info", description="Техническая информация"),
    ]
    await bot.set_my_commands(commands)


# ========= КОМАНДЫ =========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🐊 <b>Крокодил бот</b>\n\n"
        "/startgame — начать игру\n"
        "/score — рейтинг\n"
        "/top — топ-10\n"
        "/hint — подсказка (ведущий)\n"
        "/resetgame — сброс (админ)\n"
        "/info — chat_id и thread_id\n"
    )


@dp.message(Command("info"))
async def cmd_info(message: Message):
    await message.answer(
        f"<b>chat_id:</b> <code>{message.chat.id}</code>\n"
        f"<b>thread_id:</b> <code>{getattr(message, 'message_thread_id', None)}</code>"
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

    lines = []
    for idx, (uid, pts) in enumerate(sorted(scores.items(), key=lambda x: x[1], reverse=True), start=1):
        lines.append(f"{idx}. <code>{uid}</code> — {pts}")

    await message.answer("📊 <b>Рейтинг:</b>\n" + "\n".join(lines))


@dp.message(Command("top"))
async def cmd_top(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        await message.answer("🏆 Нет записей.")
        return

    top10 = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
    lines = []
    for idx, (uid, pts) in enumerate(top10, start=1):
        lines.append(f"{idx}. <code>{uid}</code> — {pts}")

    await message.answer("🏆 <b>Топ-10:</b>\n" + "\n".join(lines))


@dp.message(Command("resetgame"))
async def cmd_resetgame(message: Message):
    if not in_target_topic(message):
        return

    if not await is_admin(message.from_user.id):
        await message.answer("⛔ Только администратор может сбросить игру.")
        return

    game["active"] = False
    game["leader_id"] = None
    game["word"] = None
    game["attempts"] = 0
    scores.clear()

    await message.answer("♻️ Игра и рейтинг сброшены.")


@dp.message(Command("hint"))
async def cmd_hint(message: Message):
    if not in_target_topic(message):
        return
    if not game["active"]:
        await message.answer("Сейчас игра не идёт.")
        return
    if message.from_user.id != game["leader_id"]:
        await message.answer("Подсказку может дать только ведущий.")
        return

    word = game["word"]
    hint = word[0] + " _" * (len(word) - 1)

    await message.answer(
        f"💡 Подсказка:\n"
        f"Слово из {len(word)} букв\n"
        f"Первая буква: <b>{word[0].upper()}</b>\n"
        f"<code>{hint}</code>"
    )


# ========= CALLBACK-КНОПКИ =========

@dp.callback_query()
async def callbacks(call: CallbackQuery):
    if call.message.chat.id != CHAT_ID:
        return

   if THREAD_ID != 0:
    thread = getattr(call.message, "message_thread_id", None)
    if thread is not None and thread != THREAD_ID:
        return

    if not game["active"] or not game["leader_id"]:
        await call.answer("Игра не идёт.", show_alert=True)
        return

    data = call.data
    action, leader_id_str = data.split(":")
    leader_id = int(leader_id_str)

    if call.from_user.id != leader_id:
        await call.answer("Вы не ведущий.", show_alert=True)
        return

    # показать слово
    if action == "show":
        await call.answer(f"Слово: {game['word']}", show_alert=True)

    # новое слово
    elif action == "replace":
        words = await load_words()
        game["word"] = random.choice(words)
        game["attempts"] = 0
        await call.answer(f"Новое слово: {game['word']}", show_alert=True)

    # стоп
    elif action == "stop":
        if not await is_admin(call.from_user.id):
            await call.answer("⛔ Только админ может остановить игру.", show_alert=True)
            return

        game["active"] = False
        game["leader_id"] = None
        game["word"] = None
        game["attempts"] = 0

        await call.message.answer("⛔ Игра остановлена.")
        await call.answer("Готово.")


# ========= УГАДЫВАНИЕ =========

@dp.message()
async def game_guess(message: Message):
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
        if game["attempts"] == 10:
            await message.answer("😅 Слишком много ошибок. /hint — для подсказки")
        return

    # УГАДАНО
    uid = message.from_user.id
    scores[uid] = scores.get(uid, 0) + 1

    await message.answer(
        f"🎉 {mention(message.from_user)} угадал слово <b>{game['word']}</b>!\n"
        f"Теперь у него {scores[uid]} очков."
    )

    # передача ведущего
    words = await load_words()
    game["leader_id"] = uid
    game["word"] = random.choice(words)
    game["attempts"] = 0

    await message.answer(
        f"👉 Новый ведущий: {mention(message.from_user)}",
        reply_markup=leader_keyboard(uid)
    )


# ========= ЗАПУСК =========

async def main():
    logger.info("🚀 Бот запущен (Aiogram 3.7+)")

    await setup_bot_commands(bot)

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types()
    )


if __name__ == "__main__":
    asyncio.run(main())

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
    raise SystemExit("BOT_TOKEN is required")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

# ========= GAME STATE =========
game = {
    "active": False,
    "word": None,
    "leader_id": None,
    "attempts": 0,
}

scores = {}
used_words = set()   # <--- ВАЖНО! История использованных слов


# ========= HELPERS =========

def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return "".join(ch for ch in text if ch.isalpha())


def mention(user) -> str:
    name = user.full_name.replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def in_target_topic(message: Message) -> bool:
    if not message.chat or message.chat.id != CHAT_ID:
        return False

    if THREAD_ID == 0:
        return True

    thread = getattr(message, "message_thread_id", None)
    if thread is not None:
        return thread == THREAD_ID

    return False


async def is_admin(user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(CHAT_ID, user_id)
        return m.status in ("administrator", "creator", "owner")
    except:
        return False


async def load_words():
    try:
        with open("words.txt", "r", encoding="utf-8") as f:
            return [w.strip().lower() for w in f if w.strip()]
    except:
        return ["крокодил", "машина", "лампа", "река", "дерево"]


def leader_keyboard(uid: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👁 Показать слово",
                    callback_data=f"show:{uid}"
                ),
                InlineKeyboardButton(
                    text="🔄 Новое слово",
                    callback_data=f"replace:{uid}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⛔ Остановить игру",
                    callback_data=f"stop:{uid}"
                )
            ],
        ]
    )


async def setup_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Описание бота"),
        BotCommand(command="startgame", description="Начать игру"),
        BotCommand(command="score", description="Рейтинг игроков"),
        BotCommand(command="top", description="Топ 10 игроков"),
        BotCommand(command="hint", description="Подсказка (ведущий)"),
        BotCommand(command="resetgame", description="Сбросить игру (админ)"),
        BotCommand(command="info", description="chat_id и thread_id"),
    ])


# ========= COMMANDS =========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🐊 Крокодил бот!\n\n"
        "/startgame — начать\n"
        "/score — рейтинг\n"
        "/top — ТОП-10\n"
        "/hint — подсказка\n"
        "/resetgame — сброс игры\n"
        "/info — ID чата/темы"
    )


@dp.message(Command("info"))
async def cmd_info(message: Message):
    thread = getattr(message, "message_thread_id", None)
    await message.answer(
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"thread_id: <code>{thread}</code>"
    )


@dp.message(Command("startgame"))
async def cmd_startgame(message: Message):
    if not in_target_topic(message):
        return

    if game["active"]:
        await message.answer("⚠️ Игра уже идет.")
        return

    words = await load_words()

    global used_words
    candidates = [w for w in words if w not in used_words]

    if not candidates:
        used_words.clear()
        candidates = words.copy()

    word = random.choice(candidates)
    used_words.add(word)

    leader = message.from_user

    game.update(
        active=True,
        word=word,
        leader_id=leader.id,
        attempts=0
    )

    await message.answer(
        f"🎮 Новый раунд!\nВедущий: {mention(leader)}",
        reply_markup=leader_keyboard(leader.id),
    )


@dp.message(Command("score"))
async def cmd_score(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        await message.answer("📊 Рейтинг пуст.")
        return

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    lines = [f"{i+1}. <code>{uid}</code> — {pts}" for i, (uid, pts) in enumerate(rating)]

    await message.answer("📊 <b>Рейтинг:</b>\n" + "\n".join(lines))


@dp.message(Command("top"))
async def cmd_top(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        await message.answer("🏆 Никто ещё не играл.")
        return

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
    lines = [f"{i+1}. <code>{uid}</code> — {pts}" for i, (uid, pts) in enumerate(rating)]

    await message.answer("🏆 <b>Топ-10:</b>\n" + "\n".join(lines))


@dp.message(Command("resetgame"))
async def cmd_resetgame(message: Message):
    if not in_target_topic(message):
        return

    if not await is_admin(message.from_user.id):
        return await message.answer("⛔ Сбросить игру может только админ.")

    game.update(active=False, leader_id=None, word=None, attempts=0)
    scores.clear()
    used_words.clear()  # <---- СБРАСЫВАЕМ ИСТОРИЮ

    await message.answer("♻️ Игра и рейтинг сброшены.")


@dp.message(Command("hint"))
async def cmd_hint(message: Message):
    if not in_target_topic(message):
        return

    if not game["active"]:
        return await message.answer("Игра ещё не началась")

    if message.from_user.id != game["leader_id"]:
        return await message.answer("Только ведущий может давать подсказки")

    word = game["word"]
    hint = word[0] + " _" * (len(word) - 1)

    await message.answer(
        f"💡 Подсказка:\n"
        f"Слово из {len(word)} букв\n"
        f"<code>{hint}</code>"
    )


# ========= CALLBACKS =========

@dp.callback_query()
async def on_button(call: CallbackQuery):
    msg = call.message

    if msg.chat.id != CHAT_ID:
        return

    if THREAD_ID != 0:
        thread = getattr(msg, "message_thread_id", None)
        if thread is not None and thread != THREAD_ID:
            return

    data = call.data.split(":")
    action = data[0]
    leader_id = int(data[1])

    if call.from_user.id != leader_id:
        return await call.answer("Вы не ведущий.", show_alert=True)

    if action == "show":
        return await call.answer(f"Слово: {game['word']}", show_alert=True)

    if action == "replace":
        words = await load_words()

        global used_words
        candidates = [w for w in words if w not in used_words]
        if not candidates:
            used_words.clear()
            candidates = words.copy()

        new_word = random.choice(candidates)
        used_words.add(new_word)

        game["word"] = new_word
        game["attempts"] = 0

        return await call.answer(f"Новое слово: {new_word}", show_alert=True)

    if action == "stop":
        if not await is_admin(call.from_user.id):
            return await call.answer("Только админ может остановить игру.", show_alert=True)

        game.update(active=False, word=None, leader_id=None, attempts=0)
        used_words.clear()

        await msg.answer("⛔ Игра остановлена.")
        return await call.answer("Готово", show_alert=True)


# ========= GUESSING =========

@dp.message()
async def on_guess(message: Message):
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

    if not guess:
        return

    if answer not in guess:
        game["attempts"] += 1
        return

    # УГАДАНО!
    uid = message.from_user.id
    scores[uid] = scores.get(uid, 0) + 1

    await message.answer(
        f"🎉 {mention(message.from_user)} угадал слово <b>{game['word']}</b>!\n"
        f"Теперь у него {scores[uid]} очков."
    )

    # Новый ведущий и новое слово
    words = await load_words()

    global used_words
    candidates = [w for w in words if w not in used_words]
    if not candidates:
        used_words.clear()
        candidates = words.copy()

    new_word = random.choice(candidates)
    used_words.add(new_word)

    game["leader_id"] = uid
    game["word"] = new_word
    game["attempts"] = 0

    await message.answer(
        f"👉 Новый ведущий: {mention(message.from_user)}",
        reply_markup=leader_keyboard(uid),
    )


# ========= RUN =========

async def main():
    logger.info("🚀 Bot Started!")
    await setup_commands(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

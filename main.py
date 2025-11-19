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

# ========= ПЕРСИСТЕНТНОСТЬ =========
USED_WORDS_FILE = "used_words.txt"


def load_used_words() -> set:
    try:
        with open(USED_WORDS_FILE, "r", encoding="utf-8") as f:
            return {w.strip().lower() for w in f if w.strip()}
    except FileNotFoundError:
        return set()


def save_used_word(word: str):
    with open(USED_WORDS_FILE, "a", encoding="utf-8") as f:
        f.write(word.lower() + "\n")


used_words = load_used_words()

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
            words = [w.strip().lower() for w in f if w.strip()]
        game["words_count"] = len(words)   # <<< СЧЁТЧИК
        return words
    except:
        fallback = ["крокодил", "машина", "лампа", "река", "дерево"]
        game["words_count"] = len(fallback)
        return fallback


def leader_keyboard(uid: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👁 Показать слово", callback_data=f"show:{uid}"),
                InlineKeyboardButton(text="🔄 Новое слово", callback_data=f"replace:{uid}"),
            ],
            [
                InlineKeyboardButton(text="⛔ Остановить игру", callback_data=f"stop:{uid}")
            ],
        ]
    )


async def setup_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Описание бота"),
        BotCommand(command="startgame", description="Начать игру"),
        BotCommand(command="score", description="Рейтинг игроков"),
        BotCommand(command="top", description="Топ-10 игроков"),
        BotCommand(command="hint", description="Подсказка (ведущий)"),
        BotCommand(command="resetgame", description="Сброс игры (админ)"),
        BotCommand(command="info", description="ID чата/темы"),
    ])


# ========= КОМАНДЫ =========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🐊 <b>Крокодил Бот</b>\n\n"
        "/startgame — начать игру и стать ведущим\n"
        "/score — рейтинг игроков\n"
        "/top — топ 10 игроков\n"
        "/hint — подсказка ведущему\n"
        "/resetgame — сброс (только админ)\n"
        "/info — chat_id & thread_id\n"
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
        return await message.answer("⚠️ Игра уже идёт!")

    words = await load_words()
    global used_words

    candidates = [w for w in words if w not in used_words]
    if not candidates:
        return await message.answer("🎉 Все слова были использованы!")

    word = random.choice(candidates)
    used_words.add(word)
    save_used_word(word)

    leader = message.from_user

    game.update(active=True, word=word, leader_id=leader.id, attempts=0)

    await message.answer(
        f"🎮 Игра началась!\nВедущий: {mention(leader)}",
        reply_markup=leader_keyboard(leader.id)
    )


@dp.message(Command("score"))
async def cmd_score(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        return await message.answer("📊 Рейтинг пуст.")

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    lines = []
    for i, (uid, pts) in enumerate(rating, 1):
        try:
            member = await bot.get_chat_member(CHAT_ID, uid)
            user = member.user
            if user.username:
                name = f"@{user.username}"
            else:
                name = user.full_name
        except:
            name = f"ID:{uid}"

        lines.append(f"{i}. {name} — {pts}")

    await message.answer("📊 <b>Рейтинг игроков:</b>\n" + "\n".join(lines))


@dp.message(Command("top"))
async def cmd_top(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        return await message.answer("🏆 Топ пуст.")

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]

    lines = []
    for i, (uid, pts) in enumerate(rating, 1):
        try:
            member = await bot.get_chat_member(CHAT_ID, uid)
            user = member.user
            if user.username:
                name = f"@{user.username}"
            else:
                name = user.full_name
        except:
            name = f"ID:{uid}"

        lines.append(f"{i}. {name} — {pts}")

    await message.answer("🏆 <b>Топ-10 игроков:</b>\n" + "\n".join(lines))


@dp.message(Command("resetgame"))
async def cmd_resetgame(message: Message):
    if not await is_admin(message.from_user.id):
        return await message.answer("⛔ Только админ может сбросить игру")

    game.update(active=False, leader_id=None, word=None, attempts=0)
    # ВНИМАНИЕ — мы НЕ очищаем used_words,
    # чтобы слова не повторялись НИКОГДА
    scores.clear()

    await message.answer("♻️ Игра и рейтинг сброшены.\nСлова больше не повторятся.")


@dp.message(Command("hint"))
async def cmd_hint(message: Message):
    if not in_target_topic(message):
        return

    if not game["active"]:
        return await message.answer("Игра ещё не началась")

    if message.from_user.id != game["leader_id"]:
        return await message.answer("Подсказки даёт только ведущий")

    word = game["word"]
    hint = word[0] + " _" * (len(word) - 1)

    await message.answer(
        f"💡 Подсказка:\nСлово из {len(word)} букв\n<code>{hint}</code>"
    )

@dp.message(Command("words"))
async def cmd_words(message: Message):
    if not in_target_topic(message):
        return

    # Если слова ещё не загружены – загрузим принудительно
    if "words_count" not in game:
        await load_words()

    await message.answer(f"📘 Загружено слов: <b>{game['words_count']}</b>")

# ========= КНОПКИ =========

@dp.callback_query()
async def on_callback(call: CallbackQuery):
    msg = call.message

    if msg.chat.id != CHAT_ID:
        return

    if THREAD_ID != 0:
        thread = getattr(msg, "message_thread_id", None)
        if thread != THREAD_ID:
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
            return await call.answer("Слова закончились!", show_alert=True)

        new_word = random.choice(candidates)
        used_words.add(new_word)
        save_used_word(new_word)

        game["word"] = new_word
        game["attempts"] = 0

        return await call.answer(f"Новое слово: {new_word}", show_alert=True)

    if action == "stop":
        if not await is_admin(call.from_user.id):
            return await call.answer("Только админ!", show_alert=True)

        game.update(active=False, word=None, leader_id=None)
        return await msg.answer("⛔ Игра остановлена.")


# ========= УГАДЫВАНИЕ =========

@dp.message()
async def on_guess(message: Message):
    if not in_target_topic(message):
        return

    if not game["active"]:
        return

    if not message.text:
        return

    if message.from_user.id == game["leader_id"]:
        return

    guess = normalize(message.text)
    answer = normalize(game["word"])

    if answer not in guess:
        game["attempts"] += 1
        return

    uid = message.from_user.id
    scores[uid] = scores.get(uid, 0) + 1

    await message.answer(
        f"🎉 {mention(message.from_user)} угадал слово <b>{game['word']}</b>!\n"
        f"Теперь у него {scores[uid]} очков."
    )

    # Новый ведущий — тот, кто угадал
    words = await load_words()
    global used_words

    candidates = [w for w in words if w not in used_words]
    if not candidates:
        await message.answer("🎉 Все слова кончились! Игра завершена!")
        game["active"] = False
        return

    new_word = random.choice(candidates)
    used_words.add(new_word)
    save_used_word(new_word)

    game.update(leader_id=uid, word=new_word, attempts=0)

    await message.answer(
        f"👉 Новый ведущий: {mention(message.from_user)}",
        reply_markup=leader_keyboard(uid)
    )


# ========= ЗАПУСК =========

async def main():
    logger.info("🚀 Бот запущен!")
    await setup_commands(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

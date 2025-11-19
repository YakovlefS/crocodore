import os
import json
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

# ====== ЛОГИ ======
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====== ENV ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
THREAD_ID = int(os.getenv("THREAD_ID", "0"))

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN отсутствует!")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

# ====== ОФИЦЕРЫ ======
OFFICERS = [
    "@Maffins89",
    "@Gi_Di_Al",
    "@oOMEMCH1KOo",
    "@Ferbi55",
    "@Ahaha_Ohoho",
    "@yakovlef"
]

def is_officer(username: str) -> bool:
    if not username:
        return False
    return ("@" + username.lower()) in [o.lower() for o in OFFICERS]


# ====== ФАЙЛ ИСТОРИИ СЛОВ ======
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


# ====== ФАЙЛ ОЧКОВ ======
SCORES_FILE = "scores.json"

def load_scores() -> dict:
    try:
        with open(SCORES_FILE, "r", encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except:
        return {}

def save_scores(scores: dict):
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)


# ====== СОСТОЯНИЕ ИГРЫ ======
game = {
    "active": False,
    "word": None,
    "leader_id": None,
    "attempts": 0,
    "words_count": 0
}

scores = load_scores()


# ====== HELPERS ======
def normalize(t: str) -> str:
    t = t.lower().replace("ё", "е")
    return "".join(ch for ch in t if ch.isalpha())

def mention(user) -> str:
    name = user.full_name.replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user.id}">{name}</a>'

def in_target_topic(message: Message) -> bool:
    if message.chat.id != CHAT_ID:
        return False
    if THREAD_ID == 0:
        return True
    return getattr(message, "message_thread_id", None) == THREAD_ID

async def is_admin(uid: int) -> bool:
    try:
        m = await bot.get_chat_member(CHAT_ID, uid)
        return m.status in ("administrator", "creator")
    except:
        return False

async def load_words():
    try:
        with open("words.txt", "r", encoding="utf-8") as f:
            words = [w.strip().lower() for w in f if w.strip()]
        game["words_count"] = len(words)
        return words
    except:
        fallback = ["крокодил", "машина", "лампа"]
        game["words_count"] = len(fallback)
        return fallback

def leader_keyboard(uid: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👁 Показать слово", callback_data=f"show:{uid}"),
                InlineKeyboardButton(text="🔄 Новое слово", callback_data=f"replace:{uid}")
            ],
            [
                InlineKeyboardButton(text="🎯 Передать ход", callback_data=f"pass:{uid}")
            ],
            [
                InlineKeyboardButton(text="⛔ Стоп", callback_data=f"stop:{uid}")
            ]
        ]
    )

async def setup_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="startgame", description="Начать игру"),
        BotCommand(command="score", description="Рейтинг"),
        BotCommand(command="top", description="Топ-10"),
        BotCommand(command="hint", description="Подсказка"),
        BotCommand(command="addword", description="Добавить слово"),
        BotCommand(command="showword", description="Офицеры: показать слово"),
        BotCommand(command="passlead", description="Офицеры: передать руководителя"),
        BotCommand(command="words", description="Количество слов"),
        BotCommand(command="info", description="Показать chat_id / thread_id"),
    ])


# ====== КОМАНДЫ ======

@dp.message(Command("info"))
async def cmd_info(message: Message):
    await message.answer(
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"thread_id: <code>{getattr(message, 'message_thread_id', None)}</code>"
    )

@dp.message(Command("words"))
async def cmd_words(message: Message):
    await load_words()
    await message.answer(f"📘 Загружено слов: <b>{game['words_count']}</b>")

@dp.message(Command("addword"))
async def cmd_addword(message: Message):
    if not await is_admin(message.from_user.id):
        return await message.answer("⛔ Только администратор может добавлять слова.")

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("Использование:\n/addword слово")

    word = parts[1].strip().lower()

    if len(word) < 3 or not word.isalpha():
        return await message.answer("❌ Слово должно быть минимум 3 буквы.")

    words = await load_words()

    if word in words:
        return await message.answer("⚠️ Такое слово уже есть.")

    with open("words.txt", "a", encoding="utf-8") as f:
        f.write(word + "\n")

    game["words_count"] += 1

    await message.answer(f"✅ Добавлено слово: <b>{word}</b>\n📘 Теперь слов: {game['words_count']}")


@dp.message(Command("showword"))
async def cmd_showword(message: Message):
    username = message.from_user.username

    if not is_officer(username):
        return await message.answer("⛔ Команда доступна только офицерам.")

    if not game["active"]:
        return await message.answer("⚠️ Игра не идёт.")

    await message.answer(f"👁 Слово: <b>{game['word']}</b>")


@dp.message(Command("passlead"))
async def cmd_passlead(message: Message):
    username = message.from_user.username

    if not is_officer(username):
        return await message.answer("⛔ Передавать ход могут только офицеры.")

    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("Использование:\n/passlead @username")

    target = parts[1].lower()

    try:
        member = await bot.get_chat_member(CHAT_ID, target)
        new_leader = member.user
    except:
        return await message.answer("❌ Пользователь не найден.")

    if not game["active"]:
        return await message.answer("⚠️ Игра не идёт.")

    game["leader_id"] = new_leader.id

    await message.answer(
        f"🎯 Ход передан: {mention(new_leader)}",
        reply_markup=leader_keyboard(new_leader.id)
    )


@dp.message(Command("startgame"))
async def cmd_startgame(message: Message):
    if not in_target_topic(message):
        return

    words = await load_words()
    candidates = [w for w in words if w not in used_words]
    if not candidates:
        return await message.answer("🎉 Все слова использованы!")

    word = random.choice(candidates)
    used_words.add(word)
    save_used_word(word)

    leader = message.from_user

    game.update(active=True, word=word, leader_id=leader.id, attempts=0)

    await message.answer(
        f"🎮 Игра началась!\nВедущий: {mention(leader)}\n📘 Слов в словаре: {game['words_count']}",
        reply_markup=leader_keyboard(leader.id)
    )


@dp.message(Command("score"))
async def cmd_score(message: Message):
    if not scores:
        return await message.answer("📊 Рейтинг пуст.")

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    lines = []
    for i, (uid, pts) in enumerate(rating, 1):
        try:
            member = await bot.get_chat_member(CHAT_ID, uid)
            u = member.user
            name = f"@{u.username}" if u.username else u.full_name
        except:
            name = f"ID:{uid}"

        lines.append(f"{i}. {name} — {pts}")

    await message.answer("📊 <b>Рейтинг:</b>\n" + "\n".join(lines))


@dp.message(Command("top"))
async def cmd_top(message: Message):
    if not scores:
        return await message.answer("🏆 Нет данных.")

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]

    lines = []
    for i, (uid, pts) in enumerate(rating, 1):
        try:
            member = await bot.get_chat_member(CHAT_ID, uid)
            u = member.user
            name = f"@{u.username}" if u.username else u.full_name
        except:
            name = f"ID:{uid}"

        lines.append(f"{i}. {name} — {pts}")

    await message.answer("🏆 <b>Топ-10:</b>\n" + "\n".join(lines))


@dp.message(Command("say"))
async def cmd_say(message: Message):
    if not await is_admin(message.from_user.id):
        return await message.answer("⛔ Только админ может отправлять сообщения от имени бота.")

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("Использование:\n/say текст сообщения")

    text = parts[1]

    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"📢 Сообщение от администратора:\n{text}",
        message_thread_id=THREAD_ID if THREAD_ID != 0 else None
    )

    await message.answer("✅ Отправлено.")


# ====== CALLBACK BUTTONS ======
@dp.callback_query()
async def on_callback(call: CallbackQuery):
    msg = call.message
    data = call.data.split(":")
    action = data[0]
    leader_id = int(data[1])

    # офицеры тоже могут
    ok = (call.from_user.id == leader_id) or is_officer(call.from_user.username)
    if not ok:
        return await call.answer("⛔ Нет доступа.", show_alert=True)

    if action == "show":
        return await call.answer(f"Слово: {game['word']}", show_alert=True)

    if action == "replace":
        words = await load_words()
        candidates = [w for w in words if w not in used_words]

        if not candidates:
            return await call.answer("Слова закончились.", show_alert=True)

        new_word = random.choice(candidates)
        used_words.add(new_word)
        save_used_word(new_word)

        game["word"] = new_word
        game["attempts"] = 0

        return await call.answer(f"Новое слово: {new_word}", show_alert=True)

    if action == "pass":
        await msg.answer("Чтобы передать ход:\n/passlead @username")
        return await call.answer()

    if action == "stop":
        game.update(active=False, word=None, leader_id=None)
        return await msg.answer("⛔ Игра остановлена.")


# ====== ГЛАВНЫЙ MESSAGE HANDLER — УГАДЫВАНИЕ ======
@dp.message()
async def on_guess(message: Message):
    if not in_target_topic(message):
        return

    if not game["active"]:
        return

    if message.from_user.id == game["leader_id"]:
        return

    if not message.text:
        return

    guess = normalize(message.text)
    answer = normalize(game["word"])

    if answer not in guess:
        return

    # угадано
    uid = message.from_user.id
    scores[uid] = scores.get(uid, 0) + 1
    save_scores(scores)

    await message.answer(
        f"🎉 {mention(message.from_user)} угадал слово <b>{game['word']}</b>!\n"
        f"Очки: {scores[uid]}"
    )

    # новое слово
    words = await load_words()
    candidates = [w for w in words if w not in used_words]

    if not candidates:
        await message.answer("🎉 Все слова закончились!")
        game["active"] = False
        return

    new_word = random.choice(candidates)
    used_words.add(new_word)
    save_used_word(new_word)

    game.update(leader_id=uid, word=new_word)

    await message.answer(
        f"👉 Новый ведущий: {mention(message.from_user)}",
        reply_markup=leader_keyboard(uid)
    )


# ====== ЗАПУСК ======
async def main():
    await setup_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

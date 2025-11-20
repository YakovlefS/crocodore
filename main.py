import os
import json
import logging
import random
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ---------- ЛОГИ ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
THREAD_ID = int(os.getenv("THREAD_ID", "0"))

if not BOT_TOKEN:
    raise SystemExit("Не задан BOT_TOKEN")

SUPER_OFFICER = "@yakovlef"

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

# ---------- ФАЙЛЫ ----------
SCORES_FILE = "scores.json"
USED_WORDS_FILE = "used_words.txt"

def load_scores() -> dict:
    try:
        with open(SCORES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            return {int(k): v for k, v in raw.items()}
    except:
        return {}

def save_scores(scores: dict):
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)

def load_used_words() -> set:
    try:
        with open(USED_WORDS_FILE, "r", encoding="utf-8") as f:
            return {w.strip().lower() for w in f if w.strip()}
    except:
        return set()

def save_used_word(word: str):
    with open(USED_WORDS_FILE, "a", encoding="utf-8") as f:
        f.write(word.lower() + "\n")

# ---------- СОСТОЯНИЕ ----------
game = {
    "active": False,
    "word": None,
    "leader_id": None,
    "attempts": 0,
}

scores: dict[int, int] = load_scores()
used_words: set[str] = load_used_words()

# ---------- ФУНКЦИИ ----------

def normalize(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalpha())

def mention(user) -> str:
    name = (user.full_name or "игрок").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user.id}">{name}</a>'

def in_target_topic(message: Message) -> bool:
    return (
        message.chat
        and message.chat.id == CHAT_ID
        and getattr(message, "message_thread_id", None) == THREAD_ID
    )

async def load_words() -> list[str]:
    try:
        with open("words.txt", "r", encoding="utf-8") as f:
            words = [w.strip().lower() for w in f if w.strip()]
        if not words:
            raise ValueError("Пустой words.txt")
        return words
    except:
        return ["яблоко", "кошка", "самолёт", "дерево", "лампа"]

def leader_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👁 Показать слово", callback_data=f"show:{uid}"
                ),
                InlineKeyboardButton(
                    text="🔄 Новое слово", callback_data=f"replace:{uid}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⛔ Остановить игру", callback_data=f"stop:{uid}"
                )
            ]
        ]
    )

def is_super(user) -> bool:
    username = user.from_user.username
    return username and ("@" + username.lower()) == SUPER_OFFICER.lower()


# ---------- КОМАНДЫ ----------

@dp.message(Command("startgame"))
async def cmd_startgame(message: Message):
    if not in_target_topic(message):
        return

    words = await load_words()

    # Ищем НЕиспользованные слова
    candidates = [w for w in words if w not in used_words]
    if not candidates:
        return await message.answer("🎉 Слова закончились! Очистите used_words.txt")

    word = random.choice(candidates)

    used_words.add(word)
    save_used_word(word)

    game.update(
        active=True,
        word=word,
        leader_id=message.from_user.id,
        attempts=0,
    )

    await message.answer(
        f"🎮 Игра началась!\nВедущий: {mention(message.from_user)}",
        reply_markup=leader_keyboard(message.from_user.id)
    )


@dp.message(Command("score"))
async def cmd_score(message: Message):
    if not scores:
        return await message.answer("Рейтинг пуст.")

    sorted_s = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    lines = [
        f"{i+1}. <a href='tg://user?id={uid}'>ID:{uid}</a> — {pts}"
        for i, (uid, pts) in enumerate(sorted_s)
    ]

    await message.answer("📊 Общий рейтинг:\n" + "\n".join(lines))


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
        text=f"\n{text}",
        message_thread_id=THREAD_ID if THREAD_ID != 0 else None
    )

    await message.answer("✅ Отправлено.")


@dp.message(Command("resetgame"))
async def cmd_resetgame(message: Message):
    if not is_super(message):
        return await message.answer("⛔ Только @yakovlef может сбросить игру.")

    game.update(active=False, word=None, leader_id=None, attempts=0)
    scores.clear()
    save_scores(scores)

    await message.answer("♻️ Игра и рейтинг сброшены.")


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


# ====== Рестарт игры ======
@dp.message(Command("restartgame"))
async def cmd_restartgame(message: Message):
    username = message.from_user.username

    # Только офицеры или админы
    if not (is_officer(username) or await is_admin(message.from_user.id)):
        return await message.answer("⛔ Только офицеры или администраторы могут перезапустить игру.")

    words = await load_words()
    candidates = [w for w in words if w not in used_words]

    if not candidates:
        return await message.answer("🎉 Все слова использованы, перезапустить невозможно!")

    new_word = random.choice(candidates)
    used_words.add(new_word)
    save_used_word(new_word)

    game.update(
        active=True,
        leader_id=message.from_user.id,
        word=new_word,
        attempts=0
    )

    await message.answer(
        f"♻️ Игра перезапущена!\n"
        f"🎮 Новый ведущий: {mention(message.from_user)}\n"
        f"🆕 Слово выбрано.",
        reply_markup=leader_keyboard(message.from_user.id)


@dp.message(Command("hint"))
async def cmd_hint(message: Message):
    if not in_target_topic(message):
        return
    if not game["active"]:
        return

    if message.from_user.id != game["leader_id"]:
        return await message.answer("Подсказку может давать только ведущий.")

    word = game["word"]
    mask = word[0] + " " + "_ " * (len(word) - 1)

    await message.answer(
        f"💡 Подсказка:\n"
        f"Слово из {len(word)} букв.\n"
        f"Начинается на <b>{word[0].upper()}</b>\n"
        f"<code>{mask}</code>"
    )


# ---------- CALL BACK ----------

@dp.callback_query()
async def callbacks(call: CallbackQuery):
    if not call.message or not in_target_topic(call.message):
        return
    if not game["active"]:
        return

    data = call.data or ""
    if ":" not in data:
        return

    action, leader_id_str = data.split(":", 1)

    try:
        leader_id = int(leader_id_str)
    except:
        return

    # доступ только ведущему и супер-офицеру
    if call.from_user.id != game["leader_id"] and not is_super(call):
        return await call.answer("⛔ Доступ только ведущему и @yakovlef", show_alert=True)

    # показать слово
    if action == "show":
        return await call.answer(f"Слово: {game['word']}", show_alert=True)

    # новое слово
    elif action == "replace":
        words = await load_words()
        candidates = [w for w in words if w not in used_words]

        if not candidates:
            return await call.answer("Слова закончились!", show_alert=True)

        new_word = random.choice(candidates)
        used_words.add(new_word)
        save_used_word(new_word)

        game["word"] = new_word
        game["attempts"] = 0

        return await call.answer(f"Новое слово: {new_word}", show_alert=True)

    # стоп
    elif action == "stop":
        if not is_super(call):
            return await call.answer("⛔ Остановить игру может только @yakovlef", show_alert=True)

        game.update(active=False, word=None, leader_id=None, attempts=0)
        await call.message.answer("⛔ Игра остановлена.")
        return await call.answer("Остановлено.")


# ---------- УГАДЫВАНИЕ СЛОВА ----------

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

    if not guess:
        return

    if guess != answer:
        return

    # УГАДАЛ
    uid = message.from_user.id
    scores[uid] = scores.get(uid, 0) + 1
    save_scores(scores)

    await message.answer(
        f"🎉 {mention(message.from_user)} угадал слово <b>{game['word']}</b>!\n"
        f"Очки: {scores[uid]}"
    )

    # выбираем новое слово
    words = await load_words()
    candidates = [w for w in words if w not in used_words]

    if not candidates:
        game["active"] = False
        return await message.answer("🎉 Все слова закончились!")

    new_word = random.choice(candidates)
    used_words.add(new_word)
    save_used_word(new_word)

    # передаем ход
    game.update(
        leader_id=uid,
        word=new_word,
        attempts=0
    )

    await message.answer(
        f"👉 Новый ведущий: {mention(message.from_user)}",
        reply_markup=leader_keyboard(uid)
    )


# ---------- ЗАПУСК ----------

async def main():
    logger.info("Бот запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

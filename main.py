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
    BotCommand,
)

# ---------- ЛОГИ ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))       # id группы
THREAD_ID = int(os.getenv("THREAD_ID", "0"))   # id темы

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


def load_scores() -> dict[int, int]:
    try:
        with open(SCORES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            return {int(k): int(v) for k, v in raw.items()}
    except Exception:
        return {}


def save_scores(scores: dict[int, int]):
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)


def load_used_words() -> set[str]:
    try:
        with open(USED_WORDS_FILE, "r", encoding="utf-8") as f:
            return {w.strip().lower() for w in f if w.strip()}
    except Exception:
        return set()


def save_used_word(word: str):
    with open(USED_WORDS_FILE, "a", encoding="utf-8") as f:
        f.write(word.lower() + "\n")


# ---------- СОСТОЯНИЕ ----------
game: dict = {
    "active": False,
    "word": None,
    "leader_id": None,
    "attempts": 0,
}

scores: dict[int, int] = load_scores()
used_words: set[str] = load_used_words()


# ---------- УТИЛИТЫ ----------

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


def is_super_user(user) -> bool:
    username = user.username
    if not username:
        return False
    return ("@" + username.lower()) == SUPER_OFFICER.lower()


async def is_admin(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHAT_ID, user_id)
        status = getattr(member, "status", None)
        return status in ("administrator", "creator", "owner")
    except Exception as e:
        logger.warning(f"Не удалось проверить админа: {e}")
        return False


async def load_words() -> list[str]:
    try:
        with open("words.txt", "r", encoding="utf-8") as f:
            words = [w.strip().lower() for w in f if w.strip()]
        if not words:
            raise ValueError("Пустой words.txt")
        return words
    except Exception as e:
        logger.warning(f"Ошибка чтения words.txt: {e}")
        # fallback
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
            ],
        ]
    )


async def setup_commands():
    commands = [
        BotCommand(command="start", description="Описание бота"),
        BotCommand(command="info", description="Показать chat_id и thread_id"),
        BotCommand(command="startgame", description="Начать игру и стать ведущим"),
        BotCommand(command="score", description="Показать общий рейтинг"),
        BotCommand(command="top", description="Показать топ-10"),
        BotCommand(command="hint", description="Подсказка (ведущий)"),
        BotCommand(command="addword", description="Добавить слово (админ)"),
        BotCommand(command="say", description="Сообщение от бота (админ)"),
        BotCommand(command="resetgame", description="Сбросить игру (только @yakovlef)"),
    ]
    await bot.set_my_commands(commands)


# ---------- КОМАНДЫ ----------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🐊 Бот для игры в «Крокодила».\n\n"
        "Работает только в заданной теме.\n"
        "Команды:\n"
        "• /startgame — начать игру (становишься ведущим)\n"
        "• /score — общий рейтинг\n"
        "• /top — топ-10 игроков\n"
        "• /hint — подсказка (только ведущий)\n"
        "• /addword слово — добавить слово (админ)\n"
        "• /say текст — отправить сообщение от бота в тему (админ)\n"
        "• /resetgame — полный сброс (только @yakovlef)\n"
        "• /info — показать chat_id и thread_id"
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
        await message.answer("⚠️ Игра уже идёт. Сначала завершите текущую или остановите её кнопкой.")
        return

    words = await load_words()
    candidates = [w for w in words if w not in used_words]

    if not candidates:
        await message.answer("🎉 Слова закончились! Очисти used_words.txt, чтобы начать заново.")
        return

    word = random.choice(candidates)
    used_words.add(word)
    save_used_word(word)

    leader = message.from_user

    game.update(
        active=True,
        word=word,
        leader_id=leader.id,
        attempts=0,
    )

    await message.answer(
        f"🎮 Игра началась!\nВедущий: {mention(leader)}",
        reply_markup=leader_keyboard(leader.id)
    )


@dp.message(Command("score"))
async def cmd_score(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        await message.answer("📊 Рейтинг пуст.")
        return

    sorted_s = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    lines = []
    for i, (uid, pts) in enumerate(sorted_s, start=1):
        try:
            member = await bot.get_chat_member(CHAT_ID, uid)
            u = member.user
            name = f"@{u.username}" if u.username else u.full_name
        except Exception:
            name = f"ID:{uid}"
        lines.append(f"{i}. {name} — {pts}")

    await message.answer("📊 <b>Общий рейтинг:</b>\n" + "\n".join(lines))


@dp.message(Command("top"))
async def cmd_top(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        await message.answer("🏆 Пока нет данных для топа.")
        return

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]

    lines = []
    for i, (uid, pts) in enumerate(rating, start=1):
        try:
            member = await bot.get_chat_member(CHAT_ID, uid)
            u = member.user
            name = f"@{u.username}" if u.username else u.full_name
        except Exception:
            name = f"ID:{uid}"
        lines.append(f"{i}. {name} — {pts}")

    await message.answer("🏆 <b>Топ-10 игроков:</b>\n" + "\n".join(lines))


@dp.message(Command("say"))
async def cmd_say(message: Message):
    # отправка сообщения от бота в тему
    if not await is_admin(message.from_user.id):
        await message.answer("⛔ Только админ может отправлять сообщения от имени бота.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование:\n/say текст сообщения")
        return

    text = parts[1]

    await bot.send_message(
        chat_id=CHAT_ID,
        text=text,
        message_thread_id=THREAD_ID if THREAD_ID != 0 else None
    )

    await message.answer("✅ Отправлено.")


@dp.message(Command("resetgame"))
async def cmd_resetgame(message: Message):
    # полный сброс — только супер-офицер
    if not is_super_user(message.from_user):
        await message.answer("⛔ Только @yakovlef может сбросить игру и рейтинг.")
        return

    game.update(active=False, word=None, leader_id=None, attempts=0)
    scores.clear()
    save_scores(scores)

    await message.answer("♻️ Игра и рейтинг полностью сброшены.")


@dp.message(Command("addword"))
async def cmd_addword(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("⛔ Только администратор чата может добавлять слова.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование:\n/addword слово")
        return

    word = parts[1].strip().lower()

    if len(word) < 3 or not word.isalpha():
        await message.answer("❌ Слово должно быть минимум 3 буквы и только буквы.")
        return

    words = await load_words()
    if word in words:
        await message.answer("⚠️ Такое слово уже есть в словаре.")
        return

    with open("words.txt", "a", encoding="utf-8") as f:
        f.write(word + "\n")

    await message.answer(f"✅ Добавлено слово: <b>{word}</b>")


@dp.message(Command("hint"))
async def cmd_hint(message: Message):
    if not in_target_topic(message):
        return
    if not game["active"] or not game["word"]:
        await message.answer("Сейчас игра не запущена.")
        return

    if message.from_user.id != game["leader_id"] and not is_super_user(message.from_user):
        await message.answer("Подсказку может давать только ведущий.")
        return

    word = game["word"]
    if len(word) <= 2:
        mask = word[0] + " _"
    else:
        mask = word[0] + " " + "_ " * (len(word) - 1)

    await message.answer(
        f"💡 Подсказка:\n"
        f"Слово из {len(word)} букв.\n"
        f"Начинается на <b>{word[0].upper()}</b>\n"
        f"<code>{mask}</code>"
    )


# ---------- CALLBACK-КНОПКИ (только ведущий и @yakovlef) ----------

@dp.callback_query()
async def callbacks(call: CallbackQuery):
    if not call.message or not in_target_topic(call.message):
        return
    if not game["active"] or not game["word"]:
        return

    data = call.data or ""
    if ":" not in data:
        return

    action, leader_id_str = data.split(":", 1)
    try:
        leader_id = int(leader_id_str)
    except ValueError:
        return

    # доступ только ведущему и супер-офицеру
    if call.from_user.id != game["leader_id"] and not is_super_user(call.from_user):
        await call.answer("⛔ Доступ только ведущему и @yakovlef", show_alert=True)
        return

    if action == "show":
        await call.answer(f"Слово: {game['word']}", show_alert=True)
        return

    elif action == "replace":
        words = await load_words()
        candidates = [w for w in words if w not in used_words]

        if not candidates:
            await call.answer("Слова закончились!", show_alert=True)
            return

        new_word = random.choice(candidates)
        used_words.add(new_word)
        save_used_word(new_word)

        game["word"] = new_word
        game["attempts"] = 0

        await call.answer(f"Новое слово: {new_word}", show_alert=True)
        return

    elif action == "stop":
        # остановить игру может только супер-офицер
        if not is_super_user(call.from_user):
            await call.answer("⛔ Остановить игру может только @yakovlef", show_alert=True)
            return

        game.update(active=False, word=None, leader_id=None, attempts=0)
        await call.message.answer("⛔ Игра остановлена.")
        await call.answer("Игра остановлена.")
        return


# ---------- УГАДЫВАНИЕ СЛОВА ----------

@dp.message()
async def on_guess(message: Message):
    if not in_target_topic(message):
        return
    if not game["active"] or not game["word"]:
        return
    if not message.text:
        return
    if message.text.startswith("/"):
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
        await message.answer("🎉 Все слова закончились! Игра остановлена.")
        return

    new_word = random.choice(candidates)
    used_words.add(new_word)
    save_used_word(new_word)

    # передаём ход угадавшему
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
    logger.info("Бот запускается…")
    await setup_commands()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

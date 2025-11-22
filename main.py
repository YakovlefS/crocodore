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

# ============================================================
#                      ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
#                      ENV НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
THREAD_ID = int(os.getenv("THREAD_ID", "0"))

if not BOT_TOKEN:
    raise SystemExit("Не задан BOT_TOKEN")

SUPER_OFFICER = "@yakovlef"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# ============================================================
#                      ФАЙЛЫ
# ============================================================

SCORES_FILE = "scores.json"
USED_WORDS_FILE = "used_words.txt"

# ============================================================
#                      УТИЛИТЫ
# ============================================================

def normalize(text: str) -> str:
    """Нормализация строки: нижний регистр, ё→е, только буквы."""
    t = text.lower().replace("ё", "е")
    return "".join(ch for ch in t if ch.isalpha())


def is_single_root(target_word: str, message_word: str) -> bool:
    """Проверка на однокоренные слова."""
    t = normalize(target_word)
    m = normalize(message_word)

    # слишком короткие слова не считаем
    if len(t) < 3 or len(m) < 3:
        return False

    # берём первые 3–4 буквы — мощнее и точнее
    root_t3 = t[:3]
    root_t4 = t[:4]
    root_m3 = m[:3]
    root_m4 = m[:4]

    return (
        root_t3 in m or root_t4 in m or
        root_m3 in t or root_m4 in t
    )


def mention(user) -> str:
    name = (user.full_name or "Игрок").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def in_target_topic(message: Message) -> bool:
    return (
        message.chat
        and message.chat.id == CHAT_ID
        and getattr(message, "message_thread_id", None) == THREAD_ID
    )


def is_super_user(user) -> bool:
    return user.username and ("@" + user.username.lower() == SUPER_OFFICER)


async def is_admin(uid: int) -> bool:
    try:
        m = await bot.get_chat_member(CHAT_ID, uid)
        return m.status in ("administrator", "creator", "owner")
    except:
        return False

# ============================================================
#                      ФАЙЛЫ СОСТОЯНИЙ
# ============================================================

def load_scores() -> dict[int, int]:
    try:
        with open(SCORES_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return {int(k): int(v) for k, v in d.items()}
    except:
        return {}


def save_scores(scores: dict[int, int]):
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)


def load_used_words() -> set[str]:
    try:
        with open(USED_WORDS_FILE, "r", encoding="utf-8") as f:
            return {w.strip().lower() for w in f if w.strip()}
    except:
        return set()


def save_used_word(word: str):
    with open(USED_WORDS_FILE, "a", encoding="utf-8") as f:
        f.write(word.lower() + "\n")

# ============================================================
#                      СОСТОЯНИЕ ИГРЫ
# ============================================================

game = {
    "active": False,
    "word": None,
    "leader_id": None,
    "attempts": 0,
}

scores = load_scores()
used_words = load_used_words()

# ============================================================
#                      ЗАГРУЗКА СЛОВ
# ============================================================

async def load_words() -> list[str]:
    try:
        with open("words.txt", "r", encoding="utf-8") as f:
            words = [w.strip().lower() for w in f if w.strip()]
            return words or ["кот", "яблоко", "самолет"]
    except:
        return ["кот", "яблоко", "самолет"]

# ============================================================
#                      КНОПКИ
# ============================================================

def leader_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👁 Показать слово", callback_data=f"show:{uid}"),
                InlineKeyboardButton(text="🔄 Новое слово", callback_data=f"replace:{uid}")
            ],
            [
                InlineKeyboardButton(text="⛔ Остановить игру", callback_data=f"stop:{uid}")
            ]
        ]
    )

# ============================================================
#                      КОМАНДЫ
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🐊 Бот «Крокодил».\n\n"
        "Команды:\n"
        "/startgame — начало игры\n"
        "/score — рейтинг\n"
        "/top — топ-10\n"
        "/hint — подсказка\n"
        "/special — спец-слово\n"
        "/addword — новое слово (админ)\n"
        "/addpoints — +очки\n"
        "/removepoints — -очки\n"
        "/say — сообщение от бота\n"
        "/resetgame — сброс\n"
        "/info — служебное"
    )


@dp.message(Command("info"))
async def cmd_info(message: Message):
    await message.answer(
        f"chat_id: <code>{message.chat.id}</code>\n"
        f"thread_id: <code>{getattr(message, 'message_thread_id', None)}</code>"
    )

# ============================================================
#                  НАЧАЛО ИГРЫ
# ============================================================

@dp.message(Command("startgame"))
async def cmd_startgame(message: Message):
    if not in_target_topic(message):
        return

    if game["active"]:
        return await message.answer("⚠ Игра уже идёт!")

    words = await load_words()
    candidates = [w for w in words if w not in used_words]

    if not candidates:
        return await message.answer("🎉 Все слова закончились!")

    word = random.choice(candidates)
    used_words.add(word)
    save_used_word(word)

    leader = message.from_user
    game.update(active=True, word=word, leader_id=leader.id, attempts=0)

    await message.answer(
        f"🎮 Игра началась!\nВедущий: {mention(leader)}",
        reply_markup=leader_keyboard(leader.id),
    )

# ============================================================
#                      RESET GAME
# ============================================================

@dp.message(Command("resetgame"))
async def cmd_resetgame(message: Message):
    if not is_super_user(message.from_user):
        return await message.answer("⛔ Только @yakovlef может сбрасывать игру.")

    game.update(active=False, word=None, leader_id=None, attempts=0)
    scores.clear()
    save_scores(scores)

    await message.answer("♻ Игра и рейтинг сброшены.")

# ============================================================
#                      РЕЙТИНГ
# ============================================================

@dp.message(Command("score"))
async def cmd_score(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        return await message.answer("Рейтинг пуст.")

    lines = []
    for i, (uid, pts) in enumerate(sorted(scores.items(), key=lambda x: x[1], reverse=True), 1):
        try:
            m = await bot.get_chat_member(CHAT_ID, uid)
            name = "@" + m.user.username if m.user.username else m.user.full_name
        except:
            name = f"ID:{uid}"

        lines.append(f"{i}. {name} — {pts}")

    await message.answer("📊 <b>Рейтинг:</b>\n" + "\n".join(lines))

# ============================================================
#                      КОМАНДА say
# ============================================================

@dp.message(Command("say"))
async def cmd_say(message: Message):
    if not await is_admin(message.from_user.id):
        return await message.answer("⛔ Только администратор.")

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("Использование:\n/say текст")

    await bot.send_message(
        CHAT_ID,
        parts[1],
        message_thread_id=THREAD_ID if THREAD_ID else None
    )

    await message.answer("Отправлено.")

# ============================================================
#                      ТОП
# ============================================================

@dp.message(Command("top"))
async def cmd_top(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        return await message.answer("Пусто.")

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]

    lines = []
    for i, (uid, pts) in enumerate(rating, 1):
        try:
            m = await bot.get_chat_member(CHAT_ID, uid)
            name = "@" + m.user.username if m.user.username else m.user.full_name
        except:
            name = f"ID:{uid}"
        lines.append(f"{i}. {name} — {pts}")

    await message.answer("🏆 <b>Топ-10:</b>\n" + "\n".join(lines))

# ============================================================
#                    ПОДСКАЗКА
# ============================================================

@dp.message(Command("hint"))
async def cmd_hint(message: Message):
    if not in_target_topic(message):
        return

    if not game["active"]:
        return await message.answer("Игра не идёт.")

    if message.from_user.id != game["leader_id"] and not is_super_user(message.from_user):
        return await message.answer("Подсказку может дать только ведущий.")

    word = game["word"]
    mask = word[0] + " " + "_ " * (len(word) - 1)

    await message.answer(
        f"💡 Подсказка:\n"
        f"Слово из {len(word)} букв\n"
        f"Начинается на <b>{word[0].upper()}</b>\n"
        f"<code>{mask}</code>"
    )

# ============================================================
#                    ДОБАВЛЕНИЕ СЛОВ
# ============================================================

@dp.message(Command("addword")))
async def cmd_addword(message: Message):
    if not await is_admin(message.from_user.id):
        return await message.answer("⛔ Только админ.")

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("/addword слово")

    w = parts[1].strip().lower()
    if len(w) < 3 or not w.isalpha():
        return await message.answer("Слово ≥ 3 буквы.")

    words = await load_words()
    if w in words:
        return await message.answer("Уже есть.")

    with open("words.txt", "a", encoding="utf-8") as f:
        f.write(w + "\n")

    await message.answer(f"Добавлено: <b>{w}</b>")

# ============================================================
#                     СПЕЦ-СЛОВО
# ============================================================

SPECIAL_ACTIVE = False
SPECIAL_WORD = None

@dp.message(Command("special")))
async def cmd_special(message: Message):
    if message.chat.type != "private":
        return await message.answer("Только в личке.")

    if not is_super_user(message.from_user):
        return await message.answer("⛔ Только @yakovlef")

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("/special слово")

    global SPECIAL_ACTIVE, SPECIAL_WORD
    SPECIAL_WORD = normalize(parts[1].strip())
    SPECIAL_ACTIVE = True

    await bot.send_message(
        CHAT_ID,
        "🔮 <b>Спец-раунд!</b>\nУгадайте слово!",
        message_thread_id=THREAD_ID
    )

    await message.answer("Спец-слово запущено.")

@dp.message()
async def handle_special(message: Message):
    global SPECIAL_ACTIVE, SPECIAL_WORD

    if not SPECIAL_ACTIVE:
        return
    if not message.text:
        return
    if message.chat.id != CHAT_ID:
        return
    if getattr(message, "message_thread_id", None) != THREAD_ID:
        return

    if normalize(message.text) == SPECIAL_WORD:
        uid = message.from_user.id
        scores[uid] = scores.get(uid, 0) + 10
        save_scores(scores)

        await message.answer(
            f"🎉 {mention(message.from_user)} угадал спец-слово <b>{SPECIAL_WORD}</b>! +10 очков!"
        )

        SPECIAL_ACTIVE = False
        SPECIAL_WORD = None

# ============================================================
#                      CALLBACKS
# ============================================================

@dp.callback_query()
async def callbacks(call: CallbackQuery):
    if not call.message:
        return
    if not in_target_topic(call.message):
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

    # только ведущий или супер-офицер
    if call.from_user.id != game["leader_id"] and not is_super_user(call.from_user):
        return await call.answer("⛔ Только ведущий.", show_alert=True)

    if action == "show":
        return await call.answer(f"Слово: {game['word']}", show_alert=True)

    if action == "replace":
        words = await load_words()
        candidates = [w for w in words if w not in used_words]
        if not candidates:
            return await call.answer("Слова закончились!", show_alert=True)

        nw = random.choice(candidates)
        used_words.add(nw)
        save_used_word(nw)
        game["word"] = nw

        return await call.answer(f"Новое слово: {nw}", show_alert=True)

    if action == "stop":
        if not is_super_user(call.from_user):
            return await call.answer("⛔ Только @yakovlef", show_alert=True)

        game.update(active=False, word=None, leader_id=None, attempts=0)
        await call.message.answer("Игра остановлена.")
        return await call.answer("Остановлено.")

# ============================================================
#               ОСНОВНАЯ ЛОГИКА УГАДЫВАНИЯ
# ============================================================

@dp.message()
async def on_guess(message: Message):
    # спец-слово обрабатывается отдельным хендлером
    if SPECIAL_ACTIVE:
        return

    if not in_target_topic(message):
        return
    if not game["active"]:
        return
    if not message.text:
        return
    if message.text.startswith("/"):
        return

    # ---------- ШТРАФ ВЕДУЩЕМУ ----------
    if message.from_user.id == game["leader_id"]:
        if is_single_root(game["word"], message.text):
            lid = game["leader_id"]
            scores[lid] = scores.get(lid, 0) - 1
            save_scores(scores)

            await message.answer(
                f"⚠️ Штраф ведущему!\n"
                f"Однокоренное слово к <b>{game['word']}</b>\n"
                f"Очки: {scores[lid]}"
            )
        return

    # ---------- ПРОВЕРКА ----------
    guess = normalize(message.text)
    answer = normalize(game["word"])

    if guess != answer:
        return

    # ---------- УГАДАЛ ----------
    uid = message.from_user.id
    scores[uid] = scores.get(uid, 0) + 1
    save_scores(scores)

    await message.answer(
        f"🎉 {mention(message.from_user)} угадал слово <b>{game['word']}</b>! +1"
    )

    # ---------- НОВОЕ СЛОВО ----------
    words = await load_words()
    candidates = [w for w in words if w not in used_words]

    if not candidates:
        game["active"] = False
        return await message.answer("🎉 Все слова закончились. Игра окончена.")

    new_word = random.choice(candidates)
    used_words.add(new_word)
    save_used_word(new_word)

    game.update(leader_id=uid, word=new_word, attempts=0)

    await message.answer(
        f"👉 Новый ведущий: {mention(message.from_user)}",
        reply_markup=leader_keyboard(uid),
    )

# ============================================================
#                      ЗАПУСК БОТА
# ============================================================

async def main():
    logger.info("Бот запускается…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

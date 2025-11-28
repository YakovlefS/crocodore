import os
import json
import logging
import random
import asyncio
from datetime import datetime, date, time, timedelta

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

# =========================================================
#                    НАСТРОЙКИ / ENV
# =========================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
THREAD_ID = int(os.getenv("THREAD_ID", "0"))

if not BOT_TOKEN:
    raise SystemExit("Не задан BOT_TOKEN")

SUPER_OFFICER_USERNAME = "@yakovlef"
SUPER_OFFICER_ID = None

# =========================================================
#                    ХРАНИЛИЩЕ НА RAILWAY
# =========================================================
DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

WORDS_FILE = f"{DATA_DIR}/words.txt"
USED_WORDS_FILE = f"{DATA_DIR}/used_words.txt"
SCORES_FILE = f"{DATA_DIR}/scores.json"
STATS_FILE = f"{DATA_DIR}/stats.json"
GUESSED_WORDS_FILE = f"{DATA_DIR}/guessed_words.txt"
MISSED_WORDS_FILE = f"{DATA_DIR}/missed_words.txt"
DAILY_STATS_FILE = f"{DATA_DIR}/daily_stats.json"


INACTIVITY_HOURS = 3

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

# =========================================================
#                    ХРАНИЛИЩА / ФАЙЛЫ
# =========================================================
def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_scores() -> dict[int, int]:
    raw = load_json(SCORES_FILE, {})
    try:
        return {int(k): int(v) for k, v in raw.items()}
    except:
        return {}

def save_scores(scores: dict[int, int]):
    save_json(SCORES_FILE, {str(k): v for k, v in scores.items()})

def load_used_words() -> set[str]:
    try:
        with open(USED_WORDS_FILE, "r", encoding="utf-8") as f:
            return {w.strip().lower() for w in f if w.strip()}
    except:
        return set()

def save_used_word(word: str):
    with open(USED_WORDS_FILE, "a", encoding="utf-8") as f:
        f.write(word.lower() + "\n")

def load_words_list() -> list[str]:
    try:
        with open(WORDS_FILE, "r", encoding="utf-8") as f:
            words = [w.strip().lower() for w in f if w.strip()]
        if not words:
            raise ValueError("Пустой words.txt")
        return words
    except:
        return ["яблоко", "кошка", "самолет", "дерево", "лампа"]

def load_stats():
    stats = load_json(STATS_FILE, {
        "total_guessed": 0,
        "today_guessed": 0,
        "today_date": str(date.today())
    })
    if stats.get("today_date") != str(date.today()):
        stats["today_date"] = str(date.today())
        stats["today_guessed"] = 0
    return stats

def save_stats(stats):
    save_json(STATS_FILE, stats)

scores: dict[int, int] = load_scores()
used_words: set[str] = load_used_words()
stats = load_stats()

# ============================================================
#            ЛОГИРОВАНИЕ УГАДАННЫХ / ПРОПУЩЕННЫХ
# ============================================================
GUESSED_WORDS_FILE = "guessed_words.txt"
MISSED_WORDS_FILE = "missed_words.txt"
DAILY_STATS_FILE = "daily_stats.json"

def load_daily_stats() -> dict:
    try:
        with open(DAILY_STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_daily_stats(stats: dict):
    with open(DAILY_STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

daily_stats = load_daily_stats()

def log_guessed(uid: int, word: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(GUESSED_WORDS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{ts} | {uid} | {word}\n")

    today = datetime.now().strftime("%Y-%m-%d")
    daily_stats.setdefault(today, {})
    daily_stats[today][str(uid)] = daily_stats[today].get(str(uid), 0) + 1
    save_daily_stats(daily_stats)

def log_missed(word: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(MISSED_WORDS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{ts} | {word}\n")

# ============================================================
#            ЕЖЕДНЕВНАЯ СТАТИСТИКА
# ============================================================
async def send_daily_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    if today not in daily_stats:
        return

    lines = []
    for uid, count in daily_stats[today].items():
        try:
            m = await bot.get_chat_member(CHAT_ID, int(uid))
            name = "@" + m.user.username if m.user.username else m.user.full_name
        except:
            name = f"ID:{uid}"

        lines.append(f"{name} — {count}")

    if lines:
        text = (
            f"📊 <b>Статистика за день</b>\nДата: {today}\n\n" +
            "\n".join(lines)
        )
        await bot.send_message(CHAT_ID, text)

    del daily_stats[today]
    save_daily_stats(daily_stats)

async def daily_scheduler():
    while True:
        now = datetime.now()
        target = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if now > target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        try:
            await send_daily_stats()
        except Exception as e:
            logger.error(f"Ошибка ежедневной статистики: {e}")

# =========================================================
#                     СОСТОЯНИЕ ИГРЫ
# =========================================================
game = {
    "active": False,
    "word": None,
    "leader_id": None,
    "attempts": 0,
    "special": False,
    "special_reward": 10,
}

last_activity_ts = datetime.now()

# =========================================================
#                     ВСПОМОГАТЕЛЬНОЕ
# =========================================================
def normalize(text: str) -> str:
    t = text.lower().replace("ё", "е")
    return "".join(ch for ch in t if ch.isalpha())

def mention_html(user) -> str:
    name = (user.full_name or "игрок").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user.id}">{name}</a>'

# ============================================================
#             ПОИСК ПОЛЬЗОВАТЕЛЯ ПО @username / ID / reply
# ============================================================
async def resolve_user(reference: str | None, message: Message):
    """
    Ищет пользователя по:
    - reply
    - @username
    - числовому user_id
    """

    # 1) Reply
    if (not reference) and message.reply_to_message:
        return message.reply_to_message.from_user

    if not reference:
        return None

    ref = reference.strip().replace("@", "")

    # 2) Если число — сразу user_id
    if ref.isdigit():
        try:
            m = await bot.get_chat_member(message.chat.id, int(ref))
            return m.user
        except:
            return None

    # 3) Пробуем username через get_chat
    try:
        chat = await bot.get_chat(f"@{ref}")
        if chat:
            return chat
    except:
        pass

    # 4) Последняя попытка — поиск среди последних 200 сообщений
    try:
        async for m in bot.get_chat_history(message.chat.id, limit=200):
            u = m.from_user
            if u.username and u.username.lower() == ref.lower():
                return u
    except:
        pass

    return None

def in_target_topic(message: Message) -> bool:
    if not message.chat or message.chat.id != CHAT_ID:
        return False
    if THREAD_ID == 0:
        return True
    return getattr(message, "message_thread_id", None) == THREAD_ID

def is_super_by_username(username: str | None) -> bool:
    if not username:
        return False
    return ("@" + username.lower()) == SUPER_OFFICER_USERNAME.lower()

def is_super(user_obj) -> bool:
    username = user_obj.from_user.username
    return is_super_by_username(username)

async def is_admin(user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(CHAT_ID, user_id)
        return m.status in ("administrator", "creator", "owner")
    except:
        return False

async def maybe_delete_command(message: Message):
    try:
        if in_target_topic(message) and message.text and message.text.startswith("/"):
            await message.delete()
    except:
        pass

def leader_keyboard(leader_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👁 Показать слово", callback_data=f"show:{leader_id}"),
                InlineKeyboardButton(text="🔄 Сменить слово", callback_data=f"replace:{leader_id}")
            ],
            [
                InlineKeyboardButton(text="💡 Подсказка", callback_data=f"hint:{leader_id}")
            ]
        ]
    )

def pick_new_word(words: list[str]) -> str | None:
    candidates = [w for w in words if w not in used_words]
    if not candidates:
        return None
    w = random.choice(candidates)
    used_words.add(w)
    save_used_word(w)
    return w

def update_activity():
    global last_activity_ts
    last_activity_ts = datetime.now()

def detect_root_violation(leader_text: str, answer: str) -> bool:
    ans = normalize(answer)
    tokens = [normalize(x) for x in leader_text.split()]
    tokens = [t for t in tokens if len(t) >= 4]

    for t in tokens:
        if t == ans or t in ans or ans in t:
            return True
        pref = 0
        for a_ch, b_ch in zip(t, ans):
            if a_ch == b_ch:
                pref += 1
            else:
                break
        if pref >= 4:
            return True
    return False

def achievement_for(score: int) -> str | None:
    milestones = [
        (5, "🥉 Новичок-Угадчик"),
        (10, "🥈 Уверенный Игрок"),
        (25, "🥇 Мастер Крокодила"),
        (50, "🏅 Легенда Гильдии"),
        (100, "🏆 Абсолютный Чемпион"),
    ]
    for m, title in milestones:
        if score == m:
            return title
    return None

async def setup_commands():
    commands = [
        BotCommand(command="startgame", description="Начать игру (становишься ведущим)"),
        BotCommand(command="restartgame", description="Перезапуск игры"),
        BotCommand(command="score", description="Рейтинг"),
        BotCommand(command="top", description="Топ-10"),
    ]
    await bot.set_my_commands(commands)

# =========================================================
#                       КОМАНДЫ
# =========================================================
@dp.message(Command("info"))
async def cmd_info(message: Message):
    update_activity()
    global SUPER_OFFICER_ID
    if is_super(message):
        SUPER_OFFICER_ID = message.from_user.id

    await message.answer(
        f"{mention_html(message.from_user)}, вот параметры:\n"
        f"<b>chat_id:</b> <code>{message.chat.id}</code>\n"
        f"<b>thread_id:</b> <code>{getattr(message,'message_thread_id',None)}</code>"
    )
    await maybe_delete_command(message)

@dp.message(Command("startgame"))
async def cmd_startgame(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    global SUPER_OFFICER_ID
    if is_super(message):
        SUPER_OFFICER_ID = message.from_user.id

    if game["active"]:
        await message.answer(f"{mention_html(message.from_user)}, игра уже идёт.")
        await maybe_delete_command(message)
        return

    words = load_words_list()
    w = pick_new_word(words)
    if not w:
        await message.answer("🎉 Все слова использованы! Очисти used_words.txt.")
        await maybe_delete_command(message)
        return

    game.update(
        active=True,
        word=w,
        leader_id=message.from_user.id,
        attempts=0,
        special=False
    )

    await message.answer(
        f"🎮 Игра началась!\nВедущий: {mention_html(message.from_user)}",
        reply_markup=leader_keyboard(message.from_user.id)
    )
    await maybe_delete_command(message)

@dp.message(Command("restartgame"))
async def cmd_restartgame(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not (is_super(message) or await is_admin(message.from_user.id)):
        await message.answer(f"{mention_html(message.from_user)}, перезапуск доступен только @yakovlef или админам.")
        await maybe_delete_command(message)
        return

    words = load_words_list()
    w = pick_new_word(words)
    if not w:
        await message.answer("🎉 Все слова использованы — перезапуск невозможен.")
        await maybe_delete_command(message)
        return

    game.update(
        active=True,
        word=w,
        leader_id=message.from_user.id,
        attempts=0,
        special=False
    )

    await message.answer(
        f"♻️ Игра перезапущена!\nНовый ведущий: {mention_html(message.from_user)}",
        reply_markup=leader_keyboard(message.from_user.id)
    )
    await maybe_delete_command(message)

# ============================================================
#              СПЕЦ-СЛОВО
# ============================================================
SPECIAL = {
    "active": False,
    "word": None,
    "points": 10
}

def normalize_e(text: str) -> str:
    return text.lower().replace("ё", "е")

def is_superuser(user):
    return user.username and ("@" + user.username.lower()) == "@yakovlef"

@dp.message(Command("special"))
async def cmd_special(message: Message):
    if not is_superuser(message.from_user):
        return await message.answer("⛔ Только @yakovlef может задавать специальное слово.")

    parts = message.text.split()
    if len(parts) < 2:
        return await message.answer("Использование:\n/special слово [очки]")

    word = parts[1].strip()
    points = 10

    if len(parts) >= 3 and parts[2].isdigit():
        points = int(parts[2])

    SPECIAL["active"] = True
    SPECIAL["word"] = normalize_e(word)
    SPECIAL["points"] = points

    await message.answer(
        f"⭐ Специальное слово установлено!\n"
        f"🔤 Слово: <b>{word}</b>\n"
        f"🏆 Награда: <b>{points}</b> очков."
    )

async def check_special_word(message: Message, guess: str):
    if not SPECIAL["active"]:
        return False

    if normalize_e(guess) == SPECIAL["word"]:
        uid = message.from_user.id
        scores[uid] = scores.get(uid, 0) + SPECIAL["points"]
        save_scores(scores)

        await message.answer(
            f"🌟 {mention_html(message.from_user)} угадал специальное слово!\n"
            f"Получено: <b>{SPECIAL['points']}</b> очков!"
        )

        SPECIAL["active"] = False
        SPECIAL["word"] = None
        return True

    return False

# ============================================================
#              ПЕРЕДАЧА ХОДА
# ============================================================
@dp.message(Command("passlead"))
async def cmd_passlead(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not is_super(message):
        await message.answer("⛔ Передавать ход может только @yakovlef.")
        await maybe_delete_command(message)
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование:\n/passlead @username (или ответом на сообщение)")
        await maybe_delete_command(message)
        return

    ref = parts[1].strip()
    new_leader = await resolve_user(ref, message)

    if not new_leader:
        await message.answer("❌ Пользователь не найден. Попробуй:\n• написать /passlead @username\n• или ответом на сообщение игрока.")
        await maybe_delete_command(message)
        return

    if not game["active"]:
        await message.answer("⚠️ Игра не идёт.")
        await maybe_delete_command(message)
        return

    game["leader_id"] = new_leader.id

    await message.answer(
        f"🎯 Ход передан: {mention_html(new_leader)}",
        reply_markup=leader_keyboard(new_leader.id)
    )
    await maybe_delete_command(message)

# ============================================================
#                     ПОДСКАЗКА
# ============================================================
@dp.message(Command("hint"))
async def cmd_hint(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not game["active"] or not game["word"]:
        await message.answer("Игра не запущена.")
        await maybe_delete_command(message)
        return

    if message.from_user.id != game["leader_id"]:
        await message.answer("⛔ Подсказку может давать только ведущий.")
        await maybe_delete_command(message)
        return

    word = game["word"]
    n = len(normalize(word))
    mask = normalize(word)[0] + " " + "_ " * (n - 1)

    await message.answer(
        f"💡 Подсказка:\n"
        f"Слово из {n} букв.\n"
        f"Начинается на <b>{normalize(word)[0].upper()}</b>\n"
        f"<code>{mask}</code>"
    )
    await maybe_delete_command(message)

# ============================================================
#                     ДОБАВЛЕНИЕ СЛОВА
# ============================================================
@dp.message(Command("addword"))
async def cmd_addword(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not await is_admin(message.from_user.id):
        await message.answer(f"{mention_html(message.from_user)}, добавлять слова может только админ.")
        await maybe_delete_command(message)
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование:\n/addword слово")
        await maybe_delete_command(message)
        return

    w = parts[1].strip().lower()
    if len(normalize(w)) < 4 or not normalize(w).isalpha():
        await message.answer("❌ Слово должно быть минимум 4 буквы.")
        await maybe_delete_command(message)
        return

    words = load_words_list()
    if w in words:
        await message.answer("⚠️ Такое слово уже есть.")
        await maybe_delete_command(message)
        return

    with open(WORDS_FILE, "a", encoding="utf-8") as f:
        f.write(w + "\n")

    await message.answer(f"✅ Добавлено слово: <b>{w}</b>")
    await maybe_delete_command(message)

# ============================================================
#                     SAY (ОТ АДМИНА)
# ============================================================
@dp.message(Command("say"))
async def cmd_say(message: Message):
    update_activity()
    if not await is_admin(message.from_user.id):
        await message.answer(f"{mention_html(message.from_user)}, /say доступна только админам.")
        await maybe_delete_command(message)
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование:\n/say текст")
        await maybe_delete_command(message)
        return

    await bot.send_message(
        chat_id=CHAT_ID,
        message_thread_id=THREAD_ID if THREAD_ID != 0 else None,
        text=parts[1]
    )

    await message.answer("✅ Сообщение отправлено.")
    await maybe_delete_command(message)

# ============================================================
#                ДОБАВЛЕНИЕ / СНЯТИЕ ОЧКОВ
# ============================================================
@dp.message(Command("addpoints"))
async def cmd_addpoints(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not is_super(message):
        await message.answer("⛔ Только @yakovlef.")
        await maybe_delete_command(message)
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Использование:\n/addpoints @user N (или ответом на сообщение)")
        await maybe_delete_command(message)
        return

    ref = parts[1]
    user = await resolve_user(ref, message)
    if not user:
        await message.answer("❌ Пользователь не найден. Укажи @username или ответь на сообщение игрока.")
        await maybe_delete_command(message)
        return

    try:
        n = int(parts[2])
    except:
        await message.answer("❌ N должно быть числом.")
        await maybe_delete_command(message)
        return

    scores[user.id] = scores.get(user.id, 0) + n
    save_scores(scores)

    await message.answer(f"✅ {mention_html(user)} получил {n} очк(а). Теперь: {scores[user.id]}")
    await maybe_delete_command(message)

@dp.message(Command("delpoints"))
async def cmd_delpoints(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not is_super(message):
        await message.answer("⛔ Только @yakovlef.")
        await maybe_delete_command(message)
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Использование:\n/delpoints @user N (или ответом на сообщение)")
        await maybe_delete_command(message)
        return

    ref = parts[1]
    user = await resolve_user(ref, message)
    if not user:
        await message.answer("❌ Пользователь не найден. Укажи @username или ответь на сообщение игрока.")
        await maybe_delete_command(message)
        return

    try:
        n = int(parts[2])
    except:
        await message.answer("❌ N должно быть числом.")
        await maybe_delete_command(message)
        return

    scores[user.id] = max(0, scores.get(user.id, 0) - n)
    save_scores(scores)

    await message.answer(f"✅ У {mention_html(user)} снято {n} очк(а). Теперь: {scores[user.id]}")
    await maybe_delete_command(message)

# ============================================================
#                     ПОЛНЫЙ СБРОС
# ============================================================
@dp.message(Command("resetgame"))
async def cmd_resetgame(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not is_super(message):
        await message.answer("⛔ Только @yakovlef.")
        await maybe_delete_command(message)
        return

    game.update(active=False, word=None, leader_id=None, attempts=0, special=False)
    scores.clear()
    save_scores(scores)

    await message.answer("♻️ Игра и рейтинг сброшены.")
    await maybe_delete_command(message)

# ============================================================
#                     РЕЙТИНГИ
# ============================================================
@dp.message(Command("score"))
async def cmd_score(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not scores:
        await message.answer("📊 Рейтинг пуст.")
        await maybe_delete_command(message)
        return

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    lines = []
    medals = ["🥇", "🥈", "🥉"]

    for i, (uid, pts) in enumerate(rating, 1):
        try:
            m = await bot.get_chat_member(CHAT_ID, uid)
            u = m.user
            name = f"@{u.username}" if u.username else u.full_name
        except:
            name = f"ID:{uid}"

        medal = medals[i-1] if i <= 3 else "•"
        lines.append(f"{medal} {i}. <b>{name}</b> — {pts}")

    await message.answer("📊 <b>Рейтинг:</b>\n" + "\n".join(lines))
    await maybe_delete_command(message)

@dp.message(Command("top"))
async def cmd_top(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not scores:
        await message.answer("🏆 Пока нет данных.")
        await maybe_delete_command(message)
        return

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
    lines = []
    medals = ["🥇", "🥈", "🥉"]

    for i, (uid, pts) in enumerate(rating, 1):
        try:
            m = await bot.get_chat_member(CHAT_ID, uid)
            u = m.user
            name = f"@{u.username}" if u.username else u.full_name
        except:
            name = f"ID:{uid}"

        medal = medals[i-1] if i <= 3 else "•"
        lines.append(f"{medal} {i}. <b>{name}</b> — {pts}")

    await message.answer("🏆 <b>Топ-10 игроков:</b>\n" + "\n".join(lines))
    await maybe_delete_command(message)

# ============================================================
#           CALLBACK-КНОПКИ (показ/замена/подсказка)
# ============================================================
@dp.callback_query()
async def callbacks(call: CallbackQuery):
    if not call.message or not in_target_topic(call.message):
        return

    if not game["active"] or not game["leader_id"]:
        await call.answer("Игра сейчас не запущена.", show_alert=True)
        return

    data = call.data or ""
    if ":" not in data:
        return

    action, leader_id_str = data.split(":", 1)
    try:
        leader_id = int(leader_id_str)
    except:
        return

    allowed = (call.from_user.id == game["leader_id"]) or is_super(call)
    if not allowed or leader_id != game["leader_id"]:
        return await call.answer("⛔ Только ведущий", show_alert=True)

    # ------------------ SHOW ------------------
    if action == "show":
        return await call.answer(f"Слово: {game['word']}", show_alert=True)

    # ------------------ REPLACE ------------------
    if action == "replace":
        words = load_words_list()
        w = pick_new_word(words)
        if not w:
            return await call.answer("Слова закончились!", show_alert=True)

        game["word"] = w
        game["attempts"] = 0
        return await call.answer(f"Новое слово: {w}", show_alert=True)

    # ------------------ HINT ------------------
    if action == "hint":
        word = game["word"]
        n = len(word)
        mask = word[0] + " " + "_ " * (n - 1)

        await call.message.answer(
            f"💡 Подсказка:\n"
            f"Слово из {n} букв.\n"
            f"Начинается на <b>{word[0].upper()}</b>\n"
            f"<code>{mask}</code>"
        )
        return await call.answer("Подсказка отправлена!")

    # ------------------ PASS ------------------
    if action == "pass":
        await call.message.answer("Чтобы передать ход:\n/passlead @username")
        return await call.answer()

    # ------------------ STOP ------------------
    if action == "stop":
        if not is_super(call):
            return await call.answer("⛔ Только @yakovlef.", show_alert=True)

        game.update(active=False, word=None, leader_id=None, attempts=0, special=False)
        await call.message.answer("⛔ Игра остановлена.")
        return await call.answer("Остановлено.")

# ============================================================
#                   ГЛАВНЫЙ GAME LOOP
# ============================================================
@dp.message()
async def on_guess(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not game["active"] or not game["word"]:
        return
    if not message.text:
        return

    # ведущий — проверка подсказок, штраф
    if message.from_user.id == game["leader_id"]:
        if detect_root_violation(message.text, game["word"]):
            lid = game["leader_id"]
            scores[lid] = max(0, scores.get(lid, 0) - 1)
            save_scores(scores)

            await message.answer(
                f"⚠️ {mention_html(message.from_user)}, штраф -1 очко за подсказку."
            )
        return

    # спец-слово
    if await check_special_word(message, message.text):
        return

    guess = normalize(message.text)
    answer = normalize(game["word"])

    if not guess:
        return

    if guess != answer and answer not in guess:
        game["attempts"] += 1
        return

    # УГАДАНО!
    user = message.from_user
    uid = user.id

    reward = game["special_reward"] if game["special"] else 1
    scores[uid] = scores.get(uid, 0) + reward
    save_scores(scores)

    stats["total_guessed"] += 1
    stats["today_guessed"] += 1
    stats["today_date"] = str(date.today())
    save_stats(stats)

    ach = achievement_for(scores[uid])
    praise = random.choice([
        "Красавчик! 😎",
        "Вот это скорость! 🔥",
        "Гениально! 🧠",
        "Супер-угадчик! 🐊",
        "Легчайше! 💪"
    ])

    text = (
        f"🎉 {mention_html(user)} угадал слово <b>{game['word']}</b>!\n"
        f"{praise}\n"
        f"💎 +{reward} очк(а). Теперь: <b>{scores[uid]}</b>"
    )
    if ach:
        text += f"\n🏅 Ачивка получена: {ach}"

    await message.answer(text)

    if game["special"]:
        game.update(active=False, word=None, leader_id=None, attempts=0, special=False)
        return await message.answer("⭐ Спец-раунд завершён! Жми /startgame.")

    words = load_words_list()
    new_word = pick_new_word(words)

    if not new_word:
        game.update(active=False, word=None, leader_id=None, attempts=0, special=False)
        return await message.answer("🎉 Все слова закончились! Игра остановлена.")

    game.update(
        leader_id=uid,
        word=new_word,
        attempts=0
    )

    await message.answer(
        f"👉 Новый ведущий: {mention_html(user)}",
        reply_markup=leader_keyboard(uid)
    )

# ============================================================
#                   BACKGROUND TASKS
# ============================================================
async def daily_report_loop():
    global SUPER_OFFICER_ID
    while True:
        try:
            now = datetime.now()
            target = datetime.combine(now.date(), time(21, 0))
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())

            stats_local = load_stats()
            if SUPER_OFFICER_ID:
                await bot.send_message(
                    chat_id=SUPER_OFFICER_ID,
                    text=f"📌 За сегодня угадано: <b>{stats_local.get('today_guessed',0)}</b>"
                )

            stats_local["today_guessed"] = 0
            stats_local["today_date"] = str(date.today())
            save_stats(stats_local)
        except:
            await asyncio.sleep(60)

async def inactivity_loop():
    global last_activity_ts
    while True:
        await asyncio.sleep(60)
        try:
            if game["active"]:
                continue
            diff = datetime.now() - last_activity_ts
            if diff.total_seconds() >= INACTIVITY_HOURS * 3600:
                await bot.send_message(
                    chat_id=CHAT_ID,
                    message_thread_id=THREAD_ID if THREAD_ID != 0 else None,
                    text="🐊 Давно не играли! Жми /startgame 😄"
                )
                last_activity_ts = datetime.now()
        except:
            await asyncio.sleep(60)

# ============================================================
#                          ЗАПУСК
# ============================================================
async def main():
    logger.info("✅ Бот запущен")
    await setup_commands()

    asyncio.create_task(daily_report_loop())
    asyncio.create_task(inactivity_loop())

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())

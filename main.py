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
CHAT_ID = int(os.getenv("CHAT_ID", "0"))        # id группы
THREAD_ID = int(os.getenv("THREAD_ID", "0"))    # id нужной темы (message_thread_id)

if not BOT_TOKEN:
    raise SystemExit("Не задан BOT_TOKEN")

SUPER_OFFICER_USERNAME = "@yakovlef"  # единственный супер-офицер
SUPER_OFFICER_ID = None              # запомним id при первом обращении

WORDS_FILE = "words.txt"
USED_WORDS_FILE = "used_words.txt"
SCORES_FILE = "scores.json"
STATS_FILE = "stats.json"

INACTIVITY_HOURS = 3   # через сколько часов бездействия предложить сыграть

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
    # если день сменился — обнуляем today
    if stats.get("today_date") != str(date.today()):
        stats["today_date"] = str(date.today())
        stats["today_guessed"] = 0
    return stats

def save_stats(stats):
    save_json(STATS_FILE, stats)

scores: dict[int, int] = load_scores()
used_words: set[str] = load_used_words()
stats = load_stats()

# =========================================================
#                      СОСТОЯНИЕ ИГРЫ
# =========================================================
game = {
    "active": False,
    "word": None,
    "leader_id": None,
    "attempts": 0,
    "special": False,         # спец-раунд?
    "special_reward": 10,     # награда за спец-слово
}

last_activity_ts = datetime.now()

# =========================================================
#                       ВСПОМОГАТЕЛЬНОЕ
# =========================================================
def normalize(text: str) -> str:
    """Нормализация: нижний регистр, ё→е, только буквы."""
    t = text.lower().replace("ё", "е")
    return "".join(ch for ch in t if ch.isalpha())

def mention_html(user) -> str:
    name = (user.full_name or "игрок").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user.id}">{name}</a>'

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
    """Удаляем команды из темы, если есть права."""
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
                InlineKeyboardButton(text="🔄 Сменить слово", callback_data=f"replace:{leader_id}"),
            ],
            [
                InlineKeyboardButton(text="🎯 Передать ход", callback_data=f"pass:{leader_id}"),
            ],
            [
                InlineKeyboardButton(text="⛔ Остановить игру", callback_data=f"stop:{leader_id}"),
            ]
        ]
    )

def pick_new_word(words: list[str]) -> str | None:
    """Берём новое слово без повторов."""
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
    """
    Простейшая проверка однокоренности:
    - любое слово из сообщения ведущего длиной >=4
      если оно равно ответу / входит в ответ / имеет общий префикс >=4.
    """
    ans = normalize(answer)
    if not ans:
        return False
    tokens = [normalize(x) for x in leader_text.split()]
    tokens = [t for t in tokens if len(t) >= 4]
    for t in tokens:
        if t == ans:
            return True
        if t in ans or ans in t:
            return True
        # общий префикс >=4
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
    """Простые ачивки."""
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
        BotCommand(command="restartgame", description="Перезапустить игру (супер/админ)"),
        BotCommand(command="score", description="Полный рейтинг"),
        BotCommand(command="top", description="Топ-10"),
        BotCommand(command="addword", description="Добавить слово (админ)"),
        BotCommand(command="say", description="Сказать от имени бота (админ)"),
        BotCommand(command="special", description="Спец-слово (только @yakovlef)"),
        BotCommand(command="addpoints", description="Добавить очки (только @yakovlef)"),
        BotCommand(command="delpoints", description="Убрать очки (только @yakovlef)"),
        BotCommand(command="passlead", description="Передать ход (только @yakovlef)"),
        BotCommand(command="hint", description="Подсказка (ведущий)"),
        BotCommand(command="resetgame", description="Сброс игры и рейтинга (только @yakovlef)"),
        BotCommand(command="info", description="Показать chat_id / thread_id"),
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
        f"🎮 Игра началась!\n"
        f"Ведущий: {mention_html(message.from_user)}",
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
        f"♻️ Игра перезапущена!\n"
        f"Новый ведущий: {mention_html(message.from_user)}",
        reply_markup=leader_keyboard(message.from_user.id)
    )
    await maybe_delete_command(message)

@dp.message(Command("special"))
async def cmd_special(message: Message):
    """
    Спец-слово от @yakovlef:
    /special слово
    - можно вызвать в ЛС боту или в теме
    - ведущий на спец-слове всегда @yakovlef
    - за угадывание +10 очков
    """
    update_activity()
    global SUPER_OFFICER_ID

    if not is_super(message):
        await message.answer(f"{mention_html(message.from_user)}, спец-слово может задать только @yakovlef.")
        await maybe_delete_command(message)
        return

    SUPER_OFFICER_ID = message.from_user.id

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование:\n/special <слово>")
        await maybe_delete_command(message)
        return

    special_word = parts[1].strip().lower()
    if len(normalize(special_word)) < 4:
        await message.answer("❌ Спец-слово должно быть минимум 4 буквы.")
        await maybe_delete_command(message)
        return

    # спец-слово не пишем в used_words — оно отдельное
    game.update(
        active=True,
        word=special_word,
        leader_id=message.from_user.id,
        attempts=0,
        special=True,
        special_reward=10
    )

    # отправляем в тему уведомление
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            message_thread_id=THREAD_ID if THREAD_ID != 0 else None,
            text="⭐ Запущен <b>спец-раунд</b> от @yakovlef! Угадай слово — получишь +10 очков!",
            reply_markup=leader_keyboard(message.from_user.id)
        )
    except:
        pass

    await message.answer("✅ Спец-слово установлено и отправлено в тему.")
    await maybe_delete_command(message)

@dp.message(Command("passlead"))
async def cmd_passlead(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not is_super(message):
        await message.answer("⛔ Передавать ход может только @yakovlef.")
        await maybe_delete_command(message)
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование:\n/passlead @username")
        await maybe_delete_command(message)
        return

    target = parts[1].lower()
    try:
        member = await bot.get_chat_member(CHAT_ID, target)
        new_leader = member.user
    except:
        await message.answer("❌ Пользователь не найден в группе.")
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

@dp.message(Command("hint"))
async def cmd_hint(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not game["active"] or not game["word"]:
        await message.answer("Сейчас игра не запущена.")
        await maybe_delete_command(message)
        return

    if message.from_user.id != game["leader_id"]:
        await message.answer(f"{mention_html(message.from_user)}, подсказку может давать только ведущий.")
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
        await message.answer("❌ Слово должно быть реальным и минимум 4 буквы.")
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

    text_to_send = parts[1]
    await bot.send_message(
        chat_id=CHAT_ID,
        message_thread_id=THREAD_ID if THREAD_ID != 0 else None,
        text=text_to_send
    )
    await message.answer("✅ Сообщение отправлено в тему.")
    await maybe_delete_command(message)

@dp.message(Command("addpoints"))
async def cmd_addpoints(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not is_super(message):
        await message.answer("⛔ Добавлять очки может только @yakovlef.")
        await maybe_delete_command(message)
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Использование:\n/addpoints @user N")
        await maybe_delete_command(message)
        return

    target = parts[1].lower()
    try:
        member = await bot.get_chat_member(CHAT_ID, target)
        user = member.user
    except:
        await message.answer("❌ Пользователь не найден.")
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
        await message.answer("⛔ Убирать очки может только @yakovlef.")
        await maybe_delete_command(message)
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Использование:\n/delpoints @user N")
        await maybe_delete_command(message)
        return

    target = parts[1].lower()
    try:
        member = await bot.get_chat_member(CHAT_ID, target)
        user = member.user
    except:
        await message.answer("❌ Пользователь не найден.")
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

@dp.message(Command("resetgame"))
async def cmd_resetgame(message: Message):
    if not in_target_topic(message):
        return
    update_activity()

    if not is_super(message):
        await message.answer("⛔ Сбросить игру может только @yakovlef.")
        await maybe_delete_command(message)
        return

    game.update(active=False, word=None, leader_id=None, attempts=0, special=False)
    scores.clear()
    save_scores(scores)

    await message.answer("♻️ Игра и рейтинг сброшены.")
    await maybe_delete_command(message)

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

    await message.answer("📊 <b>Общий рейтинг:</b>\n" + "\n".join(lines))
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

# =========================================================
#                 CALLBACK-КНОПКИ ВЕДУЩЕГО
#  Доступ: только текущий ведущий ИЛИ @yakovlef
# =========================================================
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
        await call.answer("⛔ Только ведущий и @yakovlef.", show_alert=True)
        return

    if action == "show":
        await call.answer(f"Твоё слово: {game['word']}", show_alert=True)

    elif action == "replace":
        if game["special"]:
            # в спец-режиме смена слова разрешена только супер-офицеру
            if not is_super(call):
                await call.answer("⛔ В спец-раунде смена слова только для @yakovlef.", show_alert=True)
                return
            # спец-слово меняем просто на новое спец из текста нельзя — просим /special
            await call.answer("ℹ️ Для смены спец-слова используй /special <слово>.", show_alert=True)
            return

        words = load_words_list()
        w = pick_new_word(words)
        if not w:
            await call.answer("Слова закончились!", show_alert=True)
            return
        game["word"] = w
        game["attempts"] = 0
        await call.answer(f"Новое слово: {w}", show_alert=True)

    elif action == "pass":
        await call.message.answer("Чтобы передать ход:\n/passlead @username")
        await call.answer()

    elif action == "stop":
        if not is_super(call):
            await call.answer("⛔ Остановить игру может только @yakovlef.", show_alert=True)
            return
        game.update(active=False, word=None, leader_id=None, attempts=0, special=False)
        await call.message.answer("⛔ Игра остановлена.")
        await call.answer("Остановлено.")

# =========================================================
#               ОБРАБОТКА СООБЩЕНИЙ (ИГРА)
# - угадывание без reply
# - штраф ведущему за однокоренные/подсказки
# =========================================================
@dp.message()
async def on_guess(message: Message):
    if not in_target_topic(message):
        return

    update_activity()

    # если игра не активна — просто выходим
    if not game["active"] or not game["word"]:
        return

    # штраф за «однокоренные» / подсказки от ведущего
    if message.from_user.id == game["leader_id"]:
        if message.text and detect_root_violation(message.text, game["word"]):
            # штрафные очки ведущему: -1 (не ниже 0)
            lid = game["leader_id"]
            scores[lid] = max(0, scores.get(lid, 0) - 1)
            save_scores(scores)
            await message.answer(
                f"⚠️ {mention_html(message.from_user)}, штраф -1 очко за однокоренное/подсказку!"
            )
        return

    if not message.text:
        return

    guess = normalize(message.text)
    answer = normalize(game["word"])

    if not guess:
        return

    # при спец-слове можно засчитывать вхождение (на случай фраз)
    is_correct = (guess == answer) or (answer in guess)

    if not is_correct:
        game["attempts"] += 1
        return

    # ========= УГАДАЛ =========
    user = message.from_user
    uid = user.id

    reward = game["special_reward"] if game["special"] else 1
    scores[uid] = scores.get(uid, 0) + reward
    save_scores(scores)

    # статистика угадываний
    stats["total_guessed"] = int(stats.get("total_guessed", 0)) + 1
    stats["today_guessed"] = int(stats.get("today_guessed", 0)) + 1
    stats["today_date"] = str(date.today())
    save_stats(stats)

    # похвала + ачивка
    ach = achievement_for(scores[uid])
    praise = random.choice([
        "Красавчик! 😎",
        "Вот это скорость! 🔥",
        "Гениально! 🧠",
        "Супер-угадчик! 🐊",
        "Легчайше! 💪"
    ])

    text = (
        f"🎉 {mention_html(user)} угадал(а) слово <b>{game['word']}</b>!\n"
        f"{praise}\n"
        f"💎 +{reward} очк(а). Теперь у тебя: <b>{scores[uid]}</b>"
    )
    if ach:
        text += f"\n🏅 <b>Ачивка получена:</b> {ach}"

    await message.answer(text)

    # если это был спец-раунд — он заканчивается, дальше обычный раунд
    if game["special"]:
        game.update(active=False, word=None, leader_id=None, attempts=0, special=False)
        await message.answer("⭐ Спец-раунд завершён! Для продолжения жми /startgame.")
        return

    # передаём ход угадчику
    words = load_words_list()
    new_word = pick_new_word(words)

    if not new_word:
        game.update(active=False, word=None, leader_id=None, attempts=0, special=False)
        await message.answer("🎉 Все слова закончились! Игра остановлена.")
        return

    game.update(
        leader_id=uid,
        word=new_word,
        attempts=0
    )

    await message.answer(
        f"👉 Новый ведущий: {mention_html(user)}",
        reply_markup=leader_keyboard(uid)
    )

# =========================================================
#               ФОНОВЫЕ ЗАДАЧИ
# =========================================================
async def daily_report_loop():
    """Раз в день пишет супер-офицеру сколько слов угадали за день."""
    global SUPER_OFFICER_ID
    while True:
        try:
            # ждём до 21:00 серверного времени
            now = datetime.now()
            target = datetime.combine(now.date(), time(21, 0))
            if now >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())

            stats_local = load_stats()
            if SUPER_OFFICER_ID:
                await bot.send_message(
                    chat_id=SUPER_OFFICER_ID,
                    text=f"📌 За сегодня угадано слов: <b>{stats_local.get('today_guessed',0)}</b>"
                )

            # обнуляем today
            stats_local["today_guessed"] = 0
            stats_local["today_date"] = str(date.today())
            save_stats(stats_local)

        except Exception as e:
            logger.warning(f"daily_report_loop error: {e}")
            await asyncio.sleep(60)

async def inactivity_loop():
    """Если >3 часов нет активности и игра не идёт — предложить сыграть."""
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
                    text="🐊 Давно не играли! Может сыграем в Крокодила? Жми /startgame 😄"
                )
                last_activity_ts = datetime.now()
        except Exception as e:
            logger.warning(f"inactivity_loop error: {e}")

# =========================================================
#                       ЗАПУСК
# =========================================================
async def main():
    logger.info("✅ Бот запущен и готов к работе.")
    await setup_commands()

    # запускаем фоновые задачи
    asyncio.create_task(daily_report_loop())
    asyncio.create_task(inactivity_loop())

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())

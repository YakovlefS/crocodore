import os
import logging
import random
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ---------- ЛОГИРОВАНИЕ ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))       # id группы
THREAD_ID = int(os.getenv("THREAD_ID", "0"))   # id темы (message_thread_id)

if not BOT_TOKEN:
    raise SystemExit("Не задан BOT_TOKEN")

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# ---------- СОСТОЯНИЕ ИГРЫ ----------
game = {
    "active": False,
    "word": None,
    "leader_id": None,
    "attempts": 0,      # попытки в текущем раунде для подсказок
}

# user_id -> score
scores: dict[int, int] = {}


# ---------- УТИЛИТЫ ----------

def normalize(text: str) -> str:
    """Приводим строку к виду для сравнения (убираем пробелы, регистр и т.п.)."""
    return "".join(ch.lower() for ch in text if not ch.isspace())


def mention(user) -> str:
    """Красивое упоминание пользователя."""
    name = (user.full_name or "игрок").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def in_target_topic(message: Message) -> bool:
    """Проверяем, что сообщение в нужном чате и теме."""
    return (
        message.chat
        and message.chat.id == CHAT_ID
        and getattr(message, "message_thread_id", None) == THREAD_ID
    )


def leader_keyboard(leader_id: int) -> InlineKeyboardMarkup:
    """Кнопки, которые видит только ведущий."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👁 Показать слово",
                    callback_data=f"show:{leader_id}",
                ),
                InlineKeyboardButton(
                    text="🔄 Заменить слово",
                    callback_data=f"replace:{leader_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⛔ Остановить игру",
                    callback_data=f"stop:{leader_id}",
                )
            ],
        ]
    )


async def is_admin(user_id: int) -> bool:
    """Проверяем, является ли пользователь админом чата."""
    try:
        member = await bot.get_chat_member(CHAT_ID, user_id)
        status = getattr(member, "status", None)
        return status in ("administrator", "creator", "owner")
    except Exception as e:
        logger.warning(f"Не удалось проверить статус админа: {e}")
        return False


async def load_words() -> list[str]:
    """Загружаем слова из words.txt."""
    try:
        with open("words.txt", "r", encoding="utf-8") as f:
            words = [w.strip() for w in f if w.strip()]
        if not words:
            raise ValueError("Файл words.txt пуст")
        return words
    except Exception as e:
        logger.error(f"Ошибка при загрузке words.txt: {e}")
        # на крайний случай — fallback список
        return [
            "яблоко",
            "груша",
            "лампа",
            "дерево",
            "река",
            "кошка",
            "собака",
            "стол",
            "телефон",
            "самолёт",
        ]


# ---------- ХЕНДЛЕРЫ КОМАНД ----------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        "🐊 Привет! Это бот для игры в «Крокодила».\n\n"
        "Команды:\n"
        "• /startgame — начать игру (становишься ведущим)\n"
        "• /score — общий рейтинг игроков\n"
        "• /top — топ-10 игроков\n"
        "• /resetgame — полный сброс (только админ)\n"
        "• /info — показать chat_id и thread_id\n\n"
        "Бот работает только в одной теме, заданной переменными CHAT_ID и THREAD_ID."
    )
    await message.answer(text)


@dp.message(Command("info"))
async def cmd_info(message: Message):
    await message.answer(
        f"<b>chat_id:</b> <code>{message.chat.id}</code>\n"
        f"<b>thread_id:</b> <code>{getattr(message, 'message_thread_id', None)}</code>"
    )


@dp.message(Command("startgame"))
async def cmd_startgame(message: Message):
    # Игра только в нужной теме
    if not in_target_topic(message):
        return

    if game["active"]:
        await message.answer("⚠️ Игра уже идёт. Сначала закончите текущую.")
        return

    words = await load_words()
    leader = message.from_user

    game["active"] = True
    game["leader_id"] = leader.id
    game["word"] = random.choice(words)
    game["attempts"] = 0

    await message.answer(
        f"🎮 Новый раунд!\nВедущий: {mention(leader)}",
        reply_markup=leader_keyboard(leader.id),
    )


@dp.message(Command("score"))
async def cmd_score(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        await message.answer("📊 Пока ещё никто не набрал очков.")
        return

    lines = []
    for idx, (uid, pts) in enumerate(
        sorted(scores.items(), key=lambda x: x[1], reverse=True),
        start=1,
    ):
        lines.append(f"{idx}. <code>{uid}</code> — {pts} очк(о/а)")

    await message.answer("📊 <b>Общий рейтинг:</b>\n\n" + "\n".join(lines))


@dp.message(Command("top"))
async def cmd_top(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        await message.answer("🏆 Пока нет данных для топа.")
        return

    top10 = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
    lines = []
    for idx, (uid, pts) in enumerate(top10, start=1):
        lines.append(f"{idx}. <code>{uid}</code> — {pts} очк(о/а)")

    await message.answer("🏆 <b>Топ-10 игроков:</b>\n\n" + "\n".join(lines))


@dp.message(Command("resetgame"))
async def cmd_resetgame(message: Message):
    # полный сброс — только админ
    if not in_target_topic(message):
        return

    if not await is_admin(message.from_user.id):
        await message.answer("⛔ Сбросить игру и рейтинг может только администратор.")
        return

    game["active"] = False
    game["leader_id"] = None
    game["word"] = None
    game["attempts"] = 0
    scores.clear()

    await message.answer("♻️ Игра и рейтинг полностью сброшены.")


@dp.message(Command("hint"))
async def cmd_hint(message: Message):
    """Подсказка по слову — только ведущему, когда игра активна."""
    if not in_target_topic(message):
        return
    if not game["active"] or not game["word"]:
        await message.answer("Сейчас игра не запущена.")
        return
    if message.from_user.id != game["leader_id"]:
        await message.answer("Подсказку может запрашивать только ведущий.")
        return

    word = game["word"]
    if len(word) <= 2:
        hint = word[0] + " _"
    else:
        hint = word[0] + " " + " _" * (len(word) - 1)

    await message.answer(
        f"💡 Подсказка для всех:\n"
        f"Слово из {len(word)} букв.\n"
        f"Первая буква: <b>{word[0].upper()}</b>\n"
        f"Шаблон: <code>{hint}</code>"
    )


# ---------- CALLBACK-КНОПКИ ВЕДУЩЕГО ----------

@dp.callback_query()
async def callbacks(call: CallbackQuery):
    if not call.message:
        return
    if call.message.chat.id != CHAT_ID:
        return
    if getattr(call.message, "message_thread_id", None) != THREAD_ID:
        return

    if not game["active"] or not game["leader_id"]:
        await call.answer("Игра сейчас не запущена.", show_alert=True)
        return

    data = call.data or ""
    parts = data.split(":")
    if len(parts) != 2:
        await call.answer("Неверные данные кнопки.", show_alert=True)
        return

    action, leader_id_str = parts
    try:
        leader_id = int(leader_id_str)
    except ValueError:
        await call.answer("Ошибка данных кнопки.", show_alert=True)
        return

    # Кнопки доступны только текущему ведущему
    if call.from_user.id != game["leader_id"] or leader_id != game["leader_id"]:
        await call.answer("Вы не ведущий и не можете использовать эту кнопку.", show_alert=True)
        return

    # Показать слово
    if action == "show":
        await call.answer(f"Твоё слово: {game['word']}", show_alert=True)

    # Заменить слово
    elif action == "replace":
        words = await load_words()
        game["word"] = random.choice(words)
        game["attempts"] = 0
        await call.answer(f"Новое слово: {game['word']}", show_alert=True)

    # Остановить игру (только админ)
    elif action == "stop":
        if not await is_admin(call.from_user.id):
            await call.answer("⛔ Остановить игру может только администратор.", show_alert=True)
            return

        game["active"] = False
        game["leader_id"] = None
        game["word"] = None
        game["attempts"] = 0

        await call.message.answer("⛔ Игра остановлена. Для нового раунда используйте /startgame.")
        await call.answer("Игра остановлена.")


# ---------- ОБРАБОТКА СООБЩЕНИЙ (УГАДЫВАНИЕ СЛОВА) ----------

@dp.message()
async def game_messages(message: Message):
    # только нужная тема
    if not in_target_topic(message):
        return

    # нет активной игры
    if not game["active"] or not game["word"]:
        return

    # ведущий не угадывает своё слово
    if message.from_user.id == game["leader_id"]:
        return

    if not message.text:
        return

    guess = normalize(message.text)
    answer = normalize(game["word"])

    if not guess:
        return

    # НЕ угадал → увеличиваем попытки, при необходимости можно дальше расширять логику подсказок
    if guess != answer:
        game["attempts"] += 1
        # пример улучшения: после 10 неудачных попыток ведущий может дать подсказку
        if game["attempts"] == 10:
            await message.answer(
                "😅 Много неверных ответов. "
                "Ведущий может выдать подсказку командой /hint."
            )
        return

    # УГАДАЛ
    user = message.from_user
    uid = user.id

    scores[uid] = scores.get(uid, 0) + 1

    await message.answer(
        f"🎉 {mention(user)} угадал(а) слово <b>{game['word']}</b>!\n"
        f"Теперь у него(неё) {scores[uid]} очк(о/а)."
    )

    # Передаём ход новому ведущему
    words = await load_words()
    game["leader_id"] = uid
    game["word"] = random.choice(words)
    game["attempts"] = 0

    await message.answer(
        f"👉 Новый ведущий: {mention(user)}",
        reply_markup=leader_keyboard(uid),
    )


# ---------- ЗАПУСК БОТА ----------

async def main():
    logger.info("Бот запущен (aiogram 3.x).")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())

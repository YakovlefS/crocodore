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
CHAT_ID = int(os.getenv("CHAT_ID", "0"))      # id чата (supergroup)
THREAD_ID = int(os.getenv("THREAD_ID", "0"))  # id темы (message_thread_id)

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан")

# ========= BOT / DP =========
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

# user_id -> очки
scores: dict[int, int] = {}


# ========= УТИЛИТЫ =========

def normalize(text: str) -> str:
    """Оставить буквы, е = ё, убрать пробелы, пунктуацию"""
    text = text.lower().replace("ё", "е")
    return "".join(ch for ch in text if "а" <= ch <= "я" or "a" <= ch <= "z")


def mention(user) -> str:
    """Красивое упоминание пользователя."""
    name = (user.full_name or "игрок").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def in_target_topic(message: Message) -> bool:
    """
    Бот реагирует только:
    - в нужном чате
    - в нужной теме (если THREAD_ID != 0)
    """
    if not message.chat or message.chat.id != CHAT_ID:
        return False

    # если тема не задана — реагируем везде в этом чате
    if THREAD_ID == 0:
        return True

    # если Telegram прислал message_thread_id — сверяем
    thread = getattr(message, "message_thread_id", None)
    if thread is not None:
        return thread == THREAD_ID

    # на всякий случай (редкий кейс, когда thread_id не приходит)
    return True


async def is_admin(user_id: int) -> bool:
    """Проверка на администратора чата."""
    try:
        member = await bot.get_chat_member(CHAT_ID, user_id)
        return member.status in ("creator", "administrator", "owner")
    except Exception as e:
        logger.warning(f"Не удалось проверить админа: {e}")
        return False


async def load_words() -> list[str]:
    """Загрузка слов из words.txt (одно слово в строке)."""
    try:
        with open("words.txt", "r", encoding="utf-8") as f:
            words = [w.strip().lower() for w in f if w.strip()]
        if not words:
            raise ValueError("words.txt пуст")
        return words
    except Exception as e:
        logger.warning(f"Ошибка чтения words.txt: {e}")
        # fallback на базовый список
        return ["крокодил", "машина", "лампа", "река", "дерево"]


def leader_keyboard(leader_id: int) -> InlineKeyboardMarkup:
    """Кнопки ведущего."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👁 Показать слово",
                    callback_data=f"show:{leader_id}",
                ),
                InlineKeyboardButton(
                    text="🔄 Новое слово",
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


async def setup_bot_commands(bot: Bot):
    """Регистрируем команды бота в Telegram."""
    commands = [
        BotCommand(command="start", description="Описание бота"),
        BotCommand(command="startgame", description="Начать игру и стать ведущим"),
        BotCommand(command="score", description="Общий рейтинг игроков"),
        BotCommand(command="top", description="Топ-10 игроков"),
        BotCommand(command="hint", description="Подсказка (только ведущий)"),
        BotCommand(command="resetgame", description="Сброс игры и очков (админ)"),
        BotCommand(command="info", description="Показать chat_id и thread_id"),
    ]
    await bot.set_my_commands(commands)


# ========= КОМАНДЫ =========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🐊 <b>Крокодил бот</b>\n\n"
        "/startgame — начать игру и стать ведущим\n"
        "/score — рейтинг игроков\n"
        "/top — топ-10\n"
        "/hint — подсказка (ведущий)\n"
        "/resetgame — сброс (админ)\n"
        "/info — chat_id & thread_id\n"
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
        await message.answer("⚠️ Игра уже идёт. Сначала остановите её или доиграйте раунд.")
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

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    lines = [
        f"{i+1}. <code>{uid}</code> — {pts}"
        for i, (uid, pts) in enumerate(rating)
    ]
    await message.answer("📊 <b>Рейтинг игроков:</b>\n" + "\n".join(lines))


@dp.message(Command("top"))
async def cmd_top(message: Message):
    if not in_target_topic(message):
        return

    if not scores:
        await message.answer("🏆 Топ пуст — ещё никто не играл.")
        return

    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
    lines = [
        f"{i+1}. <code>{uid}</code> — {pts}"
        for i, (uid, pts) in enumerate(rating)
    ]
    await message.answer("🏆 <b>Топ-10 игроков:</b>\n" + "\n".join(lines))


@dp.message(Command("resetgame"))
async def cmd_resetgame(message: Message):
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
    if not in_target_topic(message):
        return

    if not game["active"] or not game["word"]:
        await message.answer("Сейчас игра не идёт.")
        return

    if message.from_user.id != game["leader_id"]:
        await message.answer("Подсказку может давать только текущий ведущий.")
        return

    word = game["word"]
    hint = word[0] + " _" * (len(word) - 1)

    await message.answer(
        f"💡 Подсказка:\n"
        f"Слово из {len(word)} букв.\n"
        f"Первая буква: <b>{word[0].upper()}</b>\n"
        f"<code>{hint}</code>"
    )


# ========= CALLBACK-КНОПКИ =========

@dp.callback_query()
async def callbacks(call: CallbackQuery):
    msg = call.message
    if not msg or msg.chat.id != CHAT_ID:
        return

    # проверка темы для кнопок
    if THREAD_ID != 0:
        thread = getattr(msg, "message_thread_id", None)
        if thread is not None and thread != THREAD_ID:
            return

    if not game["active"] or not game["leader_id"]:
        await call.answer("Игра сейчас не идёт.", show_alert=True)
        return

    data = call.data or ""
    try:
        action, leader_str = data.split(":")
        leader_id = int(leader_str)
    except ValueError:
        await call.answer("Некорректные данные кнопки.", show_alert=True)
        return

    if call.from_user.id != leader_id:
        await call.answer("Вы не ведущий и не можете использовать эту кнопку.", show_alert=True)
        return

    # Показать слово
    if action == "show":
        await call.answer(f"Слово: {game['word']}", show_alert=True)
        return

    # Новое слово
    if action == "replace":
        words = await load_words()
        game["word"] = random.choice(words)
        game["attempts"] = 0
        await call.answer(f"Новое слово: {game['word']}", show_alert=True)
        return

    # Остановить игру
    if action == "stop":
        if not await is_admin(call.from_user.id):
            await call.answer("Остановить игру может только админ.", show_alert=True)
            return

        game["active"] = False
        game["leader_id"] = None
        game["word"] = None
        game["attempts"] = 0

        await msg.answer("⛔ Игра остановлена. Для нового раунда используйте /startgame.")
        await call.answer("Игра остановлена.", show_alert=True)
        return


# ========= УГАДЫВАНИЕ СЛОВА =========

@dp.message()
async def handle_guess(message: Message):
    # Фильтруем по чату/теме
    if not in_target_topic(message):
        return

    # Нет активной игры или слова
    if not game["active"] or not game["word"]:
        return

    # Ведущий своё слово не угадывает
    if message.from_user.id == game["leader_id"]:
        return

    if not message.text:
        return

    guess = normalize(message.text)
    answer = normalize(game["word"])

    if not guess:
        return

    # допускаем фразы вида "это слово яблоко"
    if answer not in guess:
        game["attempts"] += 1
        return

    # УГАДАНО
    uid = message.from_user.id
    scores[uid] = scores.get(uid, 0) + 1

    await message.answer(
        f"🎉 {mention(message.from_user)} угадал слово <b>{game['word']}</b>!\n"
        f"Теперь у него {scores[uid]} очков."
    )

    # передаём ход новому ведущему
    words = await load_words()
    new_word = random.choice(words)

    game["leader_id"] = uid
    game["word"] = new_word
    game["attempts"] = 0

    await message.answer(
        f"👉 Новый ведущий: {mention(message.from_user)}",
        reply_markup=leader_keyboard(uid),
    )


# ========= ЗАПУСК =========

async def main():
    logger.info("🚀 Бот запущен")
    await setup_bot_commands(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

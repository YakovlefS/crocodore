"""Telegram-бот с игрой "Крокодил" на базе aiogram."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

# ================== НАСТРОЙКА ЛОГИРОВАНИЯ ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== НАСТРОЙКА ОКРУЖЕНИЯ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))
THREAD_ID = int(os.getenv("THREAD_ID", "0"))

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN не задан")

# Создаём экземпляры бота и диспетчера сразу, чтобы хэндлеры могли ими пользоваться
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


# ================== ДАТАКЛАССЫ ==================
@dataclass
class GameState:
    """Хранит состояние текущего раунда."""

    active: bool = False
    word: Optional[str] = None
    leader_id: Optional[int] = None
    leader_name: Optional[str] = None
    attempts: int = 0
    hint_level: int = 0
    max_hints: int = 4
    auto_hint_step: int = 6
    revealed_positions: Set[int] = field(default_factory=set)

    def reset(self) -> None:
        """Полностью очищает состояние игры."""

        self.active = False
        self.word = None
        self.leader_id = None
        self.leader_name = None
        self.attempts = 0
        self.hint_level = 0
        self.revealed_positions.clear()

    def start_round(self, word: str, leader: User) -> None:
        """Настраивает параметры нового раунда."""

        self.active = True
        self.word = word
        self.leader_id = leader.id
        self.leader_name = leader.full_name or leader.username or "игрок"
        self.attempts = 0
        self.hint_level = 0
        self.revealed_positions.clear()


@dataclass
class ScoreRecord:
    """Хранит очки игрока."""

    points: int = 0
    name: str = "игрок"


game = GameState()
scores: Dict[int, ScoreRecord] = {}

# ================== КОНСТАНТЫ ==================
VOWELS = set("аеёиоуыэюяaeiouy")
ATTEMPTS_NOTIFY_STEP = 5


# ================== УТИЛИТЫ ==================
def sanitize_name(name: str) -> str:
    """Удаляет потенциально опасные символы из имён."""

    return name.replace("<", "").replace(">", "")


def mention(user: User) -> str:
    """Создаёт HTML-ссылку на пользователя."""

    name = sanitize_name(user.full_name or user.username or "игрок")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def mention_from_record(uid: int, record: ScoreRecord) -> str:
    """Создаёт HTML-ссылку по сохранённому рекорду."""

    name = sanitize_name(record.name or "игрок")
    return f'<a href="tg://user?id={uid}">{name}</a>'


def normalize(text: str) -> str:
    """Оставляет только буквы и приводит их к нижнему регистру."""

    return "".join(ch.lower() for ch in text if ch.isalpha())


def in_target_topic(message: Message) -> bool:
    """Проверяет, что сообщение пришло из нужного чата/темы."""

    if not message.chat or message.chat.id != CHAT_ID:
        return False

    if THREAD_ID == 0:
        return True

    thread = getattr(message, "message_thread_id", None)
    if thread is not None:
        return thread == THREAD_ID

    # Telegram иногда не отправляет идентификатор темы, поэтому считаем, что всё ок
    return True


async def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором чата."""

    try:
        member = await bot.get_chat_member(CHAT_ID, user_id)
        return member.status in ("creator", "administrator", "owner")
    except Exception:
        return False


async def load_words() -> List[str]:
    """Загружает список слов из файла."""

    try:
        with open("words.txt", "r", encoding="utf-8") as file:
            return [word.strip().lower() for word in file if word.strip()]
    except Exception:
        # На случай отсутствия файла возвращаем набор запасных слов
        return ["крокодил", "машина", "лампа", "река"]


def leader_keyboard(leader_id: int) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру с действиями ведущего."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👁 Показать слово", callback_data=f"show:{leader_id}"),
                InlineKeyboardButton(text="🔄 Новое слово", callback_data=f"replace:{leader_id}"),
            ],
            [
                InlineKeyboardButton(text="⛔ Остановить игру", callback_data=f"stop:{leader_id}"),
            ],
        ]
    )


def format_rating(limit: Optional[int] = None) -> str:
    """Возвращает таблицу рейтинга в виде текста."""

    if not scores:
        return ""

    sorted_scores = sorted(scores.items(), key=lambda item: item[1].points, reverse=True)
    if limit is not None:
        sorted_scores = sorted_scores[:limit]

    lines = [
        f"{position}. {mention_from_record(uid, record)} — {record.points}"
        for position, (uid, record) in enumerate(sorted_scores, start=1)
    ]
    return "\n".join(lines)


def compute_revealed_positions(word: str, level: int) -> Set[int]:
    """Определяет индексы букв, которые должны быть показаны при подсказке."""

    level = max(0, min(level, game.max_hints))
    length = len(word)
    positions: Set[int] = set()

    if level >= 1 and length > 0:
        positions.add(0)
    if level >= 2 and length > 1:
        positions.add(length - 1)
    if level >= 3:
        positions.update(idx for idx, char in enumerate(word.lower()) if char in VOWELS)
    if level >= 4:
        positions.update(range(0, length, 2))

    return positions


def format_hint(word: str, level: int) -> str:
    """Собирает текст подсказки в зависимости от уровня."""

    if level <= 0:
        return f"Слово из {len(word)} букв."

    positions = compute_revealed_positions(word, level)
    hint_chars = [char if idx in positions else "_" for idx, char in enumerate(word)]

    descriptions = {
        1: "Открыта первая буква.",
        2: "Открыты первая и последняя буквы.",
        3: "Показаны все гласные.",
        4: "Подсвечена половина букв.",
    }
    description = descriptions.get(level, "Подсказка обновлена.")

    return f"{description}\n<code>{' '.join(hint_chars)}</code>"


async def notify_leader(word: str, leader: User) -> None:
    """Отправляет ведущему слово в личные сообщения."""

    text = (
        "🤫 <b>Вы — ведущий раунда!</b>\n"
        "Вот ваше слово:\n"
        f"<b>{word}</b>\n\n"
        "Используйте кнопки под сообщением в чате, чтобы подсмотреть или сменить слово.\n"
        "Команда /hint выдаёт подсказку игрокам."
    )
    try:
        await bot.send_message(leader.id, text)
    except Exception:
        logger.warning("Не удалось отправить слово ведущему", exc_info=True)


def add_score(user: User) -> int:
    """Добавляет игроку одно очко и возвращает новое количество очков."""

    record = scores.get(user.id)
    if not record:
        record = ScoreRecord(points=0, name=user.full_name or user.username or "игрок")
        scores[user.id] = record
    record.points = 1
    record.name = user.full_name or record.name
    return record.points


def build_status_message(include_hint: bool = True) -> str:
    """Формирует текстовое описание текущего раунда."""

    if not game.active or not game.word:
        return "Сейчас игра не запущена."

    lines = ["📢 <b>Состояние раунда</b>"]
    if game.leader_name:
        lines.append(f"Ведущий: {sanitize_name(game.leader_name)}")
    lines.append(f"Попыток: {game.attempts}")

    if include_hint:
        lines.append(format_hint(game.word, game.hint_level))
    else:
        lines.append(f"Слово из {len(game.word)} букв.")

    return "\n".join(lines)


async def maybe_auto_hint(message: Message) -> None:
    """Автоматически повышает уровень подсказки после N попыток."""

    if not game.active or not game.word:
        return

    desired_level = min(game.max_hints, game.attempts // game.auto_hint_step)
    if desired_level <= game.hint_level:
        return

    game.hint_level = desired_level
    hint = format_hint(game.word, game.hint_level)
    await message.answer(f"🤖 Авто-подсказка #{game.hint_level}:\n{hint}")


async def send_status(message: Message) -> None:
    """Отправляет сообщение о статусе игры."""

    await message.answer(build_status_message())


async def setup_bot_commands(bot_instance: Bot) -> None:
    """Настраивает список команд в меню Telegram."""

    commands = [
        BotCommand(command="start", description="Описание бота"),
        BotCommand(command="startgame", description="Начать игру"),
        BotCommand(command="status", description="Статус текущего раунда"),
        BotCommand(command="score", description="Рейтинг игроков"),
        BotCommand(command="top", description="Топ-10 игроков"),
        BotCommand(command="hint", description="Подсказка (ведущий)"),
        BotCommand(command="resetgame", description="Сброс игры (админ)"),
        BotCommand(command="info", description="Показать chat_id и thread_id"),
    ]

    await bot_instance.set_my_commands(commands)


# ================== ХЭНДЛЕРЫ КОМАНД ==================
@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Приветствует пользователя."""

    await message.answer(
        "🐊 <b>Крокодил Бот на связи!</b>\n\n"
        "Используйте /startgame, чтобы стать ведущим.\n"
        "Ведущий получает слово в личку и может давать подсказки командой /hint.\n"
        "Команда /status покажет текущий прогресс раунда.\n"
        "Очки начисляются за каждое угаданное слово — смотрите /score и /top."
    )


@dp.message(Command("info"))
async def cmd_info(message: Message) -> None:
    """Показывает идентификаторы чата и темы."""

    thread = getattr(message, "message_thread_id", None)
    await message.answer(
        f"<b>chat_id:</b> <code>{message.chat.id}</code>\n"
        f"<b>thread_id:</b> <code>{thread}</code>"
    )


@dp.message(Command("status"))
async def cmd_status(message: Message) -> None:
    """Отправляет информацию о текущей игре."""

    if not in_target_topic(message):
        return
    await send_status(message)


async def launch_round(message: Message, leader: User) -> None:
    """Запускает новый раунд и уведомляет ведущего."""

    words = await load_words()
    word = random.choice(words)
    game.start_round(word, leader)

    await message.answer(
        f"🎮 Раунд запущен! Ведущий: {mention(leader)}\n"
        "Слово отправлено в личку ведущему.",
        reply_markup=leader_keyboard(leader.id),
    )
    await notify_leader(word, leader)


@dp.message(Command("startgame"))
async def cmd_startgame(message: Message) -> None:
    """Стартует игру, если она ещё не идёт."""

    if not in_target_topic(message):
        return

    if game.active:
        await message.answer("⚠️ Игра уже идёт. Используйте /status, чтобы посмотреть прогресс.")
        return

    await launch_round(message, message.from_user)


@dp.message(Command("score"))
async def cmd_score(message: Message) -> None:
    """Показывает рейтинг всех игроков."""

    if not in_target_topic(message):
        return

    rating = format_rating()
    if not rating:
        await message.answer("📊 Пока нет очков.")
        return

    await message.answer("📊 <b>Рейтинг:</b>\n"  rating)


@dp.message(Command("top"))
async def cmd_top(message: Message) -> None:
    """Показывает топ-10 игроков."""

    if not in_target_topic(message):
        return

    rating = format_rating(limit=10)
    if not rating:
        await message.answer("🏆 Тут пока пусто.")
        return

    await message.answer("🏆 <b>Топ-10:</b>\n"  rating)


@dp.message(Command("resetgame"))
async def cmd_reset(message: Message) -> None:
    """Сбрасывает игру и очки (только для админов)."""

    if not in_target_topic(message):
        return

    if not await is_admin(message.from_user.id):
        await message.answer("⛔ Только админ может сбросить игру.")
        return

    scores.clear()
    game.reset()

    await message.answer("♻️ Игра и рейтинг полностью сброшены.")


@dp.message(Command("hint"))
async def cmd_hint(message: Message) -> None:
    """Выдаёт подсказку игрокам (может вызвать только ведущий)."""

    if not in_target_topic(message):
        return

    if not game.active or not game.word or message.from_user.id != game.leader_id:
        await message.answer("⛔ Только текущий ведущий может использовать подсказку.")
        return

    if game.hint_level >= game.max_hints:
        await message.answer("ℹ️ Достигнут максимум подсказок.")
        return

    game.hint_level = 1
    hint = format_hint(game.word, game.hint_level)
    await message.answer(f"💡 Подсказка #{game.hint_level}:\n{hint}")


# ================== CALLBACK-КНОПКИ ВЕДУЩЕГО ==================
@dp.callback_query(F.data.startswith("show"))
async def cq_show_word(call: CallbackQuery) -> None:
    """Отправляет ведущему слово повторно."""

    if not game.active or not game.word:
        await call.answer("Игра неактивна", show_alert=True)
        return

    _, leader_id_str = call.data.split(":", 1)
    if int(leader_id_str) != call.from_user.id:
        await call.answer("Это не ваша кнопка", show_alert=True)
        return

    await notify_leader(game.word, call.from_user)
    await call.answer("Слово отправлено в личку", show_alert=True)


@dp.callback_query(F.data.startswith("replace"))
async def cq_replace_word(call: CallbackQuery) -> None:
    """Меняет слово для текущего ведущего."""

    if not game.active:
        await call.answer("Игра неактивна", show_alert=True)
        return

    _, leader_id_str = call.data.split(":", 1)
    if int(leader_id_str) != call.from_user.id:
        await call.answer("Это не ваша кнопка", show_alert=True)
        return

    words = await load_words()
    new_word = random.choice(words)
    game.word = new_word
    game.hint_level = 0
    game.attempts = 0
    game.revealed_positions.clear()

    await notify_leader(new_word, call.from_user)
    await call.message.answer("🔄 Ведущий сменил слово. Начинаем угадывать заново!")
    await call.answer("Новое слово отправлено вам в личку.", show_alert=True)


@dp.callback_query(F.data.startswith("stop"))
async def cq_stop_game(call: CallbackQuery) -> None:
    """Останавливает игру (доступно только админу)."""

    if not await is_admin(call.from_user.id):
        await call.answer("⛔ Только админ может остановить игру.", show_alert=True)
        return

    game.reset()
    await call.message.answer("⛔ Игра остановлена администратором.")
    await call.answer("Готово.", show_alert=True)


# ================== ОБРАБОТКА СООБЩЕНИЙ ==================
@dp.message()
async def guessing(message: Message) -> None:
    """Отвечает за основной процесс угадывания."""

    if not in_target_topic(message):
        return

    if not game.active or not game.word:
        return

    if message.from_user.id == game.leader_id:
        return

    if not message.text:
        return

    guess = normalize(message.text)
    if not guess:
        return

    answer = normalize(game.word)
    if answer not in guess:
        game.attempts = 1
        if game.attempts % ATTEMPTS_NOTIFY_STEP == 0:
            await message.answer(
                f"🙌 Уже {game.attempts} попыток! Ведущий может выдать подсказку /hint."
            )
        await maybe_auto_hint(message)
        return

    # Если мы оказались здесь — слово угадано
    new_points = add_score(message.from_user)
    await message.answer(
        f"🎉 {mention(message.from_user)} угадал слово <b>{game.word}</b>!\n"
        f"Теперь у него {new_points} очков."
    )

    await launch_round(message, message.from_user)


# ================== ТОЧКА ВХОДА ==================
async def main() -> None:
    """Точка входа: регистрируем команды и запускаем long polling."""

    logger.info("🚀 Бот запущен!")
    await setup_bot_commands(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

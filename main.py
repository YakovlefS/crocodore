 import os
 import logging
 import random
 import asyncio
-import re
+from dataclasses import dataclass, field
+from typing import Optional, Dict, List, Set
 
 from aiogram import Bot, Dispatcher
 from aiogram.filters import Command
 from aiogram.types import (
     Message,
     CallbackQuery,
     InlineKeyboardMarkup,
     InlineKeyboardButton,
     BotCommand,
+    User,
 )
 from aiogram.client.default import DefaultBotProperties
 
+
 # ========= ЛОГИ =========
 logging.basicConfig(level=logging.INFO)
 logger = logging.getLogger(__name__)
 
 # ========= ENV =========
 BOT_TOKEN = os.getenv("BOT_TOKEN")
 CHAT_ID = int(os.getenv("CHAT_ID", "0"))
 THREAD_ID = int(os.getenv("THREAD_ID", "0"))
 
 if not BOT_TOKEN:
     raise SystemExit("❌ BOT_TOKEN не задан")
 
-bot = Bot(
-    token=BOT_TOKEN,
-    default=DefaultBotProperties(parse_mode="HTML")
-)
-
+bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
 dp = Dispatcher()
 
-# ========= ИГРА =========
-game = {
-    "active": False,
-    "word": None,
-    "leader_id": None,
-    "attempts": 0,
-}
 
-scores: dict[int, int] = {}
+# ========= ДАТАКЛАССЫ =========
+@dataclass
+class GameState:
+    active: bool = False
+    word: Optional[str] = None
+    leader_id: Optional[int] = None
+    leader_name: Optional[str] = None
+    attempts: int = 0
+    hint_level: int = 0
+    max_hints: int = 4
+    auto_hint_step: int = 6
+    revealed_positions: Set[int] = field(default_factory=set)
 
-# ========= УТИЛИТЫ =========
+    def reset(self) -> None:
+        self.active = False
+        self.word = None
+        self.leader_id = None
+        self.leader_name = None
+        self.attempts = 0
+        self.hint_level = 0
+        self.revealed_positions.clear()
+
+    def start_round(self, word: str, leader: User) -> None:
+        self.active = True
+        self.word = word
+        self.leader_id = leader.id
+        self.leader_name = leader.full_name or leader.username or "игрок"
+        self.attempts = 0
+        self.hint_level = 0
+        self.revealed_positions.clear()
+
+
+@dataclass
+class ScoreRecord:
+    points: int = 0
+    name: str = "игрок"
 
+
+game = GameState()
+scores: Dict[int, ScoreRecord] = {}
+
+
+# ========= КОНСТАНТЫ =========
+VOWELS = set("аеёиоуыэюяaeiouy")
+ATTEMPTS_NOTIFY_STEP = 5
+
+
+# ========= УТИЛИТЫ =========
 def normalize(text: str) -> str:
-    """ Оставляем только буквы (кириллица/латиница), приводим к нижнему регистру """
+    """Оставляем только буквы (кириллица/латиница), приводим к нижнему регистру."""
     return "".join(ch.lower() for ch in text if ch.isalpha())
 
 
-def mention(user) -> str:
-    """ HTML-упоминание """
-    name = (user.full_name or "игрок").replace("<", "").replace(">", "")
+def sanitize_name(name: str) -> str:
+    return name.replace("<", "").replace(">", "")
+
+
+def mention(user: User) -> str:
+    name = sanitize_name(user.full_name or user.username or "игрок")
     return f'<a href="tg://user?id={user.id}">{name}</a>'
 
 
+def mention_from_record(uid: int, record: ScoreRecord) -> str:
+    name = sanitize_name(record.name or "игрок")
+    return f'<a href="tg://user?id={uid}">{name}</a>'
+
+
 def in_target_topic(message: Message) -> bool:
-    """ Проверяем, что сообщение именно в НУЖНОЙ теме """
     if not message.chat or message.chat.id != CHAT_ID:
         return False
 
-    # Если нет привязки к теме — работаем везде
     if THREAD_ID == 0:
         return True
 
     thread = getattr(message, "message_thread_id", None)
-
-    # Если тема указана явно — сверяем
     if thread is not None:
         return thread == THREAD_ID
 
-    # Telegram иногда НЕ присылает message_thread_id
-    # Но бот работает только в 1 теме → считаем, что всё ок
     return True
 
 
 async def is_admin(user_id: int) -> bool:
     try:
         m = await bot.get_chat_member(CHAT_ID, user_id)
         return m.status in ("creator", "administrator", "owner")
-    except:
+    except Exception:
         return False
 
 
-async def load_words() -> list[str]:
+async def load_words() -> List[str]:
     try:
         with open("words.txt", "r", encoding="utf-8") as f:
             return [w.strip().lower() for w in f if w.strip()]
-    except:
+    except Exception:
         return ["крокодил", "машина", "лампа", "река"]
 
 
 def leader_keyboard(leader_id: int) -> InlineKeyboardMarkup:
-    """ Кнопки, видимые только ведущему """
     return InlineKeyboardMarkup(
         inline_keyboard=[
             [
-                InlineKeyboardButton(
-                    text="👁 Показать слово",
-                    callback_data=f"show:{leader_id}"
-                ),
-                InlineKeyboardButton(
-                    text="🔄 Новое слово",
-                    callback_data=f"replace:{leader_id}"
-                )
+                InlineKeyboardButton(text="👁 Показать слово", callback_data=f"show:{leader_id}"),
+                InlineKeyboardButton(text="🔄 Новое слово", callback_data=f"replace:{leader_id}"),
             ],
             [
-                InlineKeyboardButton(
-                    text="⛔ Остановить игру",
-                    callback_data=f"stop:{leader_id}"
-                )
-            ]
+                InlineKeyboardButton(text="⛔ Остановить игру", callback_data=f"stop:{leader_id}"),
+            ],
         ]
     )
 
 
+def format_rating(limit: Optional[int] = None) -> str:
+    if not scores:
+        return ""
+
+    sorted_scores = sorted(scores.items(), key=lambda x: x[1].points, reverse=True)
+    if limit:
+        sorted_scores = sorted_scores[:limit]
+
+    lines = [
+        f"{idx}. {mention_from_record(uid, record)} — {record.points}"
+        for idx, (uid, record) in enumerate(sorted_scores, start=1)
+    ]
+    return "\n".join(lines)
+
+
+def compute_revealed_positions(word: str, level: int) -> Set[int]:
+    level = max(0, min(level, game.max_hints))
+    length = len(word)
+    positions: Set[int] = set()
+
+    if level >= 1 and length > 0:
+        positions.add(0)
+    if level >= 2 and length > 1:
+        positions.add(length - 1)
+    if level >= 3:
+        positions.update(i for i, ch in enumerate(word.lower()) if ch in VOWELS)
+    if level >= 4:
+        positions.update(i for i in range(0, length, 2))
+
+    return positions
+
+
+def format_hint(word: str, level: int) -> str:
+    if level <= 0:
+        return f"Слово из {len(word)} букв."
+
+    positions = compute_revealed_positions(word, level)
+    hint_chars = [ch if idx in positions else "_" for idx, ch in enumerate(word)]
+
+    descriptions = {
+        1: "Открыта первая буква.",
+        2: "Открыты первая и последняя буквы.",
+        3: "Показаны все гласные.",
+        4: "Подсвечена половина букв.",
+    }
+    description = descriptions.get(level, "Подсказка обновлена.")
+
+    return f"{description}\n<code>{' '.join(hint_chars)}</code>"
+
+
+async def notify_leader(word: str, leader: User) -> None:
+    text = (
+        "🤫 <b>Вы — ведущий раунда!</b>\n"
+        "Вот ваше слово:\n"
+        f"<b>{word}</b>\n\n"
+        "Используйте кнопки под сообщением в чате, чтобы подсмотреть или сменить слово.\n"
+        "Команда /hint выдаёт подсказку игрокам."
+    )
+    try:
+        await bot.send_message(leader.id, text)
+    except Exception:
+        logger.warning("Не удалось написать ведущему в личку", exc_info=True)
+
+
+def add_score(user: User) -> int:
+    record = scores.get(user.id)
+    if not record:
+        record = ScoreRecord(points=0, name=user.full_name or user.username or "игрок")
+        scores[user.id] = record
+    record.points += 1
+    record.name = user.full_name or record.name
+    return record.points
+
+
+def build_status_message(include_hint: bool = True) -> str:
+    if not game.active or not game.word:
+        return "Сейчас игра не запущена."
+
+    lines = ["📢 <b>Состояние раунда</b>"]
+    if game.leader_name:
+        lines.append(f"Ведущий: {sanitize_name(game.leader_name)}")
+    lines.append(f"Попыток: {game.attempts}")
+
+    if include_hint:
+        lines.append(format_hint(game.word, game.hint_level))
+    else:
+        lines.append(f"Слово из {len(game.word)} букв.")
+
+    return "\n".join(lines)
+
+
+async def maybe_auto_hint(message: Message) -> None:
+    if not game.active or not game.word:
+        return
+
+    desired_level = min(game.max_hints, game.attempts // game.auto_hint_step)
+    if desired_level <= game.hint_level:
+        return
+
+    game.hint_level = desired_level
+    hint = format_hint(game.word, game.hint_level)
+    await message.answer(f"🤖 Авто-подсказка #{game.hint_level}:\n{hint}")
+
+
+async def send_status(message: Message) -> None:
+    await message.answer(build_status_message())
+
+
 async def setup_bot_commands(bot: Bot):
-    """ Команды в меню Telegram """
     commands = [
         BotCommand(command="start", description="Описание бота"),
         BotCommand(command="startgame", description="Начать игру"),
+        BotCommand(command="status", description="Статус текущего раунда"),
         BotCommand(command="score", description="Рейтинг игроков"),
         BotCommand(command="top", description="Топ-10 игроков"),
         BotCommand(command="hint", description="Подсказка (ведущий)"),
         BotCommand(command="resetgame", description="Сброс игры (админ)"),
         BotCommand(command="info", description="Показать chat_id и thread_id"),
     ]
 
     await bot.set_my_commands(commands)
 
 
 # ========= КОМАНДЫ =========
-
 @dp.message(Command("start"))
 async def cmd_start(message: Message):
     await message.answer(
-        "🐊 <b>Крокодил Бот готов!</b>\n\n"
-        "/startgame — начать игру и стать ведущим\n"
-        "/score — рейтинг игроков\n"
-        "/top — топ-10\n"
-        "/hint — подсказка (только ведущий)\n"
-        "/resetgame — сброс игры и очков (админ)\n"
-        "/info — chat_id & thread_id\n"
+        "🐊 <b>Крокодил Бот на связи!</b>\n\n"
+        "Используйте /startgame, чтобы стать ведущим.\n"
+        "Ведущий получает слово в личку и может давать подсказки командой /hint.\n"
+        "Команда /status покажет текущий прогресс раунда.\n"
+        "Очки начисляются за каждое угаданное слово — смотрите /score и /top."
     )
 
 
 @dp.message(Command("info"))
 async def cmd_info(message: Message):
     thread = getattr(message, "message_thread_id", None)
     await message.answer(
         f"<b>chat_id:</b> <code>{message.chat.id}</code>\n"
         f"<b>thread_id:</b> <code>{thread}</code>"
     )
 
 
-@dp.message(Command("startgame"))
-async def cmd_startgame(message: Message):
+@dp.message(Command("status"))
+async def cmd_status(message: Message):
     if not in_target_topic(message):
         return
+    await send_status(message)
 
-    if game["active"]:
-        await message.answer("⚠️ Игра уже идёт.")
-        return
 
+async def launch_round(message: Message, leader: User) -> None:
     words = await load_words()
-    leader = message.from_user
-
-    game["active"] = True
-    game["leader_id"] = leader.id
-    game["word"] = random.choice(words)
-    game["attempts"] = 0
+    word = random.choice(words)
+    game.start_round(word, leader)
 
     await message.answer(
-        f"🎮 Новый раунд!\nВедущий: {mention(leader)}",
-        reply_markup=leader_keyboard(leader.id)
+        f"🎮 Раунд запущен! Ведущий: {mention(leader)}\n"
+        "Слово отправлено в личку ведущему.",
+        reply_markup=leader_keyboard(leader.id),
     )
+    await notify_leader(word, leader)
+
+
+@dp.message(Command("startgame"))
+async def cmd_startgame(message: Message):
+    if not in_target_topic(message):
+        return
+
+    if game.active:
+        await message.answer("⚠️ Игра уже идёт. Используйте /status, чтобы посмотреть прогресс.")
+        return
+
+    await launch_round(message, message.from_user)
 
 
 @dp.message(Command("score"))
 async def cmd_score(message: Message):
     if not in_target_topic(message):
         return
 
-    if not scores:
+    rating = format_rating()
+    if not rating:
         await message.answer("📊 Пока нет очков.")
         return
 
-    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)
-    text = "\n".join(f"{i+1}. <code>{uid}</code> — {pts}" for i, (uid, pts) in enumerate(rating))
-
-    await message.answer("📊 <b>Рейтинг:</b>\n" + text)
+    await message.answer("📊 <b>Рейтинг:</b>\n" + rating)
 
 
 @dp.message(Command("top"))
 async def cmd_top(message: Message):
     if not in_target_topic(message):
         return
 
-    if not scores:
+    rating = format_rating(limit=10)
+    if not rating:
         await message.answer("🏆 Тут пока пусто.")
         return
 
-    rating = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]
-    text = "\n".join(f"{i+1}. <code>{uid}</code> — {pts}" for i, (uid, pts) in enumerate(rating))
-
-    await message.answer("🏆 <b>Топ-10:</b>\n" + text)
+    await message.answer("🏆 <b>Топ-10:</b>\n" + rating)
 
 
 @dp.message(Command("resetgame"))
 async def cmd_reset(message: Message):
     if not in_target_topic(message):
         return
 
     if not await is_admin(message.from_user.id):
         await message.answer("⛔ Только админ может сбросить игру.")
         return
 
     scores.clear()
-    game.update(active=False, leader_id=None, word=None, attempts=0)
+    game.reset()
 
     await message.answer("♻️ Игра и рейтинг полностью сброшены.")
 
 
 @dp.message(Command("hint"))
 async def cmd_hint(message: Message):
     if not in_target_topic(message):
         return
 
-    if not game["active"]:
+    if not game.active or not game.word:
         await message.answer("Сейчас не идёт игра.")
         return
 
-    if game["leader_id"] != message.from_user.id:
-        await message.answer("Подсказку может дать только ведущий.")
+    if game.leader_id != message.from_user.id:
+        await message.answer("Подсказку может дать только текущий ведущий.")
         return
 
-    word = game["word"]
-    hint = word[0] + " _" * (len(word) - 1)
+    if game.hint_level >= game.max_hints:
+        await message.answer("Все подсказки уже раскрыты. Пусть игроки постараются сами!")
+        return
 
-    await message.answer(
-        f"💡 Подсказка:\n"
-        f"Слово из {len(word)} букв\n"
-        f"<code>{hint}</code>"
-    )
+    game.hint_level += 1
+    hint_text = format_hint(game.word, game.hint_level)
+    await message.answer(f"💡 Подсказка #{game.hint_level}:\n{hint_text}")
 
 
 # ========= CALLBACK-КНОПКИ =========
-
 @dp.callback_query()
 async def process_buttons(call: CallbackQuery):
     if call.message.chat.id != CHAT_ID:
         return
 
-    # мягкая проверка темы
     if THREAD_ID != 0:
         thread = getattr(call.message, "message_thread_id", None)
         if thread is not None and thread != THREAD_ID:
             return
 
-    if not game["active"]:
+    if not game.active:
         await call.answer("Игра уже остановлена.", show_alert=True)
         return
 
     data = call.data or ""
-    action, leader_str = data.split(":")
+    if ":" not in data:
+        await call.answer()
+        return
+
+    action, leader_str = data.split(":", 1)
     leader_id = int(leader_str)
 
     if call.from_user.id != leader_id:
-        await call.answer("Вы не ведущий.", show_alert=True)
+        await call.answer("Эта кнопка только для ведущего.", show_alert=True)
         return
 
     if action == "show":
-        await call.answer(f"Слово: {game['word']}", show_alert=True)
+        await call.answer(f"Слово: {game.word}", show_alert=True)
         return
 
     if action == "replace":
         words = await load_words()
-        game["word"] = random.choice(words)
-        game["attempts"] = 0
-        await call.answer(f"Новое слово: {game['word']}", show_alert=True)
+        game.word = random.choice(words)
+        game.attempts = 0
+        game.hint_level = 0
+        game.revealed_positions.clear()
+        await notify_leader(game.word, call.from_user)
+        await call.message.answer("🔄 Ведущий сменил слово. Начинаем угадывать заново!")
+        await call.answer("Новое слово отправлено вам в личку.", show_alert=True)
         return
 
     if action == "stop":
         if not await is_admin(call.from_user.id):
             await call.answer("⛔ Только админ может остановить игру.", show_alert=True)
             return
-
-        game.update(active=False, leader_id=None, word=None, attempts=0)
-        await call.message.answer("⛔ Игра остановлена.")
+        game.reset()
+        await call.message.answer("⛔ Игра остановлена администратором.")
         await call.answer("Готово.", show_alert=True)
         return
 
 
 # ========= УГАДЫВАНИЕ =========
-
 @dp.message()
 async def guessing(message: Message):
     if not in_target_topic(message):
         return
 
-    if not game["active"] or not game["word"]:
+    if not game.active or not game.word:
         return
 
-    if message.from_user.id == game["leader_id"]:
+    if message.from_user.id == game.leader_id:
         return
 
     if not message.text:
         return
 
     guess = normalize(message.text)
-    answer = normalize(game["word"])
+    if not guess:
+        return
+
+    answer = normalize(game.word)
 
     if answer not in guess:
-        game["attempts"] += 1
+        game.attempts += 1
+        if game.attempts % ATTEMPTS_NOTIFY_STEP == 0:
+            await message.answer(
+                f"🙌 Уже {game.attempts} попыток! Ведущий может выдать подсказку /hint.")
+        await maybe_auto_hint(message)
         return
 
     # Угадано
-    uid = message.from_user.id
-    scores[uid] = scores.get(uid, 0) + 1
-
+    new_points = add_score(message.from_user)
     await message.answer(
-        f"🎉 {mention(message.from_user)} угадал слово <b>{game['word']}</b>!\n"
-        f"Теперь у него {scores[uid]} очков."
-    )
-
-    # Передаём ход
-    words = await load_words()
-    new_word = random.choice(words)
-
-    game["leader_id"] = uid
-    game["word"] = new_word
-    game["attempts"] = 0
-
-    await message.answer(
-        f"👉 Новый ведущий: {mention(message.from_user)}",
-        reply_markup=leader_keyboard(uid)
-    )
-
-@dp.message_handler()
-async def handle_guess(message: types.Message):
-    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
-        return
+        f"🎉 {mention(message.from_user)} угадал слово <b>{game.word}</b>!\n"
+        f"Теперь у него {new_points} очков.")
 
-    if not message.text:
-        return
-
-    # нормализация текста
-    text = re.sub(r"[^а-яa-z0-9ё]", " ", message.text.lower())
-    text = re.sub(r"\s+", " ", text).strip()
-
-    if not text:
-        return
-
-    game = await get_active_game(message.chat.id)
-    if not game or not game[5]:
-        return
-
-    chat_id, leader_id, leader_username, word, started_at, active = game
-
-    # нормализуем загаданное слово
-    word_normalized = re.sub(r"[^а-яa-z0-9ё]", "", word.lower())
-
-    # проверка вхождения как отдельного слова или полностью
-    if text == word_normalized or f" {word_normalized} " in f" {text} ":
-        await add_point(message.chat.id, message.from_user)
-
-        winner_mention = message.from_user.get_mention(as_html=True)
-        await message.reply(
-            f"Правильно! Слово было: <b>{word}</b>\n"
-            f"Очко получает {winner_mention}.\n"
-            "Следующий раунд начался — новое слово отправлено в личку.",
-            parse_mode="HTML",
-        )
-
-        await start_new_round(message.chat, message.from_user)
+    await launch_round(message, message.from_user)
 
 
 # ========= ЗАПУСК =========
-
 async def main():
     logger.info("🚀 Бот запущен!")
     await setup_bot_commands(bot)
     await dp.start_polling(bot)
 
 
 if __name__ == "__main__":
     asyncio.run(main())

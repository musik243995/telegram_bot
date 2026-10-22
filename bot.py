import random
import time
import os
import hmac
import hashlib
import json
import asyncio
import psycopg2
from psycopg2 import extensions as pg_ext
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor
from datetime import datetime
from urllib.parse import quote, parse_qsl
from aiogram import F, Bot, Dispatcher, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, FSInputFile
from aiogram.filters import Command, CommandObject
from aiohttp import web
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения Render")

bot = Bot(token=API_TOKEN)
router = Router()
dp = Dispatcher()
dp.include_router(router)

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_balance_change = State()
    waiting_for_coin_exchange = State()
    waiting_for_donate_pass = State()

# --- АНТИСПАМ ФИЛЬТРЫ ---
original_send_message = bot.send_message
original_send_photo = bot.send_photo

STOP_WORDS = [
    "special premium offers", "premium videos", "fresihbot_bot", 
    "video club", "strawberries", "referral reward levels", 
    "age confirmation", "terms of use", "100 stars =", "250 stars =", "500 stars =", "1000 stars ="
]

async def filtered_send_message(chat_id, text, *args, **kwargs):
    if text:
        text_lower = str(text).lower()
        if any(word in text_lower for word in STOP_WORDS):
            return None
    return await original_send_message(chat_id, text, *args, **kwargs)

async def filtered_send_photo(chat_id, photo, caption=None, *args, **kwargs):
    if caption:
        caption_lower = str(caption).lower()
        if any(word in caption_lower for word in STOP_WORDS):
            return None
    return await original_send_photo(chat_id, photo, caption=caption, *args, **kwargs)

bot.send_message = filtered_send_message
bot.send_photo = filtered_send_photo

work_cooldowns = {}
creator_salary_cooldowns = {}
game_cooldowns = {}
gang_rob_cooldowns = {}
BIG_TRANSFER_THRESHOLD = 10_000_000

ADMIN_IDS = [1222239198, 8390540110, 8565202662, 7145763697]
RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

# --- РЕЖИМ ТЕХНИЧЕСКИХ РАБОТ ---
MAINTENANCE_MODE = False

@router.message(Command("tech_on"))
async def enable_maintenance(message: Message):
    global MAINTENANCE_MODE
    if message.from_user.id not in ADMIN_IDS: return
    MAINTENANCE_MODE = True
    await message.answer("🚨 **Режим тех. работ ВКЛЮЧЕН.** Теперь бот отвечает только администраторам.", parse_mode="MARKDOWN")

@router.message(Command("tech_off"))
async def disable_maintenance(message: Message):
    global MAINTENANCE_MODE
    if message.from_user.id not in ADMIN_IDS: return
    MAINTENANCE_MODE = False
    await message.answer("✅ **Режим тех. работ ВЫКЛЮЧЕН.** Бот доступен всем.", parse_mode="MARKDOWN")

APARTMENT_NAMES = [
    "Убитая хрущевка на окраине", "Сталинка в центре под ремонт", 
    "Студия в новостройке с черновой отделкой", "Элитный пентхаус с видом на парк", 
    "Бабушатник со старыми коврами", "Апартаменты в бизнес-центре", 
    "Малосемейка с общим балконом", "Просторная евротрешка", 
    "Аварийная квартира в старом фонде", "Люксовая вилла на закрытой территории"
]

def get_private_keyboard(is_creator_flag=False):
    kb = [
        [KeyboardButton(text="Капуста💸")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🏆 Топ")],
        [KeyboardButton(text="🥷 Нычка"), KeyboardButton(text="🔫 Банда")],
        [KeyboardButton(text="🏪 Магазин"), KeyboardButton(text="⭐ Донат")],
        [KeyboardButton(text="Прочее")]
    ]
    if is_creator_flag:
        kb.append([KeyboardButton(text="🎬 Креатор")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cabbage_keyboard():
    kb = [
        [KeyboardButton(text="👷 Работа"), KeyboardButton(text="🎰 Казино")],
        [KeyboardButton(text="💸 Флипинг"), KeyboardButton(text="🏠 Квартиры")],
        [KeyboardButton(text="🏢 Компания по флипингу 💸"), KeyboardButton(text="🏦 Банк")],
        [KeyboardButton(text="🔙 Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_other_keyboard():
    kb = [
        [KeyboardButton(text="🎁 Бонус"), KeyboardButton(text="👥 Рефералы")],
        [KeyboardButton(text="📜 Квесты"), KeyboardButton(text="📖 Помощь")],
        [KeyboardButton(text="🪙 Обменять койны на баланс"), KeyboardButton(text="🏅 Достижения")],
        [KeyboardButton(text="🔙 Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="💰 Изменить баланс", callback_data="admin_balance")],
            [InlineKeyboardButton(text="🎁 Донат-пасс", callback_data="admin_donate_pass")],
            [InlineKeyboardButton(text="🚫 Баны / Креаторы", callback_data="admin_ban_menu")],
            [InlineKeyboardButton(text="📋 История игрока", callback_data="admin_history_help")]
        ]
    )

# --- БАЗА ДАННЫХ ---
DATABASE_URL = os.getenv("DATABASE_URL")

# Пул соединений: переиспользуем TCP/TLS/аутентификацию вместо
# установки нового соединения на каждый запрос (это давало задержку 1-2 c)
_db_pool = None
_idle_ts = {}  # id(conn) -> момент возврата в пул

def _init_pool():
    global _db_pool
    if _db_pool is None:
        _db_pool = ThreadedConnectionPool(
            1, 12,
            dsn=DATABASE_URL,
            connect_timeout=10,
            keepalives=1, keepalives_idle=30,
            keepalives_interval=10, keepalives_count=3,
        )
    return _db_pool

class PooledConnection:
    """Обёртка над соединением из пула.
    close() возвращает соединение в пул, а не закрывает его;
    __del__ подстраховывается, если соединение «забыли» закрыть."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def close(self):
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            p = _init_pool()
            if conn.closed:
                p.putconn(conn, close=True)
                return
            if conn.get_transaction_status() != pg_ext.TRANSACTION_STATUS_IDLE:
                conn.rollback()  # сбрасываем незакрытую транзакцию чтения
            _idle_ts[id(conn)] = time.time()
            p.putconn(conn)
        except Exception:
            try:
                if _db_pool is not None:
                    _db_pool.putconn(conn, close=True)
            except Exception:
                pass

def get_db():
    # Подключение к Supabase (PostgreSQL) через пул
    p = _init_pool()
    conn = p.getconn()
    ts = _idle_ts.pop(id(conn), None)
    if ts is not None and time.time() - ts > 30:
        # Пул мог сохранить отвалившееся соединение — проверяем живость
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
        except Exception:
            p.putconn(conn, close=True)
            conn = p.getconn()
    return PooledConnection(conn)

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            custom_name TEXT DEFAULT NULL,
            balance BIGINT DEFAULT 10000,
            bank_balance BIGINT DEFAULT 0,
            nyachka BIGINT DEFAULT 0,
            invested BIGINT DEFAULT 0,
            bottles INTEGER DEFAULT 0,
            metal INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            is_creator INTEGER DEFAULT 0,
            gang_name TEXT DEFAULT NULL,
            last_bank_calc REAL,
            last_bonus REAL DEFAULT 0,
            last_salary REAL DEFAULT 0,
            referrer_id BIGINT DEFAULT 0,
            ref_count INTEGER DEFAULT 0,
            stolen INTEGER DEFAULT 0,
            has_pet INTEGER DEFAULT 0,
            has_connections INTEGER DEFAULT 0,
            has_phone INTEGER DEFAULT 0,
            coins BIGINT DEFAULT 0
        )
    """)
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS cases_opened INTEGER DEFAULT 0")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_achievements (
            user_id BIGINT,
            code TEXT,
            claimed INTEGER DEFAULT 0,
            achieved_at REAL,
            PRIMARY KEY (user_id, code)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_sims (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            number TEXT,
            price BIGINT,
            category TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_sims (
            user_id BIGINT PRIMARY KEY,
            number TEXT,
            category TEXT,
            price BIGINT,
            created_at REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_sims (
            id SERIAL PRIMARY KEY,
            seller_id BIGINT,
            seller_name TEXT,
            sim_id INTEGER,
            number TEXT,
            price BIGINT,
            original_price BIGINT,
            category TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_apartments (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            apartment_name TEXT,
            price BIGINT,
            buy_time REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_companies (
            user_id BIGINT PRIMARY KEY,
            level INTEGER DEFAULT 1,
            last_claim REAL,
            earned_balance BIGINT DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            reward BIGINT,
            activations_left INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_promo (
            user_id BIGINT,
            code TEXT,
            PRIMARY KEY (user_id, code)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_quests (
            user_id BIGINT,
            day_num INTEGER,
            quest_id INTEGER,
            progress BIGINT DEFAULT 0,
            completed INTEGER DEFAULT 0,
            last_reset_date TEXT,
            PRIMARY KEY (user_id, day_num, quest_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            username TEXT,
            action_type TEXT,
            action_details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_inventory (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            item_name TEXT,
            price BIGINT,
            sold INTEGER DEFAULT 0,
            item_type TEXT DEFAULT 'normal'
        )
    """)
    # --- Батл-пасс ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS battle_pass (
            user_id BIGINT PRIMARY KEY,
            xp BIGINT DEFAULT 0,
            free_cases INTEGER DEFAULT 0,
            donate_pass INTEGER DEFAULT 0,
            claimed_normal TEXT DEFAULT '',
            claimed_donate TEXT DEFAULT ''
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS battle_pass_boosts (
            user_id BIGINT PRIMARY KEY,
            boost INTEGER DEFAULT 0,
            until_ts DOUBLE PRECISION DEFAULT 0
        )
    """)
    # Миграция старых таблиц: REAL (float32) на эпохе ~1.8e9 округляет с шагом
    # 128 секунд — таймер буста «врал». Идемпотентно переводим на точный double.
    cursor.execute("ALTER TABLE battle_pass_boosts ALTER COLUMN until_ts TYPE DOUBLE PRECISION USING until_ts::DOUBLE PRECISION")
    cursor.execute("ALTER TABLE battle_pass_settings ALTER COLUMN started_at TYPE DOUBLE PRECISION USING started_at::DOUBLE PRECISION")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS battle_pass_daily (
            user_id BIGINT,
            day TEXT,
            track TEXT,
            code TEXT,
            progress BIGINT DEFAULT 0,
            claimed INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, day, track, code)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS battle_pass_settings (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            started_at REAL NOT NULL
        )
    """)

    # Номера сим-карт должны быть уникальны во всей игре:
    # убираем старые дубликаты (оставляем самый ранний) и ставим индексы
    cursor.execute("DELETE FROM user_sims a USING user_sims b WHERE a.number = b.number AND a.id > b.id")
    cursor.execute("DELETE FROM market_sims a USING market_sims b WHERE a.number = b.number AND a.id > b.id")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_sims_number_uniq ON user_sims (number)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS market_sims_number_uniq ON market_sims (number)")

    conn.commit()
    conn.close()

def log_user_action(user_id, username, action_type, details):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO user_logs (user_id, username, action_type, action_details) VALUES (%s, %s, %s, %s)", (user_id, username, action_type, details))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Ошибка логирования: {e}")

def get_current_quest_day():
    epoch_days = int(time.time() // 86400)
    return (epoch_days % 3) + 1

def update_quest_progress(user_id, quest_type, amount=1):
    day_num = get_current_quest_day()
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT progress, completed FROM user_quests WHERE user_id = %s AND day_num = %s AND quest_id = %s", (user_id, day_num, quest_type))
    row = cursor.fetchone()
    
    if not row:
        cursor.execute("INSERT INTO user_quests (user_id, day_num, quest_id, progress, completed, last_reset_date) VALUES (%s, %s, %s, 0, 0, %s)", (user_id, day_num, quest_type, today_str))
        progress, completed = 0, 0
    else:
        progress, completed = row

    if completed == 1:
        conn.close()
        return

    new_progress = progress + amount
    reward, is_done = 0, 0

    if day_num == 1:
        if quest_type == 1 and new_progress >= 1_000_000: reward, is_done = 1_500_000, 1
        elif quest_type == 2 and new_progress >= 2_000_000: reward, is_done = 3_000_000, 1
        elif quest_type == 3 and new_progress >= 1: reward, is_done = 1_000_000, 1
    elif day_num == 2:
        if quest_type == 1 and new_progress >= 1: reward, is_done = 1_000_000, 1
        elif quest_type == 2 and new_progress >= 2_000_000: reward, is_done = 3_000_000, 1
        elif quest_type == 3 and new_progress >= 1: reward, is_done = 1_000_000, 1
    elif day_num == 3:
        if quest_type == 1 and new_progress >= 5: reward, is_done = 1_000_000, 1
        elif quest_type == 2 and new_progress >= 5: reward, is_done = 1_000_000, 1
        elif quest_type == 3 and new_progress >= 1_000_000: reward, is_done = 1_500_000, 1

    if is_done == 1 and completed == 0:
        cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (reward, user_id))
        cursor.execute("UPDATE user_quests SET progress = %s, completed = 1 WHERE user_id = %s AND day_num = %s AND quest_id = %s", (new_progress, user_id, day_num, quest_type))
    else:
        cursor.execute("UPDATE user_quests SET progress = %s WHERE user_id = %s AND day_num = %s AND quest_id = %s", (new_progress, user_id, day_num, quest_type))
    conn.commit()
    conn.close()

async def check_ban_and_register(message: Message, command: CommandObject = None):
    user = message.from_user
    user_id, username = user.id, user.username or user.first_name
    
    if MAINTENANCE_MODE and user_id not in ADMIN_IDS:
        await message.answer("🛠 **В боте ведутся технические работы!**\nПопробуйте зайти позже.", parse_mode="MARKDOWN")
        return False

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT banned, username FROM users WHERE user_id = %s", (user_id,))
        res = cursor.fetchone()

        if res is None:
            ref_id = 0
            if command and command.args and command.args.startswith("ref_"):
                try:
                    potential_ref = int(command.args.replace("ref_", ""))
                    if potential_ref != user_id:
                        cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (potential_ref,))
                        if cursor.fetchone(): 
                            ref_id = potential_ref
                except Exception:
                    pass

            cursor.execute(
                "INSERT INTO users (user_id, username, balance, last_bank_calc, referrer_id) VALUES (%s, %s, 10000, %s, %s)",
                (user_id, username, time.time(), ref_id)
            )
            if ref_id != 0:
                cursor.execute(
                    "UPDATE users SET balance = balance + 1000000, ref_count = ref_count + 1 WHERE user_id = %s",
                    (ref_id,)
                )

            conn.commit()
            if ref_id != 0:
                try:
                    await bot.send_message(
                        ref_id,
                        "🎉 По вашей реферальной ссылке зарегистрировался новый игрок! Вам начислено 1 000 000 ¢."
                    )
                except Exception:
                    pass
            return True

        elif res[0] == 1:
            await message.answer("❌ Вы заблокированы и не можете пользоваться ботом.")
            return False

        else:
            # Обновляем username только если он реально изменился
            # (экономит запись + commit на каждом сообщении)
            if res[1] != username:
                cursor.execute("UPDATE users SET username = %s WHERE user_id = %s", (username, user_id))
                conn.commit()
            return True
    finally:
        conn.close()

async def is_creator(user_id: int) -> bool:
    if user_id in ADMIN_IDS: return True
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_creator FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row and row[0] == 1

async def smart_answer(message: Message, text: str, reply_markup=None, parse_mode="MARKDOWN"):
    user_id = message.from_user.id
    creator_status = await is_creator(user_id)
    if message.chat.type == "private":
        kb = reply_markup if reply_markup else get_private_keyboard(creator_status)
        await message.answer(text, reply_markup=kb, parse_mode=parse_mode)
    else:
        await message.answer(text, reply_markup=ReplyKeyboardRemove(), parse_mode=parse_mode)

async def send_result_media(message: Message, is_win: bool, text: str):
    folder = "win" if is_win else "lose"
    files = [f for f in os.listdir(folder) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.mp4', '.gif'))] if os.path.exists(folder) else []
    if files:
        chosen_file = random.choice(files)
        path = os.path.join(folder, chosen_file)
        input_file = FSInputFile(path)
        try:
            if chosen_file.lower().endswith(('.mp4', '.gif')): await message.answer_animation(input_file, caption=text, parse_mode="MARKDOWN")
            else: await message.answer_photo(input_file, caption=text, parse_mode="MARKDOWN")
            return
        except: pass
    await message.answer(text, parse_mode="MARKDOWN")

def parse_sum(text_val):
    if not text_val: return None
    text_val = str(text_val).lower().strip().replace(" ", "")
    multiplier = 1
    if "кккк" in text_val or "tr" in text_val: multiplier, text_val = 1_000_000_000_000, text_val.replace("кккк", "").replace("tr", "")
    elif "ккк" in text_val or "b" in text_val: multiplier, text_val = 1_000_000_000, text_val.replace("ккк", "").replace("b", "")
    elif "кк" in text_val or "м" in text_val or "m" in text_val: multiplier, text_val = 1_000_000, text_val.replace("кк", "").replace("м", "").replace("m", "")
    elif "к" in text_val or "k" in text_val: multiplier, text_val = 1_000, text_val.replace("к", "").replace("k", "")
    try: return int(float(text_val) * multiplier)
    except: return None

@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    if not await check_ban_and_register(message, command): return
    await smart_answer(message, "🏠 Главное меню игры:\nДобро пожаловать! Используйте кнопки меню или команды.")

@router.message(F.text.casefold().in_(["главное меню", "меню", "🔙 назад", "назад"]))
async def text_main_menu(message: Message):
    if not await check_ban_and_register(message): return
    await smart_answer(message, "🏠 Главное меню игры:")

@router.message(F.text.casefold().in_(["капуста💸", "капуста"]))
async def text_cabbage_menu(message: Message):
    if not await check_ban_and_register(message): return
    await message.answer("💸 Раздел «Капуста💸»:\nВыберите нужный раздел из меню ниже.", reply_markup=get_cabbage_keyboard())

@router.message(F.text.casefold().in_(["прочее"]))
async def text_other_menu(message: Message):
    if not await check_ban_and_register(message): return
    await message.answer("⚙ Раздел «Прочее»:\nВыберите нужный раздел из меню ниже.", reply_markup=get_other_keyboard())

@router.message(F.text.casefold().in_(["🏅 достижения", "достижения"]))
async def msg_achievements(message: Message):
    if not await check_ban_and_register(message): return
    text, kb = build_achievements_menu(message.from_user.id)
    await message.answer(text, reply_markup=kb)

# --- ОБМЕН КОИНОВ НА БАЛАНС ЧЕРЕЗ БОТА ---
@router.message(F.text.casefold().in_(["🪙 обменять койны на баланс", "обменять койны на баланс", "обменять коины на баланс"]))
async def start_coin_exchange(message: Message, state: FSMContext):
    if not await check_ban_and_register(message): return
    await message.answer(
        "Сколько вы хотите поменять койнов на баланс?\n(1койн-500000¢)\n\n(Введите число или /cancel для отмены)",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(AdminStates.waiting_for_coin_exchange)

@router.message(AdminStates.waiting_for_coin_exchange)
async def process_coin_exchange(message: Message, state: FSMContext):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    
    if message.text.casefold() == "/cancel":
        await state.clear()
        await smart_answer(message, "❌ Отменено.")
        return

    try:
        coins_to_exchange = int(message.text.strip().replace(" ", ""))
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число койнов!")
        return

    if coins_to_exchange <= 0:
        await message.answer("❌ Количество должно быть больше нуля!")
        return

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COALESCE(coins, 0) FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        current_coins = row[0] if row else 0

        if current_coins < coins_to_exchange:
            await message.answer(f"❌ У вас недостаточно койнов! У вас в наличии: {current_coins} койнов.")
            return

        added_balance = coins_to_exchange * 500_000

        cursor.execute("""
            UPDATE users 
            SET coins = COALESCE(coins, 0) - %s, balance = balance + %s 
            WHERE user_id = %s AND COALESCE(coins, 0) >= %s
        """, (coins_to_exchange, added_balance, user_id, coins_to_exchange))
        conn.commit()

        await message.answer(f"Вам начислено {added_balance:,}¢ за {coins_to_exchange} койнов".replace(",", " "))
        await state.clear()
        await smart_answer(message, "🏠 Главное меню игры:")
    except Exception as e:
        conn.rollback()
        await message.answer("❌ Произошла ошибка при обмене.")
        await state.clear()
    finally:
        conn.close()

HELP_TEXT = (
    "📖 Инструкция по игре:\n\n"
    "🎰 Казино (пишите строго по шаблонам):\n"
    "• рул кра [ставка] (или чер, чет, нечет, мал, бол, ряд 1-3, число 0-36)\n"
    "• покер [ставка]\n"
    "• дартс [ставка] центр (или мимо)\n"
    "• баскет [ставка]\n\n"
    "💰 Переводы и Банк:\n"
    "• пер @юз сумма (или пер вб)\n"
    "• вложить [сумма] / снять [сумма]\n\n"
    "🥷 Нычка:\n"
    "• нычка положить [сумма]\n"
    "• нычка взять [сумма]\n\n"
    "💸 Раздача:\n"
    "• обранить [сумма] [кол-во людей]\n\n"
    "🔫 Банда и Работа:\n"
    "• Меню «Банда» — ограбление банка каждый час\n"
    "• Меню «Работа» — сбор бутылок и металла"
)

@router.message(F.text.casefold().in_(["помощь", "📖 помощь"]))
async def cmd_help(message: Message):
    if not await check_ban_and_register(message): return
    await message.answer(HELP_TEXT, parse_mode="MARKDOWN")

@router.message(F.text.casefold().in_(["донат", "⭐ донат"]))
async def text_donate(message: Message):
    if not await check_ban_and_register(message): return
    text = (
        "⭐ **Донат в игре**\n\n"
        "Привет! Если хочешь задонатить, вот цена:\n"
        "• **1 ⭐ = 25 000 000 ¢**\n\n"
        "🛒 *Покупка от 15 зв*\n"
        "✍️ Писать в ЛС к @shaptes"
    )
    await message.answer(text, parse_mode="MARKDOWN")

@router.message(F.text.casefold().in_(["магазин", "🏪 магазин"]))
async def text_shop(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT has_pet, has_connections FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    has_pet = row[0] if row else 0
    has_conn = row[1] if row else 0

    pet_btn_text = "✅ Куплено" if has_pet else "Купить питомца (35 млн)"
    conn_btn_text = "✅ Куплено" if has_conn else "Купить связи (50 млн)"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=pet_btn_text, callback_data="shop_buy_pet")],
        [InlineKeyboardButton(text=conn_btn_text, callback_data="shop_buy_conn")]
    ])
    text = (
        "🏪 **Магазин улучшений**\n\n"
        "🐾 **Питомца** (+10% к удаче на симку)\n"
        "Стоимость: 35 000 000 ¢\n\n"
        "📞 **Связи** (+15% к удаче на симку)\n"
        "Стоимость: 50 000 000 ¢"
    )
    await message.answer(text, reply_markup=kb, parse_mode="MARKDOWN")

@router.callback_query(F.data.in_(["shop_buy_pet", "shop_buy_conn"]))
async def shop_buy_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    is_pet = (callback.data == "shop_buy_pet")
    cost = 35_000_000 if is_pet else 50_000_000
    field_name = "has_pet" if is_pet else "has_connections"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(f"SELECT balance, {field_name} FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    balance, already_bought = row[0], row[1]
    if already_bought == 1:
        conn.close()
        await callback.answer("⚠️ У вас это уже куплено!", show_alert=True)
        return

    if balance < cost:
        conn.close()
        await callback.answer("У тя денег нет, ты шо 🤡", show_alert=True)
        return

    cursor.execute(f"UPDATE users SET balance = balance - %s, {field_name} = 1 WHERE user_id = %s", (cost, user_id))
    conn.commit()

    cursor.execute(f"SELECT has_pet, has_connections FROM users WHERE user_id = %s", (user_id,))
    r_new = cursor.fetchone()
    conn.close()

    hp, hc = r_new[0], r_new[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Куплено" if hp else "Купить питомца (35 млн)", callback_data="shop_buy_pet")],
        [InlineKeyboardButton(text="✅ Куплено" if hc else "Купить связи (50 млн)", callback_data="shop_buy_conn")]
    ])
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except:
        pass
    await callback.answer("🎉 Успешная покупка!", show_alert=True)

ACTIVE_DROPS = {}

@router.message(F.text.regexp(r"(?i)^обранить\s+(\d+[ккмилн]*)\s+(\d+)$"))
async def cmd_obran(message: Message, command: CommandObject):
    if not await check_ban_and_register(message): return
    parts = message.text.split()
    if len(parts) < 3:
        return await message.answer("❌ Формат: обранить [сумма] [кол-во людей]\nПример: обранить 10кк 10")
    
    raw_sum, count_str = parts[1], parts[2]
    amount = parse_sum(raw_sum)
    try:
        count = int(count_str)
    except:
        count = 0

    if not amount or amount <= 0 or count <= 0:
        return await message.answer("❌ Неверная сумма или количество людей!")

    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    row = cursor.fetchone()
    if not row or row[0] < amount:
        conn.close()
        return await message.answer("❌ У вас недостаточно наличных для этой раздачи!")

    cursor.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (amount, user_id))
    conn.commit()
    conn.close()

    drop_id = f"{user_id}_{int(time.time()*1000)}"
    per_person = amount // count

    ACTIVE_DROPS[drop_id] = {
        "owner_id": user_id,
        "total": amount,
        "slots_left": count,
        "per_user": per_person,
        "claimed": []
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Забрать деньги", callback_data=f"claim_{drop_id}")]
    ])

    uname = message.from_user.username or message.from_user.first_name
    text = (
        f"🎁 **Пользователь @{uname} обранил деньги!**\n\n"
        f"💰 Сумма: `{amount:,}` ¢\n"
        f"👥 Мест: `{count}` (по `{per_person:,}` ¢ на человека)\n"
        f"👇 Успей забрать первым!"
    ).replace(",", " ")
    await message.answer(text, reply_markup=kb, parse_mode="MARKDOWN")

@router.callback_query(F.data.startswith("claim_"))
async def callback_claim_drop(callback: CallbackQuery):
    drop_id = callback.data.split("_", 1)[1]
    drop = ACTIVE_DROPS.get(drop_id)
    if not drop:
        return await callback.answer("❌ Эта раздача уже завершена или истекла!", show_alert=True)

    user_id = callback.from_user.id
    if user_id in drop["claimed"]:
        return await callback.answer("❌ Вы уже забрали свой процент из этой раздачи!", show_alert=True)

    if drop["slots_left"] <= 0:
        return await callback.answer("❌ Все места уже разобрали!", show_alert=True)

    drop["claimed"].append(user_id)
    drop["slots_left"] -= 1

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (drop["per_user"], user_id))
    conn.commit()
    conn.close()

    await callback.answer(f"🎉 Вы успешно забрали {drop['per_user']:,} ¢!".replace(",", " "), show_alert=True)

    if drop["slots_left"] == 0:
        try:
            await callback.message.edit_text(callback.message.text + "\n\n✅ **Раздача полностью завершена!**", parse_mode="MARKDOWN")
        except:
            pass
        del ACTIVE_DROPS[drop_id]

@router.message(F.text.casefold().in_(["📜 квесты", "квесты"]))
async def text_quests(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    day_num = get_current_quest_day()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT quest_id, progress, completed FROM user_quests WHERE user_id = %s AND day_num = %s", (user_id, day_num))
    rows = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}
    conn.close()

    if day_num == 1: q1, q2, q3 = "Сыграй в казино на 1кк", "Сыграй в баскетбол на 2кк", "Введи промокод"
    elif day_num == 2: q1, q2, q3 = "Ограбь банк в банде", "Сыграй в дартс на 2кк", "Закинь в заначку"
    else: q1, q2, q3 = "Собери металл 5 раз", "Собери бутылки 5 раз", "Сыграй в покер на 1кк"

    def mark(qid): return "✅" if qid in rows and rows[qid][1] == 1 else "❌"
    text = f"📜 Ежедневные квесты (День {day_num}/3):\n\n1. {q1} — {mark(1)}\n2. {q2} — {mark(2)}\n3. {q3} — {mark(3)}"
    await message.answer(text, parse_mode="MARKDOWN")

@router.message(F.text.casefold().in_(["🥷 нычка", "нычка", "заначка"]))
async def text_nyachka_menu(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT nyachka FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    nyachka = row[0] if row and row[0] is not None else 0
    conn.close()
    text = f"🥷 Секретная нычка:\n💰 В нычке: {nyachka:,} ¢ (скрыто от топа)\n\nКоманды:\n• нычка положить [сумма]\n• нычка взять [сумма]".replace(",", " ")
    await message.answer(text, parse_mode="MARKDOWN")

@router.message(F.text.regexp(r"(?i)^нычка\s+(положить|взять|снять)\s+(.+)"))
async def nyachka_actions(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    parts = message.text.split()
    action, amount = parts[1].lower(), parse_sum(parts[2])
    if not amount or amount <= 0:
        await message.answer("❌ Неверная сумма!")
        return
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, nyachka FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    u_row = cursor.fetchone()
    bal, nyach = u_row[0], (u_row[1] or 0)
    
    if action == "положить":
        if bal < amount:
            await message.answer("❌ Недостаточно наличных!")
            conn.close()
            return
        cursor.execute("UPDATE users SET balance = balance - %s, nyachka = nyachka + %s WHERE user_id = %s", (amount, amount, user_id))
        conn.commit()
        conn.close()
        update_quest_progress(user_id, 3, amount if get_current_quest_day() == 2 else 1)
        await message.answer(f"🥷 Спрятано в нычку: {amount:,} ¢".replace(",", " "))
    else:
        if nyach < amount:
            await message.answer("❌ В нычке нет столько!")
            conn.close()
            return
        cursor.execute("UPDATE users SET nyachka = nyachka - %s, balance = balance + %s WHERE user_id = %s", (amount, amount, user_id))
        conn.commit()
        conn.close()
        await message.answer(f"🥷 Забрано из нычки: {amount:,} ¢".replace(",", " "))

@router.message(F.text.casefold().in_(["🔫 банда", "банда"]))
async def text_gang_menu(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT gang_name FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    gang = row[0] if row else None
    conn.close()
    
    gang_text = f"Ваша банда: {gang}" if gang else "Вы не состоите в банде."
    
    if not gang:
        kb_rows = [[InlineKeyboardButton(text="🔫 Вступить в банду", callback_data="join_gang")]]
    else:
        kb_rows = [[InlineKeyboardButton(text="👥 Братья", callback_data="gang_brothers")]]
    
    kb_rows.append([InlineKeyboardButton(text="🏦 Ограбить банк (КД 1ч)", callback_data="gang_rob_bank")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer(f"🔫 Бандитская система:\n\n{gang_text}\n\nОграбление банка доступно раз в час. Шанс успеха 80% (награда 1.5 млн ¢).", reply_markup=kb)

@router.callback_query(F.data == "join_gang")
async def callback_join_gang(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET gang_name = 'Уличные волки' WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Братья", callback_data="gang_brothers")],
        [InlineKeyboardButton(text="🏦 Ограбить банк (КД 1ч)", callback_data="gang_rob_bank")]
    ])
    try:
        await callback.message.edit_text("🔫 Бандитская система:\n\nВаша банда: Уличные волки\n\nОграбление банка доступно раз в час. Шанс успеха 80% (награда 1.5 млн ¢).", reply_markup=kb)
    except: pass
    await callback.answer("🔫 Вы вступили в банду 'Уличные волки'!", show_alert=True)

@router.callback_query(F.data == "gang_brothers")
async def callback_gang_brothers(callback: CallbackQuery):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT custom_name, username FROM users WHERE gang_name = 'Уличные волки'")
    members = cursor.fetchall()
    conn.close()
    
    text = "👥 Братья по банде 'Уличные волки':\n\n"
    for idx, (c_name, uname) in enumerate(members, 1):
        name = c_name if c_name else (uname if uname else "Брат")
        text += f"{idx}. {name}\n"
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="gang_back")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except: pass
    await callback.answer()

@router.callback_query(F.data == "gang_back")
async def callback_gang_back(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT gang_name FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    gang = row[0] if row else None
    
    gang_text = f"Ваша банда: {gang}" if gang else "Вы не состоите в банде."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Братья", callback_data="gang_brothers")],
        [InlineKeyboardButton(text="🏦 Ограбить банк (КД 1ч)", callback_data="gang_rob_bank")]
    ])
    try:
        await callback.message.edit_text(f"🔫 Бандитская система:\n\n{gang_text}\n\nОграбление банка доступно раз в час. Шанс успеха 80% (награда 1.5 млн ¢).", reply_markup=kb)
    except: pass
    await callback.answer()

@router.callback_query(F.data == "gang_rob_bank")
async def callback_gang_rob(callback: CallbackQuery):
    user_id = callback.from_user.id
    now = time.time()
    if now - gang_rob_cooldowns.get(user_id, 0) < 3600:
        await callback.answer("⏳ Банк можно грабить раз в час!", show_alert=True)
        return
    gang_rob_cooldowns[user_id] = now
    success = random.random() < 0.8
    conn = get_db()
    cursor = conn.cursor()
    if success:
        payout = 1_500_000
        cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (payout, user_id))
        conn.commit()
        conn.close()
        update_quest_progress(user_id, 1, 1)
        await callback.answer(f"🎉 Успех! Украдено {payout:,} ¢!".replace(",", " "), show_alert=True)
    else:
        conn.close()
        await callback.answer("🚨 Вас поймала полиция!", show_alert=True)

# --- ПРОФИЛЬ (ВКЛЮЧАЯ КОИНЫ) ---
def format_profile(user_data):
    user_id, display_name, balance, bank_balance, invested, apt_count, ref_count, is_creat, coins = user_data
    badge = " 🎬" if is_creat == 1 else ""
    return (
        f"👤 Профиль игрока: {display_name}{badge}\n\n"
        f"💵 Баланс наличными: {balance:,} ¢ ({coins} койнов)\n"
        f"🏦 В банке: {bank_balance:,} ¢\n"
        f"📈 Вложено во флипинг: {invested:,} ¢\n"
        f"🏢 Недвижимость: {apt_count}/10 шт.\n"
        f"👥 Приглашено друзей: {ref_count} шт."
    ).replace(",", " ")

# --- ДОСТИЖЕНИЯ ---
ACHIEVEMENTS = {
    "first_case":  {"emoji": "🥉", "name": "Первый кейс",     "desc": "Открыть первый кейс",          "reward": 500_000},
    "cases_10":    {"emoji": "🥈", "name": "Кейсоман",         "desc": "Открыть 10 кейсов",            "reward": 2_000_000},
    "cases_100":   {"emoji": "🥇", "name": "Кейсный магнат",   "desc": "Открыть 100 кейсов",           "reward": 15_000_000},
    "rich_1m":     {"emoji": "💵", "name": "Миллионер",        "desc": "Накопить 1 000 000 ¢",         "reward": 1_000_000},
    "rich_100m":   {"emoji": "💰", "name": "Олигарх",          "desc": "Накопить 100 000 000 ¢",       "reward": 10_000_000},
    "first_sim":   {"emoji": "📱", "name": "Сим-владелец",     "desc": "Сохранить первую сим-карту",   "reward": 2_000_000},
    "sim_legend":  {"emoji": "🌟", "name": "Легенда связи",    "desc": "Выбить легендарную сим-карту", "reward": 25_000_000},
    "market_buy":  {"emoji": "🛒", "name": "Покупатель рынка", "desc": "Купить сим-карту на рынке",    "reward": 5_000_000},
    "upgrade_win": {"emoji": "🎯", "name": "Апгрейдер",        "desc": "Победить в апгрейдере",        "reward": 5_000_000},
    "case_legend": {"emoji": "💎", "name": "Везунчик",         "desc": "Выбить предмет с шансом 5%",   "reward": 30_000_000},
}


def check_achievements(cursor, user_id, extra=()):
    """Отмечает выполненные достижения. Вызывать внутри открытой транзакции."""
    codes = {c for c in extra if c in ACHIEVEMENTS}
    cursor.execute("SELECT COALESCE(cases_opened, 0), COALESCE(balance, 0) FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    if row:
        if row[0] >= 1: codes.add("first_case")
        if row[0] >= 10: codes.add("cases_10")
        if row[0] >= 100: codes.add("cases_100")
        if row[1] >= 1_000_000: codes.add("rich_1m")
        if row[1] >= 100_000_000: codes.add("rich_100m")
    cursor.execute("SELECT 1 FROM user_sims WHERE user_id = %s LIMIT 1", (user_id,))
    if cursor.fetchone():
        codes.add("first_sim")
    for code in codes:
        cursor.execute(
            "INSERT INTO user_achievements (user_id, code, claimed, achieved_at) VALUES (%s, %s, 0, %s) "
            "ON CONFLICT (user_id, code) DO NOTHING",
            (user_id, code, time.time()))


def get_user_medals(cursor, user_id, limit=3):
    cursor.execute(
        "SELECT code FROM user_achievements WHERE user_id = %s AND claimed = 1 ORDER BY achieved_at LIMIT %s",
        (user_id, limit))
    return " ".join(ACHIEVEMENTS[r[0]]["emoji"] for r in cursor.fetchall() if r[0] in ACHIEVEMENTS)

@router.message(F.text.casefold().in_(["👤 профиль", "я"]))
async def msg_profile(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT custom_name, username, balance, bank_balance, invested, is_creator, COALESCE(coins, 0) FROM users WHERE user_id = %s", (user_id,))
    u = cursor.fetchone()
    custom_name, uname, balance, bank_balance, invested, is_creat, coins = u if u else (None, "user", 10000, 0, 0, 0, 0)
    display_name = custom_name if custom_name else (uname if uname else message.from_user.first_name)
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = %s", (user_id,))
    apt_count = cursor.fetchone()[0]
    cursor.execute("SELECT ref_count FROM users WHERE user_id = %s", (user_id,))
    ref_count = cursor.fetchone()[0] or 0
    medals = get_user_medals(cursor, user_id)
    conn.close()
    display_name = f"{display_name} {medals}" if medals else display_name
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏅 Достижения", callback_data="achievements_menu")]
    ])
    await message.answer(format_profile((user_id, display_name, balance, bank_balance, invested, apt_count, ref_count, is_creat, coins)), reply_markup=kb)


def build_achievements_menu(user_id):
    conn = get_db()
    cursor = conn.cursor()
    check_achievements(cursor, user_id)  # обновить статусы по текущим данным
    cursor.execute("SELECT code, claimed FROM user_achievements WHERE user_id = %s", (user_id,))
    state = {r[0]: r[1] for r in cursor.fetchall()}
    conn.commit()
    conn.close()
    lines = ["🏅 Достижения — выполняйте цели и забирайте награды!\n"]
    buttons = []
    for code, a in ACHIEVEMENTS.items():
        st = state.get(code, -1)
        if st == 1:
            lines.append(f"{a['emoji']} {a['name']} — ✅ получено")
        elif st == 0:
            lines.append(f"{a['emoji']} {a['name']} — 🎁 награда готова!")
            buttons.append([InlineKeyboardButton(text=f"🎁 Забрать «{a['name']}» (+{a['reward']:,} ¢)".replace(",", " "), callback_data=f"ach_claim_{code}")])
        else:
            lines.append(f"{a['emoji']} {a['name']} — 🔒 {a['desc']}")
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="ach_back")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "achievements_menu")
async def cb_achievements_menu(callback: CallbackQuery):
    text, kb = build_achievements_menu(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        try:
            await callback.message.answer(text, reply_markup=kb)
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data.startswith("ach_claim_"))
async def cb_ach_claim(callback: CallbackQuery):
    code = callback.data[len("ach_claim_"):]
    if code not in ACHIEVEMENTS:
        await callback.answer("❌ Достижение не найдено", show_alert=True)
        return
    user_id = callback.from_user.id
    a = ACHIEVEMENTS[code]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE user_achievements SET claimed = 1 WHERE user_id = %s AND code = %s AND claimed = 0",
                   (user_id, code))
    if cursor.rowcount == 0:
        conn.close()
        await callback.answer("❌ Это достижение уже забрано или ещё не выполнено", show_alert=True)
        return
    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (a["reward"], user_id))
    check_achievements(cursor, user_id)  # новые достижения могли разблокироваться
    conn.commit()
    conn.close()
    await callback.answer(f"🎉 «{a['name']}» получено! +{a['reward']:,} ¢".replace(",", " "), show_alert=True)
    text, kb = build_achievements_menu(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


@router.callback_query(F.data == "ach_back")
async def cb_ach_back(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT custom_name, username, balance, bank_balance, invested, is_creator, COALESCE(coins, 0) FROM users WHERE user_id = %s", (user_id,))
    u = cursor.fetchone()
    custom_name, uname, balance, bank_balance, invested, is_creat, coins = u if u else (None, "user", 10000, 0, 0, 0, 0)
    display_name = custom_name if custom_name else (uname if uname else callback.from_user.first_name)
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = %s", (user_id,))
    apt_count = cursor.fetchone()[0]
    cursor.execute("SELECT ref_count FROM users WHERE user_id = %s", (user_id,))
    ref_count = cursor.fetchone()[0] or 0
    medals = get_user_medals(cursor, user_id)
    conn.close()
    display_name = f"{display_name} {medals}" if medals else display_name
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏅 Достижения", callback_data="achievements_menu")]
    ])
    try:
        await callback.message.edit_text(format_profile((user_id, display_name, balance, bank_balance, invested, apt_count, ref_count, is_creat, coins)), reply_markup=kb)
    except Exception:
        pass
    await callback.answer()

@router.message(F.text.casefold() == "чекнуть")
async def msg_check_profile(message: Message):
    if not await check_ban_and_register(message): return
    if not message.reply_to_message:
        await message.answer("❌ Ответьте этой командой на сообщение игрока.")
        return
    target_id = message.reply_to_message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT custom_name, username, balance, bank_balance, invested, is_creator, COALESCE(coins, 0) FROM users WHERE user_id = %s", (target_id,))
    u = cursor.fetchone()
    if not u:
        await message.answer("❌ Игрок не найден.")
        conn.close()
        return
    custom_name, uname, balance, bank_balance, invested, is_creat, coins = u
    display_name = custom_name if custom_name else (uname if uname else message.reply_to_message.from_user.first_name)
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = %s", (target_id,))
    apt_count = cursor.fetchone()[0]
    cursor.execute("SELECT ref_count FROM users WHERE user_id = %s", (target_id,))
    ref_count = cursor.fetchone()[0] or 0
    conn.close()
    await smart_answer(message, format_profile((target_id, display_name, balance, bank_balance, invested, apt_count, ref_count, is_creat, coins)))

@router.message(F.text.regexp(r"(?i)^\+ник\s+.+$"))
async def set_custom_nickname(message: Message):
    if not await check_ban_and_register(message): return
    new_name = message.text.split(maxsplit=1)[1].strip()
    if len(new_name) > 20:
        await message.answer("❌ Ник должен быть не длиннее 20 символов.")
        return
    if len(new_name) < 1:
        await message.answer("❌ Укажи ник. Пример: +ник чапа")
        return
    user_id = message.from_user.id
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("UPDATE users SET custom_name = %s WHERE user_id = %s", (new_name, user_id))
    conn.commit(); conn.close()
    log_user_action(user_id, message.from_user.username or message.from_user.first_name, "Профиль", f"Установил ник: {new_name}")
    await message.answer(f"✅ Твой ник теперь: {new_name}\n🏆 В топе будет показываться именно он.")

@router.message(F.text.casefold().in_(["🏆 топ", "топ", "топ игроков"]))
async def text_top(message: Message):
    if not await check_ban_and_register(message): return
    conn = get_db()
    cursor = conn.cursor()
    placeholders = ','.join(['%s'] * len(ADMIN_IDS)) if ADMIN_IDS else '0'
    cursor.execute(
        f"SELECT custom_name, username, (balance + bank_balance + invested) as total, is_creator "
        f"FROM users WHERE user_id NOT IN ({placeholders}) ORDER BY total DESC LIMIT 10", 
        tuple(ADMIN_IDS)
    )
    top_list = cursor.fetchall()
    conn.close()
    
    text = "🏆 Топ-10 богатейших игроков:\n\n"
    if not top_list:
        text += "Пока нет игроков для отображения."
    for idx, (custom_name, uname, total, is_creat) in enumerate(top_list, 1):
        display_name = custom_name if custom_name else (uname if uname else "Игрок")
        safe_name = display_name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")
        badge = " 🎬" if is_creat == 1 else ""
        text += f"{idx}. {safe_name}{badge} — {total:,} ¢\n".replace(",", " ")
        
    await smart_answer(message, text)

@router.message(F.text.casefold().in_(["👷 работа", "работа", "ферма"]))
async def text_work(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = %s", (user_id,))
    u = cursor.fetchone()
    conn.close()
    balance, bottles, metal, stolen = (u[0], u[1], u[2], u[3] or 0) if u else (10000, 0, 0, 0)

    text = (f"👷 Центр сбора ресурсов:\n\n"
            f"🍾 Бутылок: {bottles} шт.\n"
            f"⚙️ Металла: {metal} шт.\n"
            f"🥷 Украдено: {stolen} шт.\n"
            f"💰 Наличные: {balance:,} ¢").replace(",", " ")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍾 Собирать бутылки", callback_data="work_bottles")],
        [InlineKeyboardButton(text="⚙ Собирать металл", callback_data="work_metal")],
        [InlineKeyboardButton(text="🥷 Воровать (КД 6с)", callback_data="work_steal")],
        [InlineKeyboardButton(text="💰 Продать ресурсы", callback_data="sell_resources")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="MARKDOWN")

@router.callback_query(F.data == "work_bottles")
async def work_bottles(callback: CallbackQuery):
    user_id = callback.from_user.id
    now = time.time()
    last = work_cooldowns.get(f"b_{user_id}", 0)
    if now - last < 2:
        await callback.answer(f"⏳ Подождите еще {round(2 - (now - last), 1)} сек.!", show_alert=True)
        return
    work_cooldowns[f"b_{user_id}"] = now
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET bottles = bottles + 1 WHERE user_id = %s", (user_id,))
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = %s", (user_id,))
    bal, b, m, st = cursor.fetchone()
    conn.commit(); conn.close()
    update_quest_progress(user_id, 2, 1)
    log_user_action(user_id, callback.from_user.username or callback.from_user.first_name, "Работа", "Собрал 1 бутылку")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍾 Собирать бутылки", callback_data="work_bottles")],
        [InlineKeyboardButton(text="⚙ Собирать металл", callback_data="work_metal")],
        [InlineKeyboardButton(text="🥷 Воровать (КД 6с)", callback_data="work_steal")],
        [InlineKeyboardButton(text="💰 Продать ресурсы", callback_data="sell_resources")]
    ])
    try: await callback.message.edit_text(f"👷 Центр сбора ресурсов:\n\n🍾 Бутылок: {b} шт.\n⚙️ Металла: {m} шт.\n🥷 Украдено: {st} шт.\n💰 Наличные: {bal:,} ¢".replace(",", " "), reply_markup=keyboard)
    except: pass
    await callback.answer("🍾 Вы нашли бутылку!")

@router.callback_query(F.data == "work_metal")
async def work_metal(callback: CallbackQuery):
    user_id = callback.from_user.id
    now = time.time()
    last = work_cooldowns.get(f"m_{user_id}", 0)
    if now - last < 4:
        await callback.answer(f"⏳ Подождите еще {round(4 - (now - last), 1)} сек.!", show_alert=True)
        return
    work_cooldowns[f"m_{user_id}"] = now
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("UPDATE users SET metal = metal + 1 WHERE user_id = %s", (user_id,))
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = %s", (user_id,))
    bal, b, m, st = cursor.fetchone()
    conn.commit(); conn.close()
    update_quest_progress(user_id, 1, 1)
    log_user_action(user_id, callback.from_user.username or callback.from_user.first_name, "Работа", "Собрал 1 металл")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍾 Собирать бутылки", callback_data="work_bottles")],
        [InlineKeyboardButton(text="⚙ Собирать металл", callback_data="work_metal")],
        [InlineKeyboardButton(text="🥷 Воровать (КД 6с)", callback_data="work_steal")],
        [InlineKeyboardButton(text="💰 Продать ресурсы", callback_data="sell_resources")]
    ])
    try: await callback.message.edit_text(f"👷 Центр сбора ресурсов:\n\n🍾 Бутылок: {b} шт.\n⚙️ Металла: {m} шт.\n🥷 Украдено: {st} шт.\n💰 Наличные: {bal:,} ¢".replace(",", " "), reply_markup=keyboard)
    except: pass
    await callback.answer("⚙️ Металл найден!")

@router.callback_query(F.data == "work_steal")
async def work_steal(callback: CallbackQuery):
    user_id = callback.from_user.id
    now = time.time()
    last = work_cooldowns.get(f"s_{user_id}", 0)
    if now - last < 6:
        await callback.answer(f"⏳ Подождите еще {round(6 - (now - last), 1)} сек.!", show_alert=True)
        return
    work_cooldowns[f"s_{user_id}"] = now
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("UPDATE users SET stolen = COALESCE(stolen, 0) + 1 WHERE user_id = %s", (user_id,))
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = %s", (user_id,))
    bal, b, m, st = cursor.fetchone()
    conn.commit(); conn.close()
    log_user_action(user_id, callback.from_user.username or callback.from_user.first_name, "Работа", "Украл 1 предмет стоимостью 10 000 ¢")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍾 Собирать бутылки", callback_data="work_bottles")],
        [InlineKeyboardButton(text="⚙ Собирать металл", callback_data="work_metal")],
        [InlineKeyboardButton(text="🥷 Воровать (КД 6с)", callback_data="work_steal")],
        [InlineKeyboardButton(text="💰 Продать ресурсы", callback_data="sell_resources")]
    ])
    try: await callback.message.edit_text(f"👷 Центр сбора ресурсов:\n\n🍾 Бутылок: {b} шт.\n⚙️ Металла: {m} шт.\n🥷 Украдено: {st} шт.\n💰 Наличные: {bal:,} ¢", reply_markup=keyboard)
    except: pass
    await callback.answer("🥷 Вы украли 1 предмет! Его стоимость: 10 000 ¢")

@router.callback_query(F.data == "sell_resources")
async def sell_resources(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close(); await callback.answer("❌ Игрок не найден!", show_alert=True); return
    bal, b, m, st = row[0], row[1], row[2], row[3]
    if b == 0 and m == 0 and st == 0:
        conn.close(); await callback.answer("❌ Нет ресурсов для продажи!", show_alert=True); return
    payout = (b * 1000) + (m * 5000) + (st * 10000)
    cursor.execute("UPDATE users SET balance = balance + %s, bottles = 0, metal = 0, stolen = 0 WHERE user_id = %s", (payout, user_id))
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = %s", (user_id,))
    new_bal, nb, nm, ns = cursor.fetchone()
    conn.commit(); conn.close()
    log_user_action(user_id, callback.from_user.username or callback.from_user.first_name, "Работа", f"Продал ресурсы на {payout:,} ¢")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍾 Собирать бутылки", callback_data="work_bottles")],
        [InlineKeyboardButton(text="⚙ Собирать металл", callback_data="work_metal")],
        [InlineKeyboardButton(text="🥷 Воровать (КД 6с)", callback_data="work_steal")],
        [InlineKeyboardButton(text="💰 Продать ресурсы", callback_data="sell_resources")]
    ])
    try: await callback.message.edit_text(f"👷 Центр сбора ресурсов:\n\n🍾 Бутылок: {nb} шт.\n⚙️ Металла: {nm} шт.\n🥷 Украдено: {ns} шт.\n💰 Наличные: {new_bal:,} ¢".replace(",", " "), reply_markup=keyboard)
    except: pass
    await callback.answer(f"💰 Продано на {payout:,} ¢!".replace(",", " "), show_alert=True)

COMPANY_LEVELS = {
    1: {"name": "Стартовое агентство", "cost": 100_000_000, "income": 2_500_000, "next_cost": 250_000_000, "next_income": 10_000_000},
    2: {"name": "Районная сеть филиалов", "cost": 250_000_000, "income": 10_000_000, "next_cost": 500_000_000, "next_income": 25_000_000},
    3: {"name": "Корпорация недвижимости", "cost": 500_000_000, "income": 25_000_000, "next_cost": 1_000_000_000, "next_income": 50_000_000},
    4: {"name": "Империя застройки города", "cost": 1_000_000_000, "income": 50_000_000, "next_cost": 2_500_000_000, "next_income": 125_000_000},
    5: {"name": "Международный холдинг", "cost": 2_500_000_000, "income": 125_000_000, "next_cost": 0, "next_income": 0}
}

def get_company_data_and_keyboard(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT level, last_claim, earned_balance FROM user_companies WHERE user_id = %s", (user_id,))
    comp = cursor.fetchone()
    conn.close()
    
    if not comp:
        text = (
            f"🏢 Компания по флипингу 💸\n\n"
            f"У вас еще нет собственной компании.\n"
            f"💰 Стоимость покупки: 100 000 000 ¢\n"
            f"📈 Начальный доход: 2 500 000 ¢ в час (до 24 часов накопительно)."
        ).replace(",", " ")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Купить компанию (100 млн)", callback_data="buy_company")]
        ])
        return text, keyboard
    else:
        level, last_claim, earned_bal = comp
        now = time.time()
        hours_passed = (now - last_claim) / 3600
        if hours_passed > 24: hours_passed = 24
            
        current_income_per_hour = COMPANY_LEVELS[level]["income"]
        accrued = int(hours_passed * current_income_per_hour)
        total_available = earned_bal + accrued
        
        lvl_info = COMPANY_LEVELS[level]
        if level < 5:
            next_lvl_data = COMPANY_LEVELS[level + 1]
            next_upgrade_text = f"📈 Прокачать до {level+1} ур. за {next_lvl_data['cost']:,} ¢ (будет давать {next_lvl_data['income']:,} ¢/ч)".replace(",", " ")
            upgrade_btn = InlineKeyboardButton(text="📈 Прокачать компанию", callback_data="upgrade_company")
        else:
            next_upgrade_text = "⭐️ У вас максимальный 5 уровень компании!"
            upgrade_btn = None

        text = (
            f"🏢 Компания по флипингу 💸\n\n"
            f"• Статус: {lvl_info['name']} (Уровень {level}/5)\n"
            f"• Доход: {current_income_per_hour:,} ¢ в час\n"
            f"• Накоплено прибыли: {total_available:,} ¢ (лимит 24ч)\n\n"
            f"{next_upgrade_text}"
        ).replace(",", " ")
        
        # Сделайте так:
        kb_rows = [[InlineKeyboardButton(text="💶 Забрать прибыль", callback_data="company_get_profit")]]
        if upgrade_btn: kb_rows.append([upgrade_btn])
        keyboard = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        return text, keyboard

@router.message(F.text.casefold().in_(["компания", "компания по флипингу", "🏢 компания по флипингу 💸"]))
async def text_company_menu(message: Message):
    if not await check_ban_and_register(message): return
    text, keyboard = get_company_data_and_keyboard(message.from_user.id)
    await message.answer(text, reply_markup=keyboard, parse_mode="MARKDOWN")

@router.callback_query(F.data == "buy_company")
async def callback_buy_company(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    # Блокируем строку пользователя до конца транзакции: две параллельные покупки не спишут баланс дважды
    cursor.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    cursor.execute("SELECT 1 FROM user_companies WHERE user_id = %s", (user_id,))
    if cursor.fetchone():
        conn.close()
        await callback.answer("⚠️ У вас уже есть компания!", show_alert=True)
        return
    cursor.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
    bal = cursor.fetchone()[0]
    cost = 100_000_000
    if bal < cost:
        await callback.answer("❌ У вас недостаточно наличных для покупки компании (нужно 100 млн ¢)!", show_alert=True)
        conn.close()
        return
    cursor.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (cost, user_id))
    cursor.execute("INSERT INTO user_companies (user_id, level, last_claim, earned_balance) VALUES (%s, 1, %s, 0) ON CONFLICT (user_id) DO UPDATE SET level = 1, last_claim = EXCLUDED.last_claim, earned_balance = 0", (user_id, time.time()))
    conn.commit()
    conn.close()
    await callback.answer("🎉 Вы успешно купили компанию по флипингу 1 уровня!", show_alert=True)
    text, keyboard = get_company_data_and_keyboard(user_id)
    try: await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except: pass

@router.callback_query(F.data == "company_get_profit")
async def callback_claim_company(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT level, last_claim, earned_balance FROM user_companies WHERE user_id = %s", (user_id,))
    comp = cursor.fetchone()
    if not comp:
        await callback.answer("❌ У вас нет компании.", show_alert=True)
        conn.close()
        return
    level, last_claim, earned_bal = comp
    now = time.time()
    hours_passed = (now - last_claim) / 3600
    if hours_passed > 24: hours_passed = 24
    current_income_per_hour = COMPANY_LEVELS[level]["income"]
    accrued = int(hours_passed * current_income_per_hour)
    total_to_claim = earned_bal + accrued
    if total_to_claim <= 0:
        await callback.answer("❌ Пока еще ничего не накопилось!", show_alert=True)
        conn.close()
        return
    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (total_to_claim, user_id))
    cursor.execute("UPDATE user_companies SET last_claim = %s, earned_balance = 0 WHERE user_id = %s", (now, user_id))
    conn.commit()
    conn.close()
    await callback.answer(f"💶 Вы успешно забрали прибыль: +{total_to_claim:,} ¢!".replace(",", " "), show_alert=True)
    text, keyboard = get_company_data_and_keyboard(user_id)
    try: await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except: pass

@router.callback_query(F.data == "upgrade_company")
async def callback_upgrade_company(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT level, last_claim, earned_balance FROM user_companies WHERE user_id = %s", (user_id,))
    comp = cursor.fetchone()
    if not comp:
        await callback.answer("❌ У вас нет компании.", show_alert=True)
        conn.close()
        return
    level, last_claim, earned_bal = comp
    if level >= 5:
        await callback.answer("⭐️ У вас уже максимальный уровень компании!", show_alert=True)
        conn.close()
        return
    now = time.time()
    hours_passed = (now - last_claim) / 3600
    if hours_passed > 24: hours_passed = 24
    accrued = int(hours_passed * COMPANY_LEVELS[level]["income"])
    total_earned = earned_bal + accrued
    next_level = level + 1
    upgrade_cost = COMPANY_LEVELS[next_level]["cost"]
    cursor.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    bal = cursor.fetchone()[0]
    if bal < upgrade_cost:
        await callback.answer(f"❌ Недостаточно средств для прокачки! Нужно {upgrade_cost:,} ¢".replace(",", " "), show_alert=True)
        conn.close()
        return
    cursor.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (upgrade_cost, user_id))
    cursor.execute("UPDATE user_companies SET level = %s, last_claim = %s, earned_balance = %s WHERE user_id = %s", (next_level, now, total_earned, user_id))
    conn.commit()
    conn.close()
    await callback.answer(f"📈 Компания успешно прокачана до {next_level} уровня!", show_alert=True)
    text, keyboard = get_company_data_and_keyboard(user_id)
    try: await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except: pass

@router.message(F.text.casefold().in_(["🎰 казино", "казино"]))
async def text_casino(message: Message):
    if not await check_ban_and_register(message): return
    text = (
        "🎰 Казино — точный синтаксис ставок:\n\n"
        "• рул кра [ставка] (х2)\n"
        "• рул чер [ставка] (х2)\n"
        "• рул чет [ставка] (х2)\n"
        "• рул нечет [ставка] (х2)\n"
        "• рул мал [ставка] (1-18, х2)\n"
        "• рул бол [ставка] (19-36, х2)\n"
        "• рул ряд 1 / ряд 2 / ряд 3 [ставка] (х3)\n"
        "• [число 0-36] [ставка] (х36)\n\n"
        "• покер [ставка]\n"
        "• дартс [ставка] центр (или мимо)\n"
        "• баскет [ставка]"
    )
    await message.answer(text, parse_mode="MARKDOWN")

@router.message(F.text.regexp(r"(?i)^рул\s+.+"))
async def casino_roulette(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) < 3:
        await message.answer("❌ Ошибка формата! Пример: рул кра 10к")
        return

    target_str = args[1].lower()
    if target_str == "ряд":
        if len(args) < 4 or args[2] not in ["1", "2", "3"]:
            await message.answer("❌ Пример: рул ряд 1 10к")
            return
        row = int(args[2])
        raw_amount_str = args[3].lower()
        target_str = f"ряд {row}"
    else:
        raw_amount_str = args[2].lower()

    valid_exact_targets = ["кра", "чер", "чет", "нечет", "мал", "бол", "1-12", "13-24", "25-36", "ряд 1", "ряд 2", "ряд 3"]
    is_valid = target_str in valid_exact_targets
    if not is_valid:
        try:
            num_val = int(target_str)
            if 0 <= num_val <= 36: is_valid = True
        except: pass

    if not is_valid:
        await message.answer("❌ Неверная ставка! Пишите строго: рул кра, рул чер, рул мал, рул бол, рул 1-12, или число от 0 до 36.")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    bal = cursor.fetchone()[0]

    amount = bal if raw_amount_str in ["вб", "все", "all"] else parse_sum(raw_amount_str)
    if not amount or amount <= 0 or bal < amount:
        await message.answer("❌ Неверная сумма или недостаточно средств.")
        conn.close()
        return

    rolled_num = random.randint(0, 36)
    rolled_color = "🟢" if rolled_num == 0 else ("🔴" if rolled_num in RED_NUMBERS else "⚫")

    won, payout = False, 0
    if target_str == "кра" and rolled_num != 0 and rolled_num in RED_NUMBERS: won, payout = True, amount * 2
    elif target_str == "чер" and rolled_num != 0 and rolled_num not in RED_NUMBERS: won, payout = True, amount * 2
    elif target_str == "чет" and rolled_num != 0 and rolled_num % 2 == 0: won, payout = True, amount * 2
    elif target_str == "нечет" and rolled_num != 0 and rolled_num % 2 != 0: won, payout = True, amount * 2
    elif target_str == "мал" and 1 <= rolled_num <= 18: won, payout = True, amount * 2
    elif target_str == "бол" and 19 <= rolled_num <= 36: won, payout = True, amount * 2
    elif target_str == "1-12" and 1 <= rolled_num <= 12: won, payout = True, amount * 3
    elif target_str == "13-24" and 13 <= rolled_num <= 24: won, payout = True, amount * 3
    elif target_str == "25-36" and 25 <= rolled_num <= 36: won, payout = True, amount * 3
    elif target_str == "ряд 1" and rolled_num in [1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34]: won, payout = True, amount * 3
    elif target_str == "ряд 2" and rolled_num in [2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35]: won, payout = True, amount * 3
    elif target_str == "ряд 3" and rolled_num in [3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36]: won, payout = True, amount * 3
    else:
        try:
            if int(target_str) == rolled_num: won, payout = True, amount * 36
        except: pass

    net_profit = payout - amount if won else -amount
    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (net_profit, user_id))
    conn.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.close()

    update_quest_progress(user_id, 1, amount if get_current_quest_day() == 1 else 0)

    if won:
        await send_result_media(message, True, f"🎉 Выпало {rolled_num} {rolled_color}\nВыигрыш: +{payout - amount:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))
    else:
        await send_result_media(message, False, f"🫠 Выпало {rolled_num} {rolled_color}\nПроигрыш: -{amount:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))

@router.message(F.text.regexp(r"(?i)^покер\s+.+"))
async def casino_poker(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Формат: покер 10к")
        return
    amount = parse_sum(args[1])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    bal = cursor.fetchone()[0]
    if not amount or amount <= 0 or bal < amount:
        await message.answer("❌ Ошибка ставки.")
        conn.close()
        return

    won = random.choice([True, False])
    net = amount if won else -amount
    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (net, user_id))
    conn.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.close()

    update_quest_progress(user_id, 3, amount if get_current_quest_day() == 3 else 0)
    if won: await message.answer(f"🎉 Победа в покере! +{amount:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))
    else: await message.answer(f"😢 Проигрыш в покере. -{amount:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))

@router.message(F.text.regexp(r"(?i)^дартс\b"))
async def game_darts(message: Message):
    if not await check_ban_and_register(message): return
    args = message.text.split()
    if len(args) < 3 or args[2].lower() not in ["центр", "мимо"]:
        await message.answer("❌ Строгий формат: дартс [ставка] центр ИЛИ дартс [ставка] мимо")
        return

    amount = parse_sum(args[1])
    mode = args[2].lower()
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    bal = cursor.fetchone()[0]
    if not amount or amount <= 0 or bal < amount:
        await message.answer("❌ Неверная ставка.")
        conn.close()
        return

    msg_dice = await message.answer_dice(emoji="🎯")
    await asyncio.sleep(3)
    dice_val = msg_dice.dice.value

    won = (mode == "центр" and dice_val == 6) or (mode == "мимо" and dice_val == 1)
    mult = 2 if mode == "центр" else 3
    net = (amount * mult) - amount if won else -amount

    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (net, user_id))
    conn.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.close()

    update_quest_progress(user_id, 2, amount if get_current_quest_day() == 2 else 0)
    if won: await message.answer(f"🎯 Дартс успех! Выигрыш: +{net:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))
    else: await message.answer(f"🎯 Дартс мимо! Потеряно: -{amount:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))

@router.message(F.text.regexp(r"(?i)^баскет\b"))
async def game_basketball(message: Message):
    if not await check_ban_and_register(message): return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Строгий формат: баскет [ставка]")
        return

    amount = parse_sum(args[1])
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    bal = cursor.fetchone()[0]
    if not amount or amount <= 0 or bal < amount:
        await message.answer("❌ Неверная ставка.")
        conn.close()
        return

    msg_dice = await message.answer_dice(emoji="🏀")
    await asyncio.sleep(3)
    won = msg_dice.dice.value in [4, 5]
    net = amount if won else -amount

    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (net, user_id))
    conn.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.close()

    update_quest_progress(user_id, 2, amount if get_current_quest_day() == 1 else 0)
    if won: await message.answer(f"🏀 Гол! +{amount:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))
    else: await message.answer(f"🏀 Мимо! -{amount:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))

@router.message(F.text.casefold().in_(["🏦 банк", "баланс"]))
async def text_bank(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, bank_balance FROM users WHERE user_id = %s", (user_id,))
    u = cursor.fetchone()
    conn.close()
    balance, bank_balance = (u[0], u[1]) if u else (10000, 0)
    await message.answer(f"🏦 Банк:\n💰 Наличные: {balance:,} ¢\n🏛 В банке: {bank_balance:,} ¢\n\n• вложить [сумма]\n• снять [сумма]".replace(",", " "))

@router.message(F.text.regexp(r"(?i)^(вложить|вл)\s+.+"))
async def bank_deposit(message: Message):
    if not await check_ban_and_register(message): return
    amount = parse_sum(message.text.split()[1])
    if not amount or amount <= 0: return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    if cursor.fetchone()[0] < amount:
        await message.answer("❌ Недостаточно налички.")
        conn.close()
        return
    cursor.execute("UPDATE users SET balance = balance - %s, bank_balance = bank_balance + %s WHERE user_id = %s", (amount, amount, user_id))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Вложено в банк: {amount:,} ¢".replace(",", " "))

@router.message(F.text.regexp(r"(?i)^снять\s+.+"))
async def bank_withdraw(message: Message):
    if not await check_ban_and_register(message): return
    amount = parse_sum(message.text.split()[1])
    if not amount or amount <= 0: return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT bank_balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    if cursor.fetchone()[0] < amount:
        await message.answer("❌ В банке нет столько.")
        conn.close()
        return
    cursor.execute("UPDATE users SET bank_balance = bank_balance - %s, balance = balance + %s WHERE user_id = %s", (amount, amount, user_id))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Снято со счета: {amount:,} ¢".replace(",", " "))

@router.message(F.text.regexp(r"(?i)^пер\s+.+"))
async def transfer_money(message: Message):
    if not await check_ban_and_register(message): return
    args = message.text.split()
    sender_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (sender_id,))
    sender_bal = cursor.fetchone()[0]

    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
        target_name = message.reply_to_message.from_user.first_name
        amount = sender_bal if args[1].lower() in ["вб", "все", "all"] else parse_sum(args[1])
    else:
        if len(args) < 3:
            await message.answer("❌ Формат: пер @юз сумма ИЛИ пер вб (в ответ)")
            conn.close()
            return
        target_username = args[1].replace("@", "").lower()
        amount = sender_bal if args[2].lower() in ["вб", "все", "all"] else parse_sum(args[2])
        cursor.execute("SELECT user_id, custom_name, username FROM users WHERE LOWER(username) = %s", (target_username,))
        t_row = cursor.fetchone()
        if not t_row:
            await message.answer("❌ Игрок не найден.")
            conn.close()
            return
        target_id, target_name = t_row[0], t_row[1] or t_row[2]

    if not amount or amount <= 0 or sender_bal < amount:
        await message.answer("❌ Недостаточно средств.")
        conn.close()
        return

    cursor.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (amount, sender_id))
    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (amount, target_id))
    conn.commit()
    conn.close()
    sender_name = message.from_user.full_name
    sender_username = message.from_user.username or "нет"
    formatted_amount = f"{amount:,}".replace(",", " ")
    log_user_action(sender_id, sender_username, "Перевод", f"Перевёл {formatted_amount} ¢ игроку {target_name}")
    log_user_action(target_id, target_name, "Перевод", f"Получил {formatted_amount} ¢ от {sender_name}")
    if amount >= BIG_TRANSFER_THRESHOLD:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id,
                    f"🚨 БОЛЬШОЙ ПЕРЕВОД\n\n"
                    f"💰 Сумма: {formatted_amount} ¢\n"
                    f"👤 Кто перевёл: {sender_name} (@{sender_username})\n"
                    f"➡️ Кому: {target_name}\n"
                    f"🆔 Отправитель: {sender_id}\n"
                    f"🆔 Получатель: {target_id}")
            except Exception as e:
                print(f"BIG TRANSFER NOTIFY ERROR: {e}")
    await message.answer(f"✅ Успешно переведено {formatted_amount} ¢ игроку {target_name}!")

# --- ФЛИПИНГ И КВАРТИРЫ ---
@router.message(F.text.casefold().in_(["💸 флипинг", "флипинг"]))
async def text_flipping(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = %s", (user_id,))
    apt_count = cursor.fetchone()[0]
    conn.close()
    
    price = random.randint(1_000_000, 100_000_000)
    apt_index = random.randint(0, len(APARTMENT_NAMES) - 1)
    apt_name = APARTMENT_NAMES[apt_index]

    text = (
        f"🏢 Рынок недвижимости (Флипинг):\n\n"
        f"• {apt_name}\n"
        f"💰 Цена покупки: {price:,} ¢\n\n"
        f"📊 У вас недвижимости: {apt_count}/10 шт."
    ).replace(",", " ")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
         [InlineKeyboardButton(text="🛒 Купить квартиру", callback_data=f"buy_apt_{apt_index}_{price}"),
         InlineKeyboardButton(text="⏭ Другой вариант", callback_data="refresh_flip")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="MARKDOWN")

@router.callback_query(F.data == "refresh_flip")
async def refresh_flipping(callback: CallbackQuery):
    price = random.randint(1_000_000, 100_000_000)
    apt_index = random.randint(0, len(APARTMENT_NAMES) - 1)
    apt_name = APARTMENT_NAMES[apt_index]
    
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = %s", (user_id,))
    apt_count = cursor.fetchone()[0]
    conn.close()

    text = (
        f"🏢 Рынок недвижимости (Флипинг):\n\n"
        f"• {apt_name}\n"
        f"💰 Цена покупки: {price:,} ¢\n\n"
        f"📊 У вас недвижимости: {apt_count}/10 шт."
    ).replace(",", " ")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить квартиру", callback_data=f"buy_apt_{apt_index}_{price}"),
         InlineKeyboardButton(text="⏭ Другой вариант", callback_data="refresh_flip")]
    ])
    try: await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except: pass

@router.callback_query(F.data.startswith("buy_apt_"))
async def buy_apartment(callback: CallbackQuery):
    user_id = callback.from_user.id
    parts = callback.data.split("_")
    
    # Безопасно достаем индекс и цену из коротких числовых параметров
    apt_index = int(parts[2])
    price = int(parts[3])
    apt_name = APARTMENT_NAMES[apt_index] if 0 <= apt_index < len(APARTMENT_NAMES) else "Квартира"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0
    if bal < price:
        await callback.answer("❌ Недостаточно средств для покупки!", show_alert=True)
        conn.close()
        return

    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = %s", (user_id,))
    apt_count = cursor.fetchone()[0]
    if apt_count >= 10:
        await callback.answer("❌ Лимит недвижимости (максимум 10)!", show_alert=True)
        conn.close()
        return

    cursor.execute("UPDATE users SET balance = balance - %s, invested = invested + %s WHERE user_id = %s", (price, price, user_id))
    cursor.execute("INSERT INTO user_apartments (user_id, apartment_name, price, buy_time) VALUES (%s, %s, %s, %s)", 
                   (user_id, apt_name, price, time.time()))
    conn.commit()

    new_price = random.randint(1_000_000, 100_000_000)
    new_apt_index = random.randint(0, len(APARTMENT_NAMES) - 1)
    new_apt_name = APARTMENT_NAMES[new_apt_index]
    
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = %s", (user_id,))
    new_apt_count = cursor.fetchone()[0]
    conn.close()

    text = (
        f"🏢 Рынок недвижимости (Флипинг):\n\n"
        f"• {new_apt_name}\n"
        f"💰 Цена покупки: {new_price:,} ¢\n\n"
        f"📊 У вас недвижимости: {new_apt_count}/10 шт."
    ).replace(",", " ")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить квартиру", callback_data=f"buy_apt_{new_apt_index}_{new_price}"),
         InlineKeyboardButton(text="⏭ Другой вариант", callback_data="refresh_flip")]
    ])
    try: await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except: pass
    await callback.answer("🏠 Квартира успешно куплена!", show_alert=True)

@router.message(F.text.casefold().in_(["🏠 квартиры", "мои квартиры", "квартиры"]))
async def text_my_apartments(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, apartment_name, price FROM user_apartments WHERE user_id = %s", (user_id,))
    apartments = cursor.fetchall()
    conn.close()
    if not apartments:
        await message.answer("📦 У вас пока нет купленной недвижимости.")
        return
    text = f"📦 Ваша недвижимость ({len(apartments)}/10):\n\n"
    keyboard = []
    for apt_id, name, price in apartments:
        text += f"• {name} | Куплена за: {price:,} ¢\n"
        keyboard.append([InlineKeyboardButton(text=f"💰 Продать «{name[:10]}»", callback_data=f"sell_apt_{apt_id}")])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="MARKDOWN")

@router.callback_query(F.data.startswith("sell_apt_"))
async def sell_apartment(callback: CallbackQuery):
    user_id = callback.from_user.id
    apt_id = int(callback.data.split("_")[2])

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT price FROM user_apartments WHERE id = %s AND user_id = %s", (apt_id, user_id))
    apt = cursor.fetchone()
    if not apt:
        await callback.answer("❌ Квартира не найдена.", show_alert=True)
        conn.close()
        return

    price = apt[0]
    percent = random.uniform(-0.05, 0.05)
    sell_price = int(price * (1 + percent))
    if sell_price < 1: sell_price = 1

    cursor.execute("DELETE FROM user_apartments WHERE id = %s", (apt_id,))
    cursor.execute("UPDATE users SET balance = balance + %s, invested = invested - %s WHERE user_id = %s", (sell_price, price, user_id))
    conn.commit()

    cursor.execute("SELECT id, apartment_name, price FROM user_apartments WHERE user_id = %s", (user_id,))
    apartments = cursor.fetchall()
    conn.close()

    diff = sell_price - price
    diff_sign = f"+{diff:,}" if diff >= 0 else f"{diff:,}"

    if not apartments:
        await callback.message.edit_text("📦 У вас больше нет купленной недвижимости.", reply_markup=None)
    else:
        text = f"📦 Ваша недвижимость ({len(apartments)}/10):\n\n"
        keyboard = []
        for a_id, name, p in apartments:
            text += f"• {name} | Куплена за: {p:,} ¢\n"
            keyboard.append([InlineKeyboardButton(text=f"💰 Продать «{name[:10]}»", callback_data=f"sell_apt_{a_id}")])
        try:
            await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="MARKDOWN")
        except: pass

    await callback.answer(f"💰 Продано за {sell_price:,} ¢ (Разница: {diff_sign} ¢)".replace(",", " "), show_alert=True)

@router.message(F.text.casefold().in_(["🎁 бонус", "бонус"]))
async def text_bonus(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT last_bonus FROM users WHERE user_id = %s", (user_id,))
    last_bonus = cursor.fetchone()[0] or 0.0
    if time.time() - last_bonus < 86400:
        await message.answer("⏳ Бонус доступен раз в 24 часа!")
        conn.close()
        return
    
    cursor.execute("UPDATE users SET balance = balance + 500000, last_bonus = %s WHERE user_id = %s", (time.time(), user_id))
    conn.commit()
    conn.close()
    await message.answer("🎉 Бонус получен: +500 000 ¢!")

@router.message(F.text.casefold().in_(["👥 рефералы", "реф", "рефералы"]))
async def text_referral(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT ref_count FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    ref_count = row[0] if row and row[0] is not None else 0
    conn.close()
    ref_earned = ref_count * 1_000_000
    ref_link = f"https://t.me/Flippincv_bot?start=ref_{user_id}"
    share_text = (f"Заходи в крутого бота! Приглашено: {ref_count}. "
                  f"Всего собрано за рефералов: {ref_earned:,} ¢").replace(",", " ")
    share_url = f"https://t.me/share/url?url={quote(ref_link, safe='')}&text={quote(share_text)}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📤 Поделиться", url=share_url)]])
    await message.answer(
        f"👥 Реферальная система:\n\n"
        f"🔗 Твоя ссылка:\n{ref_link}\n\n"
        f"👤 Приглашено: {ref_count}\n"
        f"💰 Всего собрано: {ref_earned:,} ¢\n"
        f"🎁 За каждого нового игрока: 1 000 000 ¢".replace(",", " "),
        reply_markup=kb
    )

@router.message(F.text.regexp(r"(?i)^промокод\s+\w+$"))
async def activate_promo_code(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    code = message.text.split()[1].upper()
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT 1 FROM user_promo WHERE user_id = %s AND code = %s", (user_id, code))
    if cursor.fetchone():
        await message.answer("❌ Вы уже вводили этот промокод.")
        conn.close()
        return

    cursor.execute("SELECT reward, activations_left FROM promo_codes WHERE code = %s", (code,))
    promo = cursor.fetchone()
    if not promo or promo[1] <= 0:
        await message.answer("❌ Промокод не найден или истек.")
        conn.close()
        return
        
    reward = promo[0]
    cursor.execute("INSERT INTO user_promo (user_id, code) VALUES (%s, %s)", (user_id, code))
    cursor.execute("UPDATE promo_codes SET activations_left = activations_left - 1 WHERE code = %s", (code,))
    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (reward, user_id))
    conn.commit()
    conn.close()
    
    update_quest_progress(user_id, 3, 1)
    await message.answer(f"🎉 Промокод активирован! Получено: +{reward:,} ¢".replace(",", " "))

@router.message(F.text.casefold().in_(["🎬 креатор"]))
async def text_creator_command(message: Message):
    if not await is_creator(message.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💵 Забрать ЗП (7.5 млн)", callback_data="creator_claim_salary")]])
    await message.answer("🎬 Меню креатора:\n• Зарплата: 7.5 млн ¢\n• Промокоды (от 1кк до 10кк): Промо [код] [сумма] [активации]\nПример: Промо GG 10кк 7", reply_markup=kb)

@router.callback_query(F.data == "creator_claim_salary")
async def callback_creator_salary(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not await is_creator(user_id): return
    now = time.time()
    if now - creator_salary_cooldowns.get(user_id, 0.0) < 86400:
        await callback.answer("⏳ ЗП доступна раз в сутки!", show_alert=True)
        return
    creator_salary_cooldowns[user_id] = now
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + 7500000 WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()
    await callback.answer("💼 ЗП получена: +7.5 млн ¢!", show_alert=True)

@router.message(F.text.regexp(r"(?i)^промо\s+\w+\s+.+\s+\d+$"))
async def create_promo_universal(message: Message):
    user_id = message.from_user.id
    is_adm = user_id in ADMIN_IDS
    creatr = await is_creator(user_id)
    if not is_adm and not creatr: return

    args = message.text.split()
    code, reward, activations = args[1].upper(), parse_sum(args[2]), int(args[3])
    if not reward or reward <= 0:
        await message.answer("❌ Ошибка суммы.")
        return

    if not is_adm and creatr and not (1_000_000 <= reward <= 10_000_000):
        await message.answer("❌ Креаторы могут ставить награду только от 1 000 000 до 10 000 000 ¢.")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO promo_codes (code, reward, activations_left) VALUES (%s, %s, %s) ON CONFLICT (code) DO UPDATE SET reward = EXCLUDED.reward, activations_left = EXCLUDED.activations_left", (code, reward, activations))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Промокод {code} создан на {reward:,} ¢ (активаций: {activations})!".replace(",", " "))

@router.message(F.text.casefold().in_(["админ", "/admin"]))
async def cmd_admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("🛠 Панель администратора:", reply_markup=get_admin_keyboard())

@router.callback_query(F.data == "admin_history_help")
async def admin_history_help_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.message.answer("📋 История игрока\n\nНапиши: история @username\nНапример: история @chapa")
    await callback.answer()

@router.message(F.text.regexp(r"(?i)^(история|действия)\s+@?\w+$"))
async def admin_history(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    username = message.text.split()[1].lstrip("@").lower()
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT user_id, custom_name, username FROM users WHERE LOWER(username) = %s", (username,))
    user = cursor.fetchone()
    if not user:
        conn.close(); await message.answer("❌ Игрок с таким username не найден."); return
    cursor.execute("SELECT action_type, action_details, timestamp FROM user_logs WHERE user_id = %s ORDER BY id DESC LIMIT 20", (user[0],))
    logs = cursor.fetchall(); conn.close()
    name = user[1] or ("@" + user[2] if user[2] else str(user[0]))
    if not logs:
        await message.answer(f"📋 История {name}: действий пока нет."); return
    lines = [f"📋 Последние действия: {name}", ""]
    for action_type, details, timestamp in logs:
        lines.append(f"• {timestamp} — {action_type}: {details}")
    await message.answer("\n".join(lines))

@router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    cnt = cursor.fetchone()[0]
    conn.close()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]])
    await callback.message.edit_text(f"📊 Всего игроков в базе: {cnt}", reply_markup=kb)

@router.callback_query(F.data == "admin_back")
async def admin_back_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.message.edit_text("🛠 Панель администратора:", reply_markup=get_admin_keyboard())

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.message.answer("📢 Введите текст для рассылки игрокам (или /cancel):")
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.answer()

@router.message(AdminStates.waiting_for_broadcast)
async def admin_broadcast_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    if message.text.casefold() == "/cancel":
        await state.clear()
        await message.answer("❌ Отменено.")
        return
    text = message.text
    await state.clear()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE banned = 0")
    users = cursor.fetchall()
    conn.close()
    success = 0
    for u in users:
        try:
            await bot.send_message(u[0], text)
            success += 1
            await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Рассылка завершена! Получили: {success} игроков.")

@router.callback_query(F.data == "admin_balance")
async def admin_balance_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.message.answer("💰 Введите юзернейм/ID и сумму (например: `@username 5кк` или `123456 1ккк`):")
    await state.set_state(AdminStates.waiting_for_balance_change)
    await callback.answer()

@router.message(AdminStates.waiting_for_balance_change)
async def admin_balance_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    if message.text.casefold() == "/cancel":
        await state.clear()
        await message.answer("❌ Отменено.")
        return
    try:
        parts = message.text.split(maxsplit=1)
        target_raw, amount = parts[0].strip(), parse_sum(parts[1])
        conn = get_db()
        cursor = conn.cursor()
        uid = int(target_raw) if target_raw.isdigit() else None
        if not uid:
            cursor.execute("SELECT user_id FROM users WHERE username = %s", (target_raw.replace("@", ""),))
            res = cursor.fetchone()
            if res: uid = res[0]
        if not uid:
            await message.answer("❌ Игрок не найден.")
            conn.close()
            return
        cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (amount, uid))
        conn.commit()
        conn.close()
        await state.clear()
        await message.answer(f"✅ Баланс игроку {target_raw} успешно изменен на {amount:,} ¢!".replace(",", " "))
    except:
        await message.answer("❌ Ошибка формата! Пример: `@user 1кк`")

@router.callback_query(F.data == "admin_donate_pass")
async def admin_donate_pass_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.message.answer("🎁 Введите юзернейм или ID игрока, которому выдать донат-пасс (или /cancel):")
    await state.set_state(AdminStates.waiting_for_donate_pass)
    await callback.answer()

@router.message(AdminStates.waiting_for_donate_pass)
async def admin_donate_pass_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    if message.text.casefold() == "/cancel":
        await state.clear()
        await message.answer("❌ Отменено.")
        return
    try:
        target_raw = message.text.strip()
        conn = get_db()
        cursor = conn.cursor()
        uid = int(target_raw) if target_raw.isdigit() else None
        if not uid:
            cursor.execute("SELECT user_id FROM users WHERE LOWER(username) = LOWER(%s)", (target_raw.replace("@", ""),))
            res = cursor.fetchone()
            if res: uid = res[0]
        if not uid:
            await message.answer("❌ Игрок не найден.")
            conn.close()
            return
        cursor.execute("INSERT INTO battle_pass (user_id, donate_pass) VALUES (%s, 1) "
                       "ON CONFLICT (user_id) DO UPDATE SET donate_pass = 1", (uid,))
        conn.commit()
        conn.close()
        await state.clear()
        await message.answer(f"✅ Донат-пасс выдан игроку {target_raw}!")
    except Exception:
        await message.answer("❌ Ошибка! Пример: `@username` или `123456`")

@router.callback_query(F.data == "admin_ban_menu")
async def admin_ban_menu_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    text = "Команды в чате:\n• бан @юз\n• разбан @юз\n• выдавать креатора @юз\n• забрать креатора @юз\n• обнулить @юз"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]])
    await callback.message.edit_text(text, reply_markup=kb)

@router.message(F.text.lower().startswith("бан "))
async def admin_ban_user(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    uname = message.text.split(maxsplit=1)[1].strip().replace("@", "")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET banned = 1 WHERE username = %s", (uname,))
    conn.commit()
    conn.close()
    await message.answer(f"🚫 Игрок @{uname} забанен.")

@router.message(F.text.lower().startswith("разбан "))
async def admin_unban_user(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    uname = message.text.split(maxsplit=1)[1].strip().replace("@", "")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET banned = 0 WHERE username = %s", (uname,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Игрок @{uname} разбанен.")

@router.message(F.text.casefold().regexp(r"^выдавать\s+креатора\s+"))
async def admin_give_creator(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    uname = message.text.split(maxsplit=2)[2].strip().replace("@", "")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_creator = 1 WHERE username = %s", (uname,))
    conn.commit()
    conn.close()
    await message.answer(f"🎬 @{uname} назначен креатором.")

@router.message(F.text.casefold().regexp(r"^забрать\s+креатора\s+"))
async def admin_take_creator(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    uname = message.text.split(maxsplit=2)[2].strip().replace("@", "")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_creator = 0 WHERE username = %s", (uname,))
    conn.commit()
    conn.close()
    await message.answer(f"❌ У @{uname} забран креатор.")

@router.message(F.text.casefold().startswith("обнулить "))
async def admin_reset_user(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    uname = message.text.split(maxsplit=1)[1].strip().replace("@", "")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = 10000, bank_balance = 0, nyachka = 0, invested = 0, bottles = 0, metal = 0 WHERE username = %s", (uname,))
    conn.commit()
    conn.close()
    await message.answer(f"🔄 Игрок @{uname} обнулен.")

# --- СЕРВЕР И API ДЛЯ WEB APP КЕЙСОВ И РЫНКА ---
INIT_DATA_MAX_AGE = 7 * 86400  # срок годности подписи initData (сек)

def verify_init_data(init_data: str):
    """Проверяет подпись Telegram initData. Возвращает user_id или None."""
    if not init_data or not API_TOKEN:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        auth_date = int(parsed.get("auth_date", 0))
        if abs(time.time() - auth_date) > INIT_DATA_MAX_AGE:
            return None
        data_check_string = "\n".join(f"{key}={parsed[key]}" for key in sorted(parsed))
        secret_key = hmac.new(b"WebAppData", API_TOKEN.encode(), hashlib.sha256).digest()
        computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, received_hash.lower()):
            return None
        user = json.loads(parsed.get("user", "{}"))
        return int(user["id"])
    except Exception:
        return None

def api_uid(request) -> int:
    """user_id из проверенного initData (0 — если проверка не пройдена)."""
    return verify_init_data(request.headers.get("X-Init-Data", "")) or 0

@web.middleware
async def telegram_auth_middleware(request, handler):
    # Главная страница и статические файлы отдаются без проверки: initData подставит сам Telegram
    if request.path == "/" or request.path.startswith("/cases/"):
        return await handler(request)
    if verify_init_data(request.headers.get("X-Init-Data", "")) is None:
        return web.json_response({"error": "Не авторизовано"}, status=401)
    return await handler(request)

async def index_handler(request):
    return web.FileResponse("index.html")

async def api_get_data(request):
    user_id = api_uid(request)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, COALESCE(coins, 0), COALESCE(cases_opened, 0) FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 10000
    coins = row[1] if row else 0
    cases_opened = row[2] if row else 0
    check_achievements(cursor, user_id)
    medals = get_user_medals(cursor, user_id)
    season = bp_season(cursor)
    boost = bp_get_boost(cursor, user_id)
    cursor.execute("SELECT COALESCE(free_cases, 0) FROM battle_pass WHERE user_id = %s", (user_id,))
    fr = cursor.fetchone()
    conn.commit()
    conn.close()
    return web.json_response({
        "balance": balance, "coins": coins, "cases_opened": cases_opened, "medals": medals,
        "free_cases": fr[0] if fr else 0,
        "bp_boost": boost,
        "bp_season": season,
        "server_now": time.time()
    })

async def api_get_inventory(request):
    user_id = api_uid(request)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, item_name, price, COALESCE(item_type, 'normal') FROM user_inventory WHERE user_id = %s AND sold = 0", (user_id,))
    items = [{"id": r[0], "name": r[1], "price": r[2], "item_type": r[3], "emoji": "💻" if r[3] == "electronics" else "🎁"} for r in cursor.fetchall()]
    conn.close()
    return web.json_response(items)

# --- БАТЛ-ПАСС (Хэллоуин, сезон 31 день, 50 уровней) ---
BP_MAX_LEVEL = 50
BP_SEASON_DAYS = 31
BP_TRACKS = ("normal", "donate")


def bp_level_xp(level):
    """XP, чтобы поднять уровень: 1-й — 100 XP, каждый следующий на x1.05 больше.
    Рост подобран так, чтобы весь пасс (50 уровней) проходился примерно за 24 дня
    при сезоне в 31 день — с запасом, не впритык (суммарно ~20 900 XP)."""
    return int(100 * (1.05 ** (level - 1)))


def bp_level_state(xp):
    """(уровень 0..50, XP внутри текущего уровня, XP нужно до следующего)."""
    level, passed = 0, 0
    while level < BP_MAX_LEVEL:
        need = bp_level_xp(level + 1)
        if xp >= passed + need:
            passed += need
            level += 1
        else:
            return level, xp - passed, need
    return level, xp - passed, 0


def bp_reward(level, track):
    """Награда уровня пасса. Донат-пасс: деньги x2, кейсы x2, бусты на 2 минуты."""
    donate = track == "donate"
    boost_secs = 120 if donate else 60
    # 10/20/30/40/50 — x4; кратные 3 — баланс (шаг +500к, донат x2); 3k+2 — x2; остальные — бесплатный кейс
    if level % 10 == 0 and level >= 10:
        return {"kind": "boost", "boost": 4, "duration": boost_secs, "emoji": "🍀",
                "name": f"x4 везение в кейсах · {boost_secs} сек"}
    if level == 1 or level % 3 == 0:
        amount = 500_000 if level == 1 else 500_000 * (level // 3 + 1)
        if donate:
            amount *= 2
        return {"kind": "balance", "emoji": "💰", "amount": amount,
                "name": f"Баланс +{amount:,} ¢".replace(",", " ")}
    if level % 3 == 2:
        return {"kind": "boost", "boost": 2, "duration": boost_secs, "emoji": "🍀",
                "name": f"x2 везение в кейсах · {boost_secs} сек"}
    cases = 2 if donate else 1
    return {"kind": "free_case", "emoji": "🎃", "amount": cases,
            "name": f"Бесплатный кейс «Хеллоуин кейс» ×{cases}"}


# Пул ежедневных квестов: сложность 0/1/2 -> цель и награда
BP_QUEST_POOL = [
    {"code": "open_cases", "emoji": "🎁", "tpl": "Открой кейсов: {t}",
     "targets": (3, 7, 15),
     "xp": ((100, 150), (150, 225), (225, 300)),
     "bal": ((250_000, 400_000), (500_000, 700_000), (750_000, 1_000_000))},
    {"code": "rare_drop", "emoji": "💎", "tpl": "Выбей предметов с шансом ≤15%: {t}",
     "targets": (1, 2, 4),
     "xp": ((100, 150), (150, 225), (225, 300)),
     "bal": ((250_000, 400_000), (500_000, 700_000), (750_000, 1_000_000))},
    {"code": "sell_value", "emoji": "💰", "tpl": "Продай предметов на {t} ¢",
     "targets": (1_000_000, 5_000_000, 15_000_000),
     "xp": ((100, 150), (150, 225), (225, 300)),
     "bal": ((250_000, 400_000), (500_000, 700_000), (750_000, 1_000_000))},
    {"code": "upgrade_win", "emoji": "🎯", "tpl": "Победи в апгрейдере: {t}",
     "targets": (1, 2, 4),
     "xp": ((100, 150), (150, 225), (225, 300)),
     "bal": ((250_000, 400_000), (500_000, 700_000), (750_000, 1_000_000))},
    {"code": "roll_sim", "emoji": "📱", "tpl": "Сделай прокруток сим-карт: {t}",
     "targets": (1, 3, 6),
     "xp": ((100, 150), (150, 225), (225, 300)),
     "bal": ((250_000, 400_000), (500_000, 700_000), (750_000, 1_000_000))},
    {"code": "market_buy", "emoji": "🛒", "tpl": "Купи сим-карт на рынке: {t}",
     "targets": (1, 2, 3),
     "xp": ((100, 150), (150, 225), (225, 300)),
     "bal": ((250_000, 400_000), (500_000, 700_000), (750_000, 1_000_000))},
]
BP_DONATE_XP = ((300, 375), (375, 437), (437, 500))
BP_DONATE_BAL = ((1_000_000, 1_300_000), (1_400_000, 1_700_000), (1_800_000, 2_000_000))


def bp_day_str():
    """Московский день — квесты обновляются раз в сутки (по МСК)."""
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d")


def bp_daily_quests(user_id, day, track):
    """Детерминированные квесты дня: другие каждый день, свои у каждого игрока."""
    rng = random.Random(f"bp:{user_id}:{day}:{track}")
    n = 3 if track == "normal" else 2
    picks = rng.sample(range(len(BP_QUEST_POOL)), n)
    out = []
    for idx in picks:
        q = BP_QUEST_POOL[idx]
        diff = rng.randint(0, 2)
        if track == "donate":
            xp = rng.randint(*BP_DONATE_XP[diff])
            bal = rng.randint(*BP_DONATE_BAL[diff])
        else:
            xp = rng.randint(*q["xp"][diff])
            bal = rng.randint(*q["bal"][diff])
        target = q["targets"][diff]
        out.append({
            "code": q["code"], "emoji": q["emoji"], "difficulty": diff,
            "name": q["tpl"].format(t=f"{target:,}".replace(",", " ")),
            "target": target, "xp": xp, "balance": bal,
        })
    return out


def bp_bump(cursor, user_id, code, amount):
    """Накидывает прогресс дневного квеста (вызывать внутри транзакции до commit)."""
    if amount <= 0:
        return
    day = bp_day_str()
    for track in BP_TRACKS:
        codes = {q["code"] for q in bp_daily_quests(user_id, day, track)}
        if code not in codes:
            continue
        cursor.execute("""
            INSERT INTO battle_pass_daily (user_id, day, track, code, progress, claimed)
            VALUES (%s, %s, %s, %s, %s, 0)
            ON CONFLICT (user_id, day, track, code)
            DO UPDATE SET progress = battle_pass_daily.progress + EXCLUDED.progress
        """, (user_id, day, track, code, amount))


def bp_season(cursor):
    """Сезон стартует с первой активности и длится 31 день."""
    cursor.execute("INSERT INTO battle_pass_settings (id, started_at) VALUES (1, %s) ON CONFLICT (id) DO NOTHING", (time.time(),))
    cursor.execute("SELECT started_at FROM battle_pass_settings WHERE id = 1")
    start = cursor.fetchone()[0]
    days_left = BP_SEASON_DAYS - int((time.time() - start) // 86400)
    return {"active": days_left > 0, "days_left": max(0, days_left), "started_at": start}


def bp_get_boost(cursor, user_id):
    cursor.execute("SELECT boost, until_ts FROM battle_pass_boosts WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    now = time.time()
    if row and row[0] in (2, 4) and row[1] > now:
        # left — секунды до конца буста, посчитанные на СЕРВЕРЕ: клиент отсчитывает
        # их своим монотонным таймером, поэтому кривые часы устройства не влияют
        return {"boost": row[0], "until": row[1], "left": max(0.0, row[1] - now)}
    return None


def bp_ensure_row(cursor, user_id):
    cursor.execute("INSERT INTO battle_pass (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))


def bp_full_state(cursor, user_id, season=None):
    bp_ensure_row(cursor, user_id)
    if season is None:
        season = bp_season(cursor)
    cursor.execute("SELECT xp, free_cases, donate_pass, claimed_normal, claimed_donate FROM battle_pass WHERE user_id = %s", (user_id,))
    xp, free_cases, donate, cn, cd = cursor.fetchone()
    level, into, need = bp_level_state(xp)
    day = bp_day_str()
    cursor.execute("SELECT track, code, progress, claimed FROM battle_pass_daily WHERE user_id = %s AND day = %s", (user_id, day))
    prog = {(r[0], r[1]): (r[2], r[3]) for r in cursor.fetchall()}
    quests = {}
    for track in BP_TRACKS:
        qs = bp_daily_quests(user_id, day, track)
        for q in qs:
            p, c = prog.get((track, q["code"]), (0, 0))
            q["progress"] = p
            q["claimed"] = c
        quests[track] = qs
    return {
        "season": season,
        "level": level, "xp": xp, "xp_into": into, "xp_needed": need,
        "passed": level >= BP_MAX_LEVEL,
        "free_cases": free_cases,
        "donate_pass": bool(donate),
        "claimed_normal": [int(x) for x in cn.split(",") if x],
        "claimed_donate": [int(x) for x in cd.split(",") if x],
        "boost": bp_get_boost(cursor, user_id),
        "rewards": {t: [bp_reward(i, t) for i in range(1, BP_MAX_LEVEL + 1)] for t in BP_TRACKS},
        "quests": quests,
        "server_now": time.time(),
    }


async def api_battle_pass(request):
    user_id = api_uid(request)
    conn = get_db()
    cursor = conn.cursor()
    state = bp_full_state(cursor, user_id)
    state["success"] = True
    conn.commit()
    conn.close()
    return web.json_response(state)


async def api_bp_claim(request):
    """Забрать награду уровня пасса."""
    try:
        data = await request.json()
        user_id = api_uid(request)
        level = int(data.get("level", 0))
        track = str(data.get("track", "normal"))
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "Некорректные данные"}, status=400)
    if track not in BP_TRACKS or not (1 <= level <= BP_MAX_LEVEL):
        return web.json_response({"success": False, "error": "Некорректные данные"}, status=400)

    conn = get_db()
    cursor = conn.cursor()
    season = bp_season(cursor)
    if not season["active"]:
        conn.close()
        return web.json_response({"success": False, "error": "Сезон батл-пасса завершён"}, status=400)
    bp_ensure_row(cursor, user_id)
    cursor.execute("SELECT xp, donate_pass, claimed_normal, claimed_donate FROM battle_pass WHERE user_id = %s FOR UPDATE", (user_id,))
    xp, donate, cn, cd = cursor.fetchone()
    cur_level = bp_level_state(xp)[0]
    claimed_field = cn if track == "normal" else cd
    claimed = {int(x) for x in claimed_field.split(",") if x}
    if level in claimed:
        conn.close()
        return web.json_response({"success": False, "error": "Эта награда уже забрана"}, status=400)
    if level > cur_level:
        conn.close()
        return web.json_response({"success": False, "error": "Уровень ещё не получен"}, status=400)
    if track == "donate" and not donate:
        conn.close()
        return web.json_response({"success": False, "error": "Нужен донат-пасс — обращайтесь к @shaptes"}, status=400)

    reward = bp_reward(level, track)
    if reward["kind"] == "boost":
        if bp_get_boost(cursor, user_id):
            conn.close()
            return web.json_response({"success": False, "error": "Подожди, пока закончится эта удача! 🍀"}, status=400)
        cursor.execute(
            "INSERT INTO battle_pass_boosts (user_id, boost, until_ts) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET boost = EXCLUDED.boost, until_ts = EXCLUDED.until_ts",
            (user_id, reward["boost"], time.time() + reward["duration"]))
    elif reward["kind"] == "balance":
        cursor.execute("UPDATE users SET balance = COALESCE(balance, 0) + %s WHERE user_id = %s", (reward["amount"], user_id))
        check_achievements(cursor, user_id)
    else:  # free_case
        cursor.execute("UPDATE battle_pass SET free_cases = free_cases + %s WHERE user_id = %s", (reward["amount"], user_id))

    new_field = claimed_field + ("," if claimed_field else "") + str(level)
    col = "claimed_normal" if track == "normal" else "claimed_donate"
    cursor.execute(f"UPDATE battle_pass SET {col} = %s WHERE user_id = %s", (new_field, user_id))

    state = bp_full_state(cursor, user_id, season)
    cursor.execute("SELECT COALESCE(balance, 0), COALESCE(coins, 0) FROM users WHERE user_id = %s", (user_id,))
    bal = cursor.fetchone() or (0, 0)
    state["new_balance"], state["new_coins"] = bal[0], bal[1]
    state["success"] = True
    conn.commit()
    conn.close()
    return web.json_response({"success": True, "reward": reward, "state": state})


async def api_bp_claim_quest(request):
    """Забрать награду ежедневного квеста: XP + баланс."""
    try:
        data = await request.json()
        user_id = api_uid(request)
        track = str(data.get("track", "normal"))
        idx = int(data.get("idx", -1))
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "Некорректные данные"}, status=400)
    if track not in BP_TRACKS:
        return web.json_response({"success": False, "error": "Некорректные данные"}, status=400)

    conn = get_db()
    cursor = conn.cursor()
    season = bp_season(cursor)
    if not season["active"]:
        conn.close()
        return web.json_response({"success": False, "error": "Сезон батл-пасса завершён"}, status=400)

    day = bp_day_str()
    quests = bp_daily_quests(user_id, day, track)
    if not (0 <= idx < len(quests)):
        conn.close()
        return web.json_response({"success": False, "error": "Квест не найден"}, status=400)
    q = quests[idx]

    bp_ensure_row(cursor, user_id)
    cursor.execute("SELECT donate_pass FROM battle_pass WHERE user_id = %s FOR UPDATE", (user_id,))
    row = cursor.fetchone()
    if track == "donate" and (not row or not row[0]):
        conn.close()
        return web.json_response({"success": False, "error": "Нужен донат-пасс — обращайтесь к @shaptes"}, status=400)

    cursor.execute(
        "SELECT progress, claimed FROM battle_pass_daily "
        "WHERE user_id = %s AND day = %s AND track = %s AND code = %s FOR UPDATE",
        (user_id, day, track, q["code"]))
    prow = cursor.fetchone()
    progress, claimed = prow if prow else (0, 0)
    if claimed:
        conn.close()
        return web.json_response({"success": False, "error": "Квест уже выполнен"}, status=400)
    if progress < q["target"]:
        conn.close()
        return web.json_response({"success": False, "error": "Квест ещё не выполнен"}, status=400)
    if prow:
        cursor.execute(
            "UPDATE battle_pass_daily SET claimed = 1 "
            "WHERE user_id = %s AND day = %s AND track = %s AND code = %s",
            (user_id, day, track, q["code"]))
    else:
        cursor.execute(
            "INSERT INTO battle_pass_daily (user_id, day, track, code, progress, claimed) VALUES (%s, %s, %s, %s, %s, 1)",
            (user_id, day, track, q["code"], q["target"]))

    cursor.execute("UPDATE battle_pass SET xp = xp + %s WHERE user_id = %s", (q["xp"], user_id))
    cursor.execute("UPDATE users SET balance = COALESCE(balance, 0) + %s WHERE user_id = %s", (q["balance"], user_id))
    check_achievements(cursor, user_id)

    state = bp_full_state(cursor, user_id, season)
    cursor.execute("SELECT COALESCE(balance, 0), COALESCE(coins, 0) FROM users WHERE user_id = %s", (user_id,))
    bal = cursor.fetchone() or (0, 0)
    state["new_balance"], state["new_coins"] = bal[0], bal[1]
    conn.commit()
    conn.close()
    return web.json_response({"success": True, "quest": q, "state": state})


async def api_open_case(request):
    try:
        data = await request.json()
        user_id = api_uid(request)
        case_id = int(data.get("case_id", 0))
    except (TypeError, ValueError):
        return web.json_response({"error": "Некорректные данные"}, status=400)

    cases_config = {
        1: {"price": 500000000, "currency": "balance", "items": [
            ("Игровой Телефон", 150000000, 35), ("Xbox", 250000000, 30),
            ("Плейстейшен 5", 650000000, 15), ("Игровой пк", 800000000, 5)]},
        2: {"price": 150000000, "currency": "balance", "items": [
            ("Жигуль", 50000000, 45), ("Камри", 100000000, 35), ("БМВ", 200000000, 15), ("БУГАТИ", 250000000, 5)]},
        3: {"price": 250000000, "currency": "balance", "items": [
            ("Кп по кв", 150000000, 45), ("Кп по мш", 200000000, 35), ("Кп по дм", 300000000, 15), ("Кп по флт", 400000000, 5)]},
        4: {"price": 100000000, "currency": "balance", "items": [
            ("Ключи от маленького домика", 35000000, 45), ("Средний домик", 65000000, 35), ("Большой дом", 125000000, 15), ("Пин Хаус", 200000000, 5)]},
        5: {"price": 150, "currency": "coins", "item_type": "electronics", "items": [
            ("Планшет", 50, 45), ("Телефон", 100, 25), ("Ноутбук", 150, 15), ("Хороший ПК", 250, 5)]},
        6: {"price": 10000000, "currency": "balance", "items": [
            ("Тыква", 5000000, 35), ("Череп", 7500000, 30), ("Заколдованный телефон", 15000000, 20),
            ("Заброшенный дом", 25000000, 10), ("Хеллоуин машина", 50000000, 5)]}
    }
    case_info = cases_config.get(case_id)
    if not case_info:
        return web.json_response({"error": "Кейс не найден"}, status=400)

    try:
        count = int(data.get("count", 1))
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(3, count))

    conn = get_db()
    cursor = conn.cursor()
    currency = case_info["currency"]
    balance_field = "coins" if currency == "coins" else "balance"

    # Бесплатные кейсы батл-пасса («Хеллоуин кейс») списываются первыми, остальное — за деньги
    free_used = 0
    if case_id == 6:
        cursor.execute("SELECT COALESCE(free_cases, 0) FROM battle_pass WHERE user_id = %s FOR UPDATE", (user_id,))
        fr = cursor.fetchone()
        have = fr[0] if fr else 0
        if have > 0:
            free_used = min(have, count)
            cursor.execute("UPDATE battle_pass SET free_cases = free_cases - %s WHERE user_id = %s", (free_used, user_id))
    paid_count = count - free_used
    total_price = case_info["price"] * paid_count

    if paid_count > 0:
        cursor.execute(f"SELECT COALESCE({balance_field}, 0) FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        if not row or row[0] < total_price:
            conn.rollback()
            conn.close()
            return web.json_response({"error": "Недостаточно коинов" if currency == "coins" else "Недостаточно средств"}, status=400)

        # Атомарное списание за всё количество сразу — защита от гонок баланса
        cursor.execute(f"UPDATE users SET {balance_field} = COALESCE({balance_field}, 0) - %s WHERE user_id = %s AND COALESCE({balance_field}, 0) >= %s", (total_price, user_id, total_price))
        if cursor.rowcount == 0:
            conn.rollback()
            conn.close()
            return web.json_response({"error": "Недостаточно средств для открытия кейса"}, status=400)

    # Буст везения из пасса: x2/x4 = столько прокруток, берётся самый дорогой предмет
    boost_mult = 1
    active_boost = bp_get_boost(cursor, user_id)
    if active_boost:
        boost_mult = active_boost["boost"]

    items = []
    got_rare_drop = False
    rare_count = 0
    for _ in range(count):
        won_item = None
        for _try in range(boost_mult):
            roll = random.randint(1, 100)
            current_sum = 0
            cand = None
            for name, price, chance in case_info["items"]:
                current_sum += chance
                if roll <= current_sum:
                    cand = (name, price, chance)
                    break
            if cand is None:
                continue  # пусто не бьёт предмет: с бустом берётся лучший исход
            if won_item is None or cand[1] > won_item[1]:
                won_item = cand
        item_type = case_info.get("item_type", "normal")
        if won_item:
            name, price, chance = won_item
            if chance <= 5:
                got_rare_drop = True
            if chance <= 15:
                rare_count += 1
            cursor.execute("INSERT INTO user_inventory (user_id, item_name, price, sold, item_type) VALUES (%s, %s, %s, 0, %s) RETURNING id", (user_id, name, price, item_type))
            inv_id = cursor.fetchone()[0]
            items.append({"id": inv_id, "name": name, "price": price, "item_type": item_type})
        else:
            items.append({"name": None, "price": 0, "item_type": item_type})

    cursor.execute("UPDATE users SET cases_opened = COALESCE(cases_opened, 0) + %s WHERE user_id = %s", (count, user_id))
    check_achievements(cursor, user_id, {"case_legend"} if got_rare_drop else ())
    bp_bump(cursor, user_id, "open_cases", count)
    bp_bump(cursor, user_id, "rare_drop", rare_count)
    # XP батл-пасса за каждую открытую линию — пасс двигается и без квестов
    cursor.execute(
        "INSERT INTO battle_pass (user_id, xp) VALUES (%s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET xp = battle_pass.xp + EXCLUDED.xp",
        (user_id, count * 10))

    cursor.execute("SELECT balance, COALESCE(coins, 0) FROM users WHERE user_id = %s", (user_id,))
    balances = cursor.fetchone()
    cursor.execute("SELECT COALESCE(free_cases, 0) FROM battle_pass WHERE user_id = %s", (user_id,))
    fr = cursor.fetchone()
    free_cases_now = fr[0] if fr else 0
    conn.commit()
    conn.close()
    first = next((i for i in items if i["name"]), None)
    return web.json_response({
        "new_balance": balances[0], "new_coins": balances[1],
        "count": count,
        "items": items,
        "item_name": first["name"] if first else None,
        "item_price": first["price"] if first else 0,
        "item_type": case_info.get("item_type", "normal"), "currency": currency,
        "free_cases": free_cases_now,
        "boost": active_boost,
        "free_used": free_used
    })

async def api_sell_item(request):
    data = await request.json()
    user_id = api_uid(request)
    item_id = data.get("item_id")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT price, COALESCE(item_type, 'normal') FROM user_inventory WHERE id = %s AND user_id = %s AND sold = 0", (item_id, user_id))
    item = cursor.fetchone()
    if not item:
        conn.close()
        return web.json_response({"success": False})

    price, item_type = item[0], item[1]
    # FOR UPDATE + sold=0 в условии: второй параллельный запрос не сможет продать предмет дважды
    cursor.execute("UPDATE user_inventory SET sold = 1 WHERE id = %s AND user_id = %s AND sold = 0", (item_id, user_id))
    if cursor.rowcount == 0:
        conn.rollback()
        conn.close()
        return web.json_response({"success": False})
    
    if item_type == "electronics":
        cursor.execute("UPDATE users SET coins = COALESCE(coins, 0) + %s WHERE user_id = %s", (price, user_id))
    else:
        cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (price, user_id))
    bp_bump(cursor, user_id, "sell_value", price)

    cursor.execute("SELECT balance, COALESCE(coins, 0) FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    new_bal, new_coins = row[0], row[1]
    conn.commit()
    conn.close()

    return web.json_response({"success": True, "new_balance": new_bal, "new_coins": new_coins})


async def api_sell_items(request):
    """Продажа списка предметов (например, только что выигранных в кейсе).
    Один атомарный UPDATE ... RETURNING — сумма считается ровно по тем
    предметам, которые реально были проданы этим запросом."""
    try:
        data = await request.json()
        user_id = api_uid(request)
        item_ids = [int(i) for i in (data.get("item_ids") or [])][:50]
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "Некорректные данные"}, status=400)
    if not item_ids:
        return web.json_response({"success": False, "error": "Нет предметов для продажи"}, status=400)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE user_inventory SET sold = 1 "
        "WHERE user_id = %s AND sold = 0 AND id = ANY(%s) "
        "RETURNING price, COALESCE(item_type, 'normal')",
        (user_id, item_ids))
    sold = cursor.fetchall()
    if not sold:
        conn.close()
        return web.json_response({"success": False, "error": "Предметы уже проданы"}, status=400)

    total_balance = sum(r[0] for r in sold if r[1] != "electronics")
    total_coins = sum(r[0] for r in sold if r[1] == "electronics")
    if total_balance:
        cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (total_balance, user_id))
    if total_coins:
        cursor.execute("UPDATE users SET coins = COALESCE(coins, 0) + %s WHERE user_id = %s", (total_coins, user_id))
    bp_bump(cursor, user_id, "sell_value", total_balance + total_coins)

    check_achievements(cursor, user_id)
    cursor.execute("SELECT balance, COALESCE(coins, 0) FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.commit()
    conn.close()

    return web.json_response({
        "success": True, "sold_count": len(sold),
        "total_balance": total_balance, "total_coins": total_coins,
        "new_balance": row[0], "new_coins": row[1]
    })


async def api_sell_all(request):
    """Продать весь инвентарь одним махом: electronics -> коины, обычные -> баланс."""
    try:
        user_id = api_uid(request)
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "Некорректные данные"}, status=400)

    conn = get_db()
    cursor = conn.cursor()
    # Атомарно: параллельная продажа не продаст предмет дважды,
    # а деньги начисляются ровно за то, что успели продать мы
    cursor.execute(
        "UPDATE user_inventory SET sold = 1 WHERE user_id = %s AND sold = 0 "
        "RETURNING price, COALESCE(item_type, 'normal')",
        (user_id,))
    sold = cursor.fetchall()
    if not sold:
        conn.close()
        return web.json_response({"success": False, "error": "Инвентарь пуст"}, status=400)

    total_balance = sum(r[0] for r in sold if r[1] != "electronics")
    total_coins = sum(r[0] for r in sold if r[1] == "electronics")
    if total_balance:
        cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (total_balance, user_id))
    if total_coins:
        cursor.execute("UPDATE users SET coins = COALESCE(coins, 0) + %s WHERE user_id = %s", (total_coins, user_id))
    bp_bump(cursor, user_id, "sell_value", total_balance + total_coins)

    check_achievements(cursor, user_id)
    cursor.execute("SELECT balance, COALESCE(coins, 0) FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.commit()
    conn.close()

    return web.json_response({
        "success": True, "sold_count": len(sold),
        "total_balance": total_balance, "total_coins": total_coins,
        "new_balance": row[0], "new_coins": row[1]
    })


# Прокрутка сим-карт: стоимость списывается и результат считается ТОЛЬКО на сервере.
SIM_SPIN_COSTS = {"common": 1000000, "rare": 10000000, "epic": 50000000, "legendary": 250000000}
SIM_COIN_REWARDS = {"common": 1, "rare": 10, "epic": 50, "legendary": 400}

def generate_sim_number(category):
    def dig(n):
        return "".join(str(random.randint(0, 9)) for _ in range(n))

    if category == "common":
        d = dig(8)
    elif category == "rare":
        base = str(random.randint(0, 9))
        arr = [str(random.randint(0, 9)) for _ in range(8)]
        for idx in random.sample(range(8), 2):
            arr[idx] = base
        d = "".join(arr)
    elif category == "epic":
        repeat_count = 3 if random.random() < 0.6 else 4
        base = str(random.randint(0, 9))
        arr = [base] * repeat_count + [str(random.randint(0, 9)) for _ in range(8 - repeat_count)]
        random.shuffle(arr)
        d = "".join(arr)
    else:  # legendary
        rand = random.random() * 100
        roll = 1 if rand < 50 else 4 if rand < 80 else 5 if rand < 95 else 6 if rand < 99 else 8
        if roll == 1:
            d = dig(8)
        else:
            digit = str(random.randint(0, 9))
            arr = list(digit * roll + dig(8 - roll))
            random.shuffle(arr)
            d = "".join(arr)
    return f"+888 {d[:4]} {d[4:]}"

def sim_number_taken(cursor, number, user_id):
    """Проверяет, есть ли номер уже у кого-то в игре: в инвентарях,
    на рынке или в чужих текущих прокрутках."""
    cursor.execute("""
        SELECT 1 FROM user_sims WHERE number = %s
        UNION ALL SELECT 1 FROM market_sims WHERE number = %s
        UNION ALL SELECT 1 FROM pending_sims WHERE number = %s AND user_id <> %s
        LIMIT 1
    """, (number, number, number, user_id))
    return cursor.fetchone() is not None

async def api_roll_sim(request):
    try:
        data = await request.json()
        user_id = api_uid(request)
        category = str(data.get("category", ""))
    except (TypeError, ValueError):
        return web.json_response({"error": "Некорректные данные"}, status=400)

    cost = SIM_SPIN_COSTS.get(category)
    if cost is None:
        return web.json_response({"error": "Неизвестная категория"}, status=400)

    conn = get_db()
    cursor = conn.cursor()

    # Подбираем номер, которого ещё нет ни у кого в игре
    number = None
    for _ in range(20):
        cand = generate_sim_number(category)
        if not sim_number_taken(cursor, cand, user_id):
            number = cand
            break
    if number is None:
        conn.close()
        return web.json_response({"error": "Не удалось подобрать свободный номер — попробуйте ещё раз"}, status=400)

    # Один запрос вместо четырёх: проверка телефона + атомарное списание +
    # запись прокрутки + новый баланс. Меньше круговых обменов — меньше задержка.
    cursor.execute("""
        WITH ded AS (
            UPDATE users SET balance = balance - %s
            WHERE user_id = %s AND balance >= %s AND COALESCE(has_phone, 0) = 1
            RETURNING balance
        ), ins AS (
            INSERT INTO pending_sims (user_id, number, category, price, created_at)
            SELECT %s, %s, %s, %s, %s FROM ded
            ON CONFLICT (user_id) DO UPDATE SET
                number = EXCLUDED.number, category = EXCLUDED.category,
                price = EXCLUDED.price, created_at = EXCLUDED.created_at
            RETURNING 1
        )
        SELECT (SELECT balance FROM ded), (SELECT COUNT(*) FROM ins)
    """, (cost, user_id, cost, user_id, number, category, cost, time.time()))
    row = cursor.fetchone()
    if not row or row[1] == 0:
        # Отказ — уточняем причину (только в редком случае ошибки)
        cursor.execute("SELECT COALESCE(has_phone, 0), COALESCE(balance, 0) FROM users WHERE user_id = %s", (user_id,))
        diag = cursor.fetchone()
        conn.rollback()
        conn.close()
        if not diag:
            return web.json_response({"error": "Пользователь не найден"}, status=400)
        if not diag[0]:
            return web.json_response({"error": "Сначала купите телефон!"}, status=400)
        return web.json_response({"error": "Недостаточно средств для прокрутки!"}, status=400)

    new_balance = row[0]
    bp_bump(cursor, user_id, "roll_sim", 1)
    conn.commit()
    conn.close()

    return web.json_response({
        "success": True, "number": number, "category": category,
        "coins": SIM_COIN_REWARDS[category], "balance": new_balance})

async def api_buy_phone(request):
    data = await request.json()
    user_id = api_uid(request)
    cost = 2500000

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT has_phone FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return web.json_response({"error": "Пользователь не найден"}, status=400)
    if row[0]:
        conn.close()
        return web.json_response({"error": "У вас уже есть телефон!"}, status=400)

    # Атомарное списание стоимости телефона
    cursor.execute("UPDATE users SET balance = balance - %s, has_phone = 1 WHERE user_id = %s AND balance >= %s",
                   (cost, user_id, cost))
    if cursor.rowcount == 0:
        conn.rollback()
        conn.close()
        return web.json_response({"error": "Недостаточно средств"}, status=400)

    cursor.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
    new_balance = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return web.json_response({"success": True, "new_balance": new_balance})

async def api_get_sims(request):
    user_id = api_uid(request)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT has_phone FROM users WHERE user_id = %s", (user_id,))
    u_row = cursor.fetchone()
    has_phone = u_row[0] if u_row else 0

    cursor.execute("SELECT id, number, price, category FROM user_sims WHERE user_id = %s", (user_id,))
    sims = [{"id": r[0], "number": r[1], "price": r[2], "category": r[3]} for r in cursor.fetchall()]
    conn.close()
    return web.json_response({"has_phone": has_phone, "sims": sims})

async def api_save_sim(request):
    try:
        await request.json()
    except (TypeError, ValueError):
        return web.json_response({"error": "Некорректные данные"}, status=400)
    user_id = api_uid(request)

    conn = get_db()
    cursor = conn.cursor()

    # Номер генерировал сервер при прокрутке — данные клиента игнорируем
    cursor.execute("SELECT number, category, price FROM pending_sims WHERE user_id = %s", (user_id,))
    pending = cursor.fetchone()
    if not pending:
        conn.close()
        return web.json_response({"error": "Нет свежей прокрутки — сначала крутаните номер!"}, status=400)
    number, category, price = pending
    earned_coins = SIM_COIN_REWARDS.get(category, 1)

    cursor.execute("SELECT 1 FROM user_sims WHERE number = %s", (number,))
    if cursor.fetchone():
        conn.close()
        return web.json_response({"error": "Этот номер уже занят!"}, status=400)

    cursor.execute("SELECT COUNT(*) FROM user_sims WHERE user_id = %s", (user_id,))
    if cursor.fetchone()[0] >= 5:
        conn.close()
        return web.json_response({"error": "Лимит номеров (максимум 5)"}, status=400)

    # Уникальный индекс в базе не даст создать дубликат даже при гонке
    try:
        cursor.execute("INSERT INTO user_sims (user_id, number, price, category) VALUES (%s, %s, %s, %s)", 
                       (user_id, number, earned_coins, category))
    except psycopg2.IntegrityError:
        conn.rollback()
        conn.close()
        return web.json_response({"error": "Этот номер уже занят!"}, status=400)
    cursor.execute("DELETE FROM pending_sims WHERE user_id = %s", (user_id,))

    extra = {"first_sim"}
    if category == "legendary":
        extra.add("sim_legend")
    check_achievements(cursor, user_id, extra)

    conn.commit()
    conn.close()

    return web.json_response({"success": True, "earned_coins": earned_coins})

async def api_sell_sim(request):
    try:
        await request.json()
    except (TypeError, ValueError):
        return web.json_response({"error": "Некорректные данные"}, status=400)
    user_id = api_uid(request)

    conn = get_db()
    cursor = conn.cursor()

    # Категория берётся из ПОСЛЕДНЕЙ ПРОКРУТКИ на сервере, не из тела запроса
    cursor.execute("SELECT category FROM pending_sims WHERE user_id = %s", (user_id,))
    pending = cursor.fetchone()
    if not pending:
        conn.close()
        return web.json_response({"error": "Нет свежей прокрутки — сначала крутаните номер!"}, status=400)
    earned_coins = SIM_COIN_REWARDS.get(pending[0], 1)

    cursor.execute("DELETE FROM pending_sims WHERE user_id = %s", (user_id,))
    if cursor.rowcount == 0:
        conn.rollback()
        conn.close()
        return web.json_response({"error": "Номер уже обработан"}, status=400)

    cursor.execute("UPDATE users SET coins = COALESCE(coins, 0) + %s WHERE user_id = %s", (earned_coins, user_id))
    cursor.execute("SELECT coins FROM users WHERE user_id = %s", (user_id,))
    new_coins = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return web.json_response({"success": True, "new_coins": new_coins, "earned_coins": earned_coins})

async def api_exchange_sim_coins(request):
    data = await request.json()
    user_id = api_uid(request)
    sim_id = data.get("sim_id")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT category FROM user_sims WHERE id = %s AND user_id = %s", (sim_id, user_id))
    sim = cursor.fetchone()
    
    if not sim:
        conn.close()
        return web.json_response({"error": "Сим-карта не найдена"}, status=400)
    
    category = sim[0]
    coin_rewards = {
        "common": 1,
        "rare": 10,
        "epic": 50,
        "legendary": 400
    }
    earned_coins = coin_rewards.get(category, 1)

    cursor.execute("DELETE FROM user_sims WHERE id = %s", (sim_id,))
    cursor.execute("UPDATE users SET coins = COALESCE(coins, 0) + %s WHERE user_id = %s", (earned_coins, user_id))
    cursor.execute("SELECT coins FROM users WHERE user_id = %s", (user_id,))
    new_coins = cursor.fetchone()[0]

    conn.commit()
    conn.close()
    return web.json_response({"success": True, "new_coins": new_coins, "added_coins": earned_coins})

async def api_market_get_listings(request):
    try:
        page = int(request.query.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)
    page_size = 8

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM market_sims")
    total = cursor.fetchone()[0]
    pages = max(1, (total + page_size - 1) // page_size)
    if page > pages:
        page = pages
    cursor.execute(
        "SELECT id, seller_id, seller_name, sim_id, number, price, category FROM market_sims "
        "ORDER BY id DESC LIMIT %s OFFSET %s",
        (page_size, (page - 1) * page_size))
    listings = [
        {
            "id": r[0], "seller_id": r[1], "seller_name": r[2], 
            "sim_id": r[3], "number": r[4], "price": r[5], "category": r[6]
        } 
        for r in cursor.fetchall()
    ]
    conn.close()
    return web.json_response({"listings": listings, "page": page, "pages": pages, "total": total})

async def api_market_list_sim(request):
    data = await request.json()
    user_id = api_uid(request)
    sim_id = data.get("sim_id")
    price = int(data.get("price", 0))

    if price <= 0:
        return web.json_response({"error": "Неверная цена"}, status=400)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT number, price, category FROM user_sims WHERE id = %s AND user_id = %s", (sim_id, user_id))
    sim = cursor.fetchone()
    if not sim:
        conn.close()
        return web.json_response({"error": "Сим-карта не найдена в инвентаре"}, status=400)

    number, orig_price, category = sim[0], sim[1], sim[2]
    cursor.execute("SELECT custom_name, username FROM users WHERE user_id = %s", (user_id,))
    u = cursor.fetchone()
    seller_name = u[0] if u and u[0] else (u[1] if u and u[1] else "Игрок")

    cursor.execute("DELETE FROM user_sims WHERE id = %s", (sim_id,))
    cursor.execute(
        "INSERT INTO market_sims (seller_id, seller_name, sim_id, number, price, original_price, category) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (user_id, seller_name, sim_id, number, price, orig_price, category)
    )
    conn.commit()
    conn.close()
    return web.json_response({"success": True})

async def api_market_buy_sim(request):
    data = await request.json()
    user_id = api_uid(request)
    listing_id = data.get("listing_id")

    conn = get_db()
    cursor = conn.cursor()
    # FOR UPDATE блокирует лот до конца транзакции — параллельные покупатели получат «Лот не найден»
    cursor.execute("SELECT seller_id, sim_id, number, price, category FROM market_sims WHERE id = %s FOR UPDATE", (listing_id,))
    listing = cursor.fetchone()
    if not listing:
        conn.close()
        return web.json_response({"error": "Лот не найден или уже куплен"}, status=400)

    seller_id, sim_id, number, price, category = listing
    if seller_id == user_id:
        conn.close()
        return web.json_response({"error": "Нельзя купить свою собственную сим-карту"}, status=400)

    cursor.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
    buyer_row = cursor.fetchone()
    if not buyer_row or buyer_row[0] < price:
        conn.close()
        return web.json_response({"error": "Недостаточно средств для покупки"}, status=400)

    cursor.execute("SELECT COUNT(*) FROM user_sims WHERE user_id = %s", (user_id,))
    if cursor.fetchone()[0] >= 5:
        conn.close()
        return web.json_response({"error": "В вашем инвентаре нет свободного места (максимум 5 сим-карт)"}, status=400)

    cursor.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s AND balance >= %s", (price, user_id, price))
    if cursor.rowcount == 0:
        conn.rollback()
        conn.close()
        return web.json_response({"error": "Недостаточно средств для покупки"}, status=400)
    cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (price, seller_id))
    cursor.execute("INSERT INTO user_sims (user_id, number, price, category) VALUES (%s, %s, %s, %s)", (user_id, number, price, category))
    cursor.execute("DELETE FROM market_sims WHERE id = %s", (listing_id,))
    
    cursor.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
    new_balance = cursor.fetchone()[0]

    check_achievements(cursor, user_id, {"market_buy"})
    bp_bump(cursor, user_id, "market_buy", 1)
    conn.commit()
    conn.close()
    try:
        await bot.send_message(seller_id, f"✅ Вы успешно продали сим-карту {number} за {price:,} ¢.".replace(",", " "))
    except Exception as e:
        print(f"Не удалось отправить продавцу уведомление о продаже: {e}")
    return web.json_response({"success": True, "new_balance": new_balance, "seller_notified": True})

async def api_market_cancel_sim(request):
    data = await request.json()
    user_id = api_uid(request)
    listing_id = data.get("listing_id")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT sim_id, number, price, original_price, category FROM market_sims WHERE id = %s AND seller_id = %s", (listing_id, user_id))
    listing = cursor.fetchone()
    if not listing:
        conn.close()
        return web.json_response({"error": "Лот не найден"}, status=400)

    sim_id, number, sale_price, orig_price, category = listing
    cursor.execute("SELECT COUNT(*) FROM user_sims WHERE user_id = %s", (user_id,))
    if cursor.fetchone()[0] >= 5:
        conn.close()
        return web.json_response({"error": "В инвентаре нет свободного места для возврата сим-карты"}, status=400)

    if orig_price is None:
        orig_price = sale_price

    cursor.execute("INSERT INTO user_sims (user_id, number, price, category) VALUES (%s, %s, %s, %s)", (user_id, number, orig_price, category))
    cursor.execute("DELETE FROM market_sims WHERE id = %s", (listing_id,))
    conn.commit()
    conn.close()
    return web.json_response({"success": True})

async def api_upgrader(request):
    try:
        data = await request.json()
        user_id = api_uid(request)
        from_item_id = int(data.get("from_item_id", 0))
        target_name = str(data.get("target_item_name", ""))
        target_price = int(data.get("target_item_price", 0))
        target_type = str(data.get("target_item_type", "normal"))
    except (TypeError, ValueError):
        return web.json_response({"error": "Некорректные данные апгрейда"}, status=400)

    electronics = {"Планшет": 50, "Телефон": 100, "Ноутбук": 150, "Хороший ПК": 250}
    if target_type == "electronics":
        if target_name not in electronics or electronics[target_name] != target_price:
            return web.json_response({"error": "Недопустимая цель апгрейда"}, status=400)
    elif target_type != "normal":
        return web.json_response({"error": "Недопустимый тип предмета"}, status=400)

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, price, COALESCE(item_type, 'normal') FROM user_inventory WHERE id = %s AND user_id = %s AND sold = 0", (from_item_id, user_id))
        item = cursor.fetchone()
        if not item:
            return web.json_response({"error": "Исходный предмет не найден"}, status=400)
        source_type = item[2]
        if source_type == "electronics" and target_type != "electronics":
            return web.json_response({"error": "Электронику можно улучшать только до другой электроники за коины"}, status=400)
        if source_type != "electronics" and target_type == "electronics":
            return web.json_response({"error": "Обычные предметы нельзя улучшать до электроники"}, status=400)

        source_price = item[1]
        if target_price <= source_price:
            return web.json_response({"error": "Цель должна быть дороже исходного предмета"}, status=400)

        # Шанс считает сервер: (цена исхода / цена цели) * 100, максимум 100%
        chance = (source_price / target_price) * 100
        if chance > 100:
            chance = 100.0
        won = random.random() * 100 <= chance

        cursor.execute("UPDATE user_inventory SET sold = 1 WHERE id = %s AND user_id = %s AND sold = 0", (from_item_id, user_id))
        if cursor.rowcount == 0:
            return web.json_response({"error": "Предмет уже использован"}, status=400)
        if won:
            cursor.execute("INSERT INTO user_inventory (user_id, item_name, price, sold, item_type) VALUES (%s, %s, %s, 0, %s)", (user_id, target_name, target_price, target_type))
            check_achievements(cursor, user_id, {"upgrade_win"})
            bp_bump(cursor, user_id, "upgrade_win", 1)
        conn.commit()
        return web.json_response({"success": True, "won": won, "chance": chance})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

ACTIVE_CRASH_ROUNDS = {}  # user_id -> {"bet": int, "crash_point": float}

async def api_crash_bet(request):
    try:
        data = await request.json()
        user_id = api_uid(request)
        action = data.get("action")
    except (TypeError, ValueError):
        return web.json_response({"error": "Некорректные данные"}, status=400)

    conn = get_db()
    cursor = conn.cursor()

    if action == "start":
        try:
            bet = int(data.get("bet", 0))
        except (TypeError, ValueError):
            bet = 0
        if bet <= 0:
            conn.close()
            return web.json_response({"error": "Неверная ставка"}, status=400)

        # Атомарное списание: баланс не уйдёт в минус при гонках
        cursor.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s AND balance >= %s", (bet, user_id, bet))
        if cursor.rowcount == 0:
            conn.rollback()
            conn.close()
            return web.json_response({"error": "Недостаточно средств"}, status=400)
        cursor.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
        new_balance = cursor.fetchone()[0]
        conn.commit()
        conn.close()

        r = random.random()
        if r < 0.12:
            crash_point = 1.00
        else:
            raw_val = random.random() ** 5
            crash_point = round(1.01 + raw_val * 14.0, 2)
            if crash_point > 15.0:
                crash_point = 15.0

        ACTIVE_CRASH_ROUNDS[user_id] = {"bet": bet, "crash_point": crash_point}
        return web.json_response({
            "balance": new_balance,
            "crash_point": crash_point
        })

    if action == "cashout":
        rnd = ACTIVE_CRASH_ROUNDS.get(user_id)
        if not rnd:
            conn.close()
            return web.json_response({"error": "Активной ставки нет"}, status=400)
        try:
            multiplier = float(data.get("multiplier", 1.0))
        except (TypeError, ValueError):
            multiplier = 0.0
        # Сервер не выплатит больше точки крушения
        if multiplier < 1.0 or multiplier > rnd["crash_point"] + 1e-9:
            conn.close()
            return web.json_response({"error": "Некорректный множитель"}, status=400)

        win_amount = int(rnd["bet"] * multiplier)
        cursor.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (win_amount, user_id))
        cursor.execute("SELECT balance FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        conn.commit()
        conn.close()
        del ACTIVE_CRASH_ROUNDS[user_id]

        return web.json_response({
            "balance": row[0],
            "win_amount": win_amount
        })

    if action == "crash":
        # Игрок проиграл — раунд закрывается без выплаты
        ACTIVE_CRASH_ROUNDS.pop(user_id, None)
        conn.close()
        return web.json_response({"success": True})

    conn.close()
    return web.json_response({"error": "Неверное действие"}, status=400)

async def web_server():
    app = web.Application(middlewares=[telegram_auth_middleware])
    app.router.add_get("/", index_handler)
    # 3D-кейсы (SVG с прозрачным фоном)
    cases_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases")
    app.router.add_static("/cases/", path=cases_dir, name="cases")
    app.router.add_get("/api/get_data", api_get_data)
    app.router.add_get("/api/get_inventory", api_get_inventory)
    app.router.add_post("/api/open_case", api_open_case)
    app.router.add_post("/api/sell_item", api_sell_item)
    app.router.add_post("/api/sell_items", api_sell_items)
    app.router.add_post("/api/sell_all", api_sell_all)
    app.router.add_get("/api/battle_pass", api_battle_pass)
    app.router.add_post("/api/battle_pass/claim", api_bp_claim)
    app.router.add_post("/api/battle_pass/claim_quest", api_bp_claim_quest)
    app.router.add_post("/api/upgrader", api_upgrader)
    app.router.add_post("/api/crash", api_crash_bet)
    
    app.router.add_post("/api/buy_phone", api_buy_phone)
    app.router.add_get("/api/get_sims", api_get_sims)
    app.router.add_post("/api/roll_sim", api_roll_sim)
    app.router.add_post("/api/save_sim", api_save_sim)
    app.router.add_post("/api/sell_sim", api_sell_sim)
    app.router.add_post("/api/exchange_sim_coins", api_exchange_sim_coins)

    app.router.add_get("/api/market/get_listings", api_market_get_listings)
    app.router.add_post("/api/market/list_sim", api_market_list_sim)
    app.router.add_post("/api/market/buy_sim", api_market_buy_sim)
    app.router.add_post("/api/market/cancel_sim", api_market_cancel_sim)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 6543))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    init_db()
    
    from aiogram.types import MenuButtonWebApp, WebAppInfo
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="🎰 Кейсы",
            web_app=WebAppInfo(url="https://telegram-bot-tg9i.onrender.com")
        )
    )
    
    await asyncio.gather(
        web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())

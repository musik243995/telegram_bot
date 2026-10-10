import random
import time
import os
import sqlite3
import asyncio
from datetime import datetime
from urllib.parse import quote
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

# Названия квартир для флипинга
APARTMENT_NAMES = [
    "Убитая хрущевка на окраине", "Сталинка в центре под ремонт", 
    "Студия в новостройке с черновой отделкой", "Элитный пентхаус с видом на парк", 
    "Бабушатник со старыми коврами", "Апартаменты в бизнес-центре", 
    "Малосемейка с общим балконом", "Просторная евротрешка", 
    "Аварийная квартира в старом фонде", "Люксовая вилла на закрытой территории"
]

def get_private_keyboard(is_creator_flag=False):
    kb = [
        [KeyboardButton(text="🎰 Казино"), KeyboardButton(text="👷 Работа")],
        [KeyboardButton(text="💸 Флипинг"), KeyboardButton(text="🏢 Компания по флипингу 💸")],
        [KeyboardButton(text="🏦 Банк"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🏆 Топ"), KeyboardButton(text="🏠 Квартиры")],
        [KeyboardButton(text="🎁 Бонус"), KeyboardButton(text="👥 Рефералы")],
        [KeyboardButton(text="🥷 Нычка"), KeyboardButton(text="📜 Квесты")],
        [KeyboardButton(text="🔫 Банда"), KeyboardButton(text="📖 Помощь")]
    ]
    if is_creator_flag:
        kb.append([KeyboardButton(text="🎬 Креатор")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="💰 Изменить баланс", callback_data="admin_balance")],
            [InlineKeyboardButton(text="🚫 Баны / Креаторы", callback_data="admin_ban_menu")],
            [InlineKeyboardButton(text="📋 История игрока", callback_data="admin_history_help")]
        ]
    )

# --- БАЗА ДАННЫХ ---
def get_db():
    conn = sqlite3.connect("game.db", timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            custom_name TEXT DEFAULT NULL,
            balance INTEGER DEFAULT 10000,
            bank_balance INTEGER DEFAULT 0,
            nyachka INTEGER DEFAULT 0,
            invested INTEGER DEFAULT 0,
            bottles INTEGER DEFAULT 0,
            metal INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            is_creator INTEGER DEFAULT 0,
            gang_name TEXT DEFAULT NULL,
            last_bank_calc REAL,
            last_bonus REAL DEFAULT 0,
            last_salary REAL DEFAULT 0,
            referrer_id INTEGER DEFAULT 0,
            ref_count INTEGER DEFAULT 0,
            stolen INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_apartments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            apartment_name TEXT,
            price INTEGER,
            buy_time REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_companies (
            user_id INTEGER PRIMARY KEY,
            level INTEGER DEFAULT 1,
            last_claim REAL,
            earned_balance INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            reward INTEGER,
            activations_left INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_promo (
            user_id INTEGER,
            code TEXT,
            PRIMARY KEY (user_id, code)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_quests (
            user_id INTEGER,
            day_num INTEGER,
            quest_id INTEGER,
            progress INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            last_reset_date TEXT,
            PRIMARY KEY (user_id, day_num, quest_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action_type TEXT,
            action_details TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_name TEXT,
            price INTEGER,
            sold INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    # Миграция для уже существующей базы: добавляем счётчик украденных вещей.
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN stolen INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.close()

def log_user_action(user_id, username, action_type, details):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO user_logs (user_id, username, action_type, action_details) VALUES (?, ?, ?, ?)", (user_id, username, action_type, details))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Ошибка логирования: {e}")

# --- СИСТЕМА КВЕСТОВ ---
def get_current_quest_day():
    epoch_days = int(time.time() // 86400)
    return (epoch_days % 3) + 1

def update_quest_progress(user_id, quest_type, amount=1):
    day_num = get_current_quest_day()
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT progress, completed FROM user_quests WHERE user_id = ? AND day_num = ? AND quest_id = ?", (user_id, day_num, quest_type))
    row = cursor.fetchone()
    
    if not row:
        cursor.execute("INSERT INTO user_quests (user_id, day_num, quest_id, progress, completed, last_reset_date) VALUES (?, ?, ?, 0, 0, ?)", (user_id, day_num, quest_type, today_str))
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
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
        cursor.execute("UPDATE user_quests SET progress = ?, completed = 1 WHERE user_id = ? AND day_num = ? AND quest_id = ?", (new_progress, user_id, day_num, quest_type))
    else:
        cursor.execute("UPDATE user_quests SET progress = ? WHERE user_id = ? AND day_num = ? AND quest_id = ?", (new_progress, user_id, day_num, quest_type))
    conn.commit()
    conn.close()

async def check_ban_and_register(message: Message, command: CommandObject = None):
    user = message.from_user
    user_id, username = user.id, user.username or user.first_name
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT banned FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()

        if res is None:
            ref_id = 0
            if command and command.args and command.args.startswith("ref_"):
                try:
                    potential_ref = int(command.args.replace("ref_", ""))
                    if potential_ref != user_id:
                        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (potential_ref,))
                        if cursor.fetchone(): ref_id = potential_ref
                except: pass

            cursor.execute("INSERT INTO users (user_id, username, balance, last_bank_calc, referrer_id) VALUES (?, ?, 10000, ?, ?)", (user_id, username, time.time(), ref_id))
            if ref_id != 0:
                cursor.execute("UPDATE users SET balance = balance + 1000000, ref_count = ref_count + 1 WHERE user_id = ?", (ref_id,))
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
            cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
            conn.commit()
        return True
    finally:
        conn.close()

async def is_creator(user_id: int) -> bool:
    if user_id in ADMIN_IDS: return True
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_creator FROM users WHERE user_id = ?", (user_id,))
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

# --- СТАРТ И МЕНЮ ---
@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    if not await check_ban_and_register(message, command): return
    await smart_answer(message, "🏠 Главное меню игры:\nДобро пожаловать! Используйте кнопки меню или команды.")

@router.message(F.text.casefold().in_(["главное меню", "меню"]))
async def text_main_menu(message: Message):
    if not await check_ban_and_register(message): return
    await smart_answer(message, "🏠 Главное меню игры:")

HELP_TEXT = (
    "📖 Инструкция по игре:\n\n"
    "🎰 Казино (пишите строго по шаблонам):\n"
    "• рул кра [ставка] (или чер, чет, нечет, мал, бол, ряд 1-3, число 0-36)\n"
    "• покер [ставка]\n"
    "• фортуна [ставка]\n"
    "• дартс [ставка] центр (или мимо)\n"
    "• баскет [ставка]\n\n"
    "💰 Переводы и Банк:\n"
    "• пер @юз сумма (или пер вб)\n"
    "• вложить [сумма] / снять [сумма]\n\n"
    "🥷 Нычка:\n"
    "• нычка положить [сумма]\n"
    "• нычка взять [сумма]\n\n"
    "🔫 Банда и Работа:\n"
    "• Меню «Банда» — ограбление банка каждый час\n"
    "• Меню «Работа» — сбор бутылок и металла"
)

@router.message(F.text.casefold().in_(["помощь", "📖 помощь"]))
async def cmd_help(message: Message):
    if not await check_ban_and_register(message): return
    await message.answer(HELP_TEXT, parse_mode="MARKDOWN")

# --- КВЕСТЫ ---
@router.message(F.text.casefold().in_(["📜 квесты", "квесты"]))
async def text_quests(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    day_num = get_current_quest_day()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT quest_id, progress, completed FROM user_quests WHERE user_id = ? AND day_num = ?", (user_id, day_num))
    rows = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}
    conn.close()

    if day_num == 1: q1, q2, q3 = "Сыграй в казино на 1кк", "Сыграй в баскетбол на 2кк", "Введи промокод"
    elif day_num == 2: q1, q2, q3 = "Ограбь банк в банде", "Сыграй в дартс на 2кк", "Закинь в заначку"
    else: q1, q2, q3 = "Собери металл 5 раз", "Собери бутылки 5 раз", "Сыграй в покер на 1кк"

    def mark(qid): return "✅" if qid in rows and rows[qid][1] == 1 else "❌"
    text = f"📜 Ежедневные квесты (День {day_num}/3):\n\n1. {q1} — {mark(1)}\n2. {q2} — {mark(2)}\n3. {q3} — {mark(3)}"
    await message.answer(text, parse_mode="MARKDOWN")

# --- НЫЧКА ---
@router.message(F.text.casefold().in_(["🥷 нычка", "нычка", "заначка"]))
async def text_nyachka_menu(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT nyachka FROM users WHERE user_id = ?", (user_id,))
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
    cursor.execute("SELECT balance, nyachka FROM users WHERE user_id = ?", (user_id,))
    u_row = cursor.fetchone()
    bal, nyach = u_row[0], (u_row[1] or 0)
    
    if action == "положить":
        if bal < amount:
            await message.answer("❌ Недостаточно наличных!")
            conn.close()
            return
        cursor.execute("UPDATE users SET balance = balance - ?, nyachka = nyachka + ? WHERE user_id = ?", (amount, amount, user_id))
        conn.commit()
        conn.close()
        update_quest_progress(user_id, 3, amount if get_current_quest_day() == 2 else 1)
        await message.answer(f"🥷 Спрятано в нычку: {amount:,} ¢".replace(",", " "))
    else:
        if nyach < amount:
            await message.answer("❌ В нычке нет столько!")
            conn.close()
            return
        cursor.execute("UPDATE users SET nyachka = nyachka - ?, balance = balance + ? WHERE user_id = ?", (amount, amount, user_id))
        conn.commit()
        conn.close()
        await message.answer(f"🥷 Забрано из нычки: {amount:,} ¢".replace(",", " "))

# --- БАНДА ---
@router.message(F.text.casefold().in_(["🔫 банда", "банда"]))
async def text_gang_menu(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT gang_name FROM users WHERE user_id = ?", (user_id,))
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
    cursor.execute("UPDATE users SET gang_name = 'Уличные волки' WHERE user_id = ?", (user_id,))
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
    cursor.execute("SELECT gang_name FROM users WHERE user_id = ?", (user_id,))
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
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (payout, user_id))
        conn.commit()
        conn.close()
        update_quest_progress(user_id, 1, 1) # Квест банк день 2
        await callback.answer(f"🎉 Успех! Украдено {payout:,} ¢!".replace(",", " "), show_alert=True)
    else:
        conn.close()
        await callback.answer("🚨 Вас поймала полиция!", show_alert=True)

# --- ПРОФИЛЬ И ТОП ---
def format_profile(user_data):
    user_id, display_name, balance, bank_balance, invested, apt_count, ref_count, is_creat = user_data
    badge = " 🎬" if is_creat == 1 else ""
    return (
        f"👤 Профиль игрока: {display_name}{badge}\n\n"
        f"💵 Баланс наличными: {balance:,} ¢\n"
        f"🏦 В банке: {bank_balance:,} ¢\n"
        f"📈 Вложено во флипинг: {invested:,} ¢\n"
        f"🏢 Недвижимость: {apt_count}/10 шт.\n"
        f"👥 Приглашено друзей: {ref_count} шт."
    ).replace(",", " ")

@router.message(F.text.casefold().in_(["👤 профиль", "я"]))
async def msg_profile(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT custom_name, username, balance, bank_balance, invested, is_creator FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    custom_name, uname, balance, bank_balance, invested, is_creat = u if u else (None, "user", 10000, 0, 0, 0)
    display_name = custom_name if custom_name else (uname if uname else message.from_user.first_name)
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = ?", (user_id,))
    apt_count = cursor.fetchone()[0]
    cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (user_id,))
    ref_count = cursor.fetchone()[0] or 0
    conn.close()
    await message.answer(format_profile((user_id, display_name, balance, bank_balance, invested, apt_count, ref_count, is_creat)))

@router.message(F.text.casefold() == "чекнуть")
async def msg_check_profile(message: Message):
    if not await check_ban_and_register(message): return
    if not message.reply_to_message:
        await message.answer("❌ Ответьте этой командой на сообщение игрока.")
        return
    target_id = message.reply_to_message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT custom_name, username, balance, bank_balance, invested, is_creator FROM users WHERE user_id = ?", (target_id,))
    u = cursor.fetchone()
    if not u:
        await message.answer("❌ Игрок не найден.")
        conn.close()
        return
    custom_name, uname, balance, bank_balance, invested, is_creat = u
    display_name = custom_name if custom_name else (uname if uname else message.reply_to_message.from_user.first_name)
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = ?", (target_id,))
    apt_count = cursor.fetchone()[0]
    cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (target_id,))
    ref_count = cursor.fetchone()[0] or 0
    conn.close()
    await smart_answer(message, format_profile((target_id, display_name, balance, bank_balance, invested, apt_count, ref_count, is_creat)))

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
    cursor.execute("UPDATE users SET custom_name = ? WHERE user_id = ?", (new_name, user_id))
    conn.commit(); conn.close()
    log_user_action(user_id, message.from_user.username or message.from_user.first_name, "Профиль", f"Установил ник: {new_name}")
    await message.answer(f"✅ Твой ник теперь: {new_name}\n🏆 В топе будет показываться именно он.")

@router.message(F.text.casefold().in_(["🏆 топ", "топ", "топ игроков"]))
async def text_top(message: Message):
    if not await check_ban_and_register(message): return
    conn = get_db()
    cursor = conn.cursor()
    placeholders = ','.join(['?'] * len(ADMIN_IDS)) if ADMIN_IDS else '0'
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
        # Экранируем спецсимволы Markdown, чтобы они не ломали отправку сообщения
        safe_name = display_name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")
        badge = " 🎬" if is_creat == 1 else ""
        text += f"{idx}. {safe_name}{badge} — {total:,} ¢\n".replace(",", " ")
        
    await smart_answer(message, text)

# --- РАБОТА (КД: МЕТАЛЛ 4С, БУТЫЛКИ 2С, ВОРОВСТВО 6С) ---
@router.message(F.text.casefold().in_(["👷 работа", "работа", "ферма"]))
async def text_work(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = ?", (user_id,))
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
    cursor.execute("UPDATE users SET bottles = bottles + 1 WHERE user_id = ?", (user_id,))
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = ?", (user_id,))
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
    cursor.execute("UPDATE users SET metal = metal + 1 WHERE user_id = ?", (user_id,))
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = ?", (user_id,))
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
    cursor.execute("UPDATE users SET stolen = COALESCE(stolen, 0) + 1 WHERE user_id = ?", (user_id,))
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = ?", (user_id,))
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
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close(); await callback.answer("❌ Игрок не найден!", show_alert=True); return
    bal, b, m, st = row[0], row[1], row[2], row[3] or 0
    if b == 0 and m == 0 and st == 0:
        conn.close(); await callback.answer("❌ Нет ресурсов для продажи!", show_alert=True); return
    payout = (b * 1000) + (m * 5000) + (st * 10000)
    cursor.execute("UPDATE users SET balance = balance + ?, bottles = 0, metal = 0, stolen = 0 WHERE user_id = ?", (payout, user_id))
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = ?", (user_id,))
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

# --- КОМПАНИИ ПО ФЛИПИНГУ ---
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
    cursor.execute("SELECT level, last_claim, earned_balance FROM user_companies WHERE user_id = ?", (user_id,))
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
        if hours_passed > 24:
            hours_passed = 24
            
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
        
        kb_rows = [[InlineKeyboardButton(text="💶 Забрать прибыль", callback_data="claim_company_profit")]]
        if upgrade_btn:
            kb_rows.append([upgrade_btn])
        keyboard = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        return text, keyboard

@router.message(F.text.casefold().in_(["компания", "компания по флипингу", "🏢 компания по флипингу 💸"]))
async def text_company_menu(message: Message):
    if not await check_ban_and_register(message): return
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА/КОМАНДА", "Компания по флипингу")
    text, keyboard = get_company_data_and_keyboard(message.from_user.id)
    await message.answer(text, reply_markup=keyboard, parse_mode="MARKDOWN")

@router.callback_query(F.data == "buy_company")
async def callback_buy_company(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    bal = cursor.fetchone()[0]
    
    cost = 100_000_000
    if bal < cost:
        await callback.answer("❌ У вас недостаточно наличных для покупки компании (нужно 100 млн ¢)!", show_alert=True)
        conn.close()
        return
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
    cursor.execute("INSERT OR REPLACE INTO user_companies (user_id, level, last_claim, earned_balance) VALUES (?, 1, ?, 0)", (user_id, time.time()))
    conn.commit()
    conn.close()
    
    await callback.answer("🎉 Вы успешно купили компанию по флипингу 1 уровня!", show_alert=True)
    text, keyboard = get_company_data_and_keyboard(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except:
        pass

@router.callback_query(F.data == "claim_company_profit")
async def callback_claim_company(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT level, last_claim, earned_balance FROM user_companies WHERE user_id = ?", (user_id,))
    comp = cursor.fetchone()
    
    if not comp:
        await callback.answer("❌ У вас нет компании.", show_alert=True)
        conn.close()
        return
        
    level, last_claim, earned_bal = comp
    now = time.time()
    hours_passed = (now - last_claim) / 3600
    if hours_passed > 24:
        hours_passed = 24
        
    current_income_per_hour = COMPANY_LEVELS[level]["income"]
    accrued = int(hours_passed * current_income_per_hour)
    total_to_claim = earned_bal + accrued
    
    if total_to_claim <= 0:
        await callback.answer("❌ Пока еще ничего не накопилось!", show_alert=True)
        conn.close()
        return

    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (total_to_claim, user_id))
    cursor.execute("UPDATE user_companies SET last_claim = ?, earned_balance = 0 WHERE user_id = ?", (now, user_id))
    conn.commit()
    conn.close()
    
    await callback.answer(f"💶 Вы успешно забрали прибыль: +{total_to_claim:,} ¢!".replace(",", " "), show_alert=True)
    text, keyboard = get_company_data_and_keyboard(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except:
        pass

@router.callback_query(F.data == "upgrade_company")
async def callback_upgrade_company(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT level, last_claim, earned_balance FROM user_companies WHERE user_id = ?", (user_id,))
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
    if hours_passed > 24:
        hours_passed = 24
    accrued = int(hours_passed * COMPANY_LEVELS[level]["income"])
    total_earned = earned_bal + accrued

    next_level = level + 1
    upgrade_cost = COMPANY_LEVELS[next_level]["cost"]

    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    bal = cursor.fetchone()[0]

    if bal < upgrade_cost:
        await callback.answer(f"❌ Недостаточно средств для прокачки! Нужно {upgrade_cost:,} ¢".replace(",", " "), show_alert=True)
        conn.close()
        return

    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (upgrade_cost, user_id))
    cursor.execute("UPDATE user_companies SET level = ?, last_claim = ?, earned_balance = ? WHERE user_id = ?", 
                   (next_level, now, total_earned, user_id))
    conn.commit()
    conn.close()
    await callback.answer(f"📈 Компания успешно прокачана до {next_level} уровня!", show_alert=True)
    
    text, keyboard = get_company_data_and_keyboard(user_id)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except:
        pass

# --- КАЗИНО ---
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
        "• фортуна [ставка]\n"
        "• дартс [ставка] центр (или мимо)\n"
        "• баскет [ставка]"
    )
    await smart_answer(message, text)

@router.message(F.text.regexp(r"(?i)^рул\s+.+"))
async def casino_roulette(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "Игра", "Играл: Рулетка")
    args = message.text.split()
    if len(args) < 3:
        await message.answer("❌ Ошибка формата! Пример: рул кра 10к или рул 1-12 10к")
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
        if len(args) < 3:
            await message.answer("❌ Пример: рул кра 10к")
            return

        raw_amount_str = args[2].lower()

    # Разрешенные варианты ставок
    valid_exact_targets = [
        "кра", "чер", "чет", "нечет", "мал", "бол",
        "1-12", "13-24", "25-36",
        "ряд 1", "ряд 2", "ряд 3"
    ]

    is_valid = target_str in valid_exact_targets

    if not is_valid:
        try:
            num_val = int(target_str)
            if 0 <= num_val <= 36:
                is_valid = True
        except:
            pass
        
    is_valid = target_str in valid_exact_targets
    if not is_valid:
        try:
            num_val = int(target_str)
            if 0 <= num_val <= 36: is_valid = True
        except: pass

    if not is_valid:
        await message.answer("❌ Неверная ставка! Пишите строго: рул кра, рул чер, рул мал, рул бол, рул 1-12, рул 13-24, рул 25-36 или число от 0 до 36.")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    bal = cursor.fetchone()[0]

    amount = bal if raw_amount_str in ["вб", "все", "all"] else parse_sum(raw_amount_str)
    if not amount or amount <= 0 or bal < amount:
        await message.answer("❌ Неверная сумма или недостаточно средств.")
        conn.close()
        return

    rolled_num = random.randint(0, 36)
    rolled_color = "🟢" if rolled_num == 0 else ("🔴" if rolled_num in RED_NUMBERS else "⚫")

    won, payout = False, 0

    if target_str == "кра" and rolled_num != 0 and rolled_num in RED_NUMBERS:
        won, payout = True, amount * 2

    elif target_str == "чер" and rolled_num != 0 and rolled_num not in RED_NUMBERS:
        won, payout = True, amount * 2

    elif target_str == "чет" and rolled_num != 0 and rolled_num % 2 == 0:
        won, payout = True, amount * 2

    elif target_str == "нечет" and rolled_num != 0 and rolled_num % 2 != 0:
        won, payout = True, amount * 2

    elif target_str == "мал" and 1 <= rolled_num <= 18:
        won, payout = True, amount * 2

    elif target_str == "бол" and 19 <= rolled_num <= 36:
        won, payout = True, amount * 2

    elif target_str == "1-12" and 1 <= rolled_num <= 12:
        won, payout = True, amount * 3

    elif target_str == "13-24" and 13 <= rolled_num <= 24:
        won, payout = True, amount * 3

    elif target_str == "25-36" and 25 <= rolled_num <= 36:
        won, payout = True, amount * 3

    elif target_str == "ряд 1" and rolled_num in [1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34]:
        won, payout = True, amount * 3

    elif target_str == "ряд 2" and rolled_num in [2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35]:
        won, payout = True, amount * 3

    elif target_str == "ряд 3" and rolled_num in [3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36]:
        won, payout = True, amount * 3

    else:
        try:
            if int(target_str) == rolled_num:
                won, payout = True, amount * 36
        except:
            pass

    net_profit = payout - amount if won else -amount
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net_profit, user_id))
    conn.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
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
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "Игра", "Играл: Покер")
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Формат: покер 10к")
        return
    amount = parse_sum(args[1])
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    bal = cursor.fetchone()[0]
    if not amount or amount <= 0 or bal < amount:
        await message.answer("❌ Ошибка ставки.")
        conn.close()
        return

    won = random.choice([True, False])
    net = amount if won else -amount
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net, user_id))
    conn.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.close()

    update_quest_progress(user_id, 3, amount if get_current_quest_day() == 3 else 0)
    if won: await message.answer(f"🎉 Победа в покере! +{amount:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))
    else: await message.answer(f"😢 Проигрыш в покере. -{amount:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))

@router.message(F.text.regexp(r"(?i)^фортуна\s+.+"))
async def casino_wheel(message: Message):
    if not await check_ban_and_register(message): return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Формат: фортуна 10к")
        return
    amount = parse_sum(args[1])
    user_id = message.from_user.id
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "Игра", "Играл: Фортуна")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    bal = cursor.fetchone()[0]
    if not amount or amount <= 0 or bal < amount:
        await message.answer("❌ Ошибка ставки.")
        conn.close()
        return

    mult = random.choice([-1.0, -0.5, 0.5, 1.0, 2.0, 5.0])
    net = int(amount * mult)
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net, user_id))
    conn.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.close()

    if net > 0: await message.answer(f"🎡 Фортуна: Выигрыш +{net:,} ¢ (x{mult})\nБаланс: {new_bal:,} ¢".replace(",", " "))
    elif net < 0: await message.answer(f"🎡 Фортуна: Проигрыш {net:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))
    else: await message.answer(f"🎡 Фортуна: Ничья!\nБаланс: {new_bal:,} ¢".replace(",", " "))

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
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "Игра", "Играл: Дартс")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
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

    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net, user_id))
    conn.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
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
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "Игра", "Играл: Баскетбол")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    bal = cursor.fetchone()[0]
    if not amount or amount <= 0 or bal < amount:
        await message.answer("❌ Неверная ставка.")
        conn.close()
        return

    msg_dice = await message.answer_dice(emoji="🏀")
    await asyncio.sleep(3)
    won = msg_dice.dice.value in [4, 5]
    net = amount if won else -amount

    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net, user_id))
    conn.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.close()

    update_quest_progress(user_id, 2, amount if get_current_quest_day() == 1 else 0)
    if won: await message.answer(f"🏀 Гол! +{amount:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))
    else: await message.answer(f"🏀 Мимо! -{amount:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))

# --- БАНК И ПЕРЕВОДЫ ---
@router.message(F.text.casefold().in_(["🏦 банк", "баланс"]))
async def text_bank(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, bank_balance FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    conn.close()
    balance, bank_balance = (u[0], u[1]) if u else (10000, 0)
    await smart_answer(message, f"🏦 Банк:\n💰 Наличные: {balance:,} ¢\n🏛 В банке: {bank_balance:,} ¢\n\n• вложить [сумма]\n• снять [сумма]".replace(",", " "))

@router.message(F.text.regexp(r"(?i)^(вложить|вл)\s+.+"))
async def bank_deposit(message: Message):
    if not await check_ban_and_register(message): return
    amount = parse_sum(message.text.split()[1])
    if not amount or amount <= 0: return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone()[0] < amount:
        await message.answer("❌ Недостаточно налички.")
        conn.close()
        return
    cursor.execute("UPDATE users SET balance = balance - ?, bank_balance = bank_balance + ? WHERE user_id = ?", (amount, amount, user_id))
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
    cursor.execute("SELECT bank_balance FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone()[0] < amount:
        await message.answer("❌ В банке нет столько.")
        conn.close()
        return
    cursor.execute("UPDATE users SET bank_balance = bank_balance - ?, balance = balance + ? WHERE user_id = ?", (amount, amount, user_id))
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
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (sender_id,))
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
        cursor.execute("SELECT user_id, custom_name, username FROM users WHERE LOWER(username) = ?", (target_username,))
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

    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, sender_id))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
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
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА/КОМАНДА", "Флипинг")
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = ?", (user_id,))
    apt_count = cursor.fetchone()[0]
    conn.close()
    price = random.randint(1_000_000, 100_000_000)
    apt_name = random.choice(APARTMENT_NAMES)

    text = (
        f"🏢 Рынок недвижимости (Флипинг):\n\n"
        f"• {apt_name}\n"
        f"💰 Цена покупки: {price:,} ¢\n\n"
        f"📊 У вас недвижимости: {apt_count}/10 шт."
    ).replace(",", " ")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
         [InlineKeyboardButton(text="🛒 Купить квартиру", callback_data=f"buy_apt_{price}_{apt_name[:12]}"),
         InlineKeyboardButton(text="⏭ Другой вариант", callback_data="refresh_flip")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="MARKDOWN")

@router.callback_query(F.data == "refresh_flip")
async def refresh_flipping(callback: CallbackQuery):
    price = random.randint(1_000_000, 100_000_000)
    apt_name = random.choice(APARTMENT_NAMES)
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = ?", (user_id,))
    apt_count = cursor.fetchone()[0]
    conn.close()

    text = (
        f"🏢 Рынок недвижимости (Флипинг):\n\n"
        f"• {apt_name}\n"
        f"💰 Цена покупки: {price:,} ¢\n\n"
        f"📊 У вас недвижимости: {apt_count}/10 шт."
    ).replace(",", " ")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить квартиру", callback_data=f"buy_apt_{price}_{apt_name[:12]}"),
         InlineKeyboardButton(text="⏭ Другой вариант", callback_data="refresh_flip")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MARKDOWN")

@router.callback_query(F.data.startswith("buy_apt_"))
async def buy_apartment(callback: CallbackQuery):
    user_id = callback.from_user.id
    parts = callback.data.split("_")
    price = int(parts[2])
    apt_name = " ".join(parts[3:]) if len(parts) > 3 else "Квартира"

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0
    if bal < price:
        await callback.answer("❌ Недостаточно средств для покупки!", show_alert=True)
        conn.close()
        return

    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = ?", (user_id,))
    apt_res = cursor.fetchone()
    apt_count = apt_res[0] if apt_res else 0
    if apt_count >= 10:
        await callback.answer("❌ Лимит недвижимости (максимум 10)!", show_alert=True)
        conn.close()
        return

    cursor.execute("UPDATE users SET balance = balance - ?, invested = invested + ? WHERE user_id = ?", (price, price, user_id))
    cursor.execute("INSERT INTO user_apartments (user_id, apartment_name, price, buy_time) VALUES (?, ?, ?, ?)", 
                   (user_id, apt_name, price, time.time()))
    conn.commit()

    new_price = random.randint(1_000_000, 100_000_000)
    new_apt_name = random.choice(APARTMENT_NAMES)
    
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = ?", (user_id,))
    new_apt_count = cursor.fetchone()[0]
    conn.close()

    text = (
        f"🏢 Рынок недвижимости (Флипинг):\n\n"
        f"• {new_apt_name}\n"
        f"💰 Цена покупки: {new_price:,} ¢\n\n"
        f"📊 У вас недвижимости: {new_apt_count}/10 шт."
    ).replace(",", " ")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить квартиру", callback_data=f"buy_apt_{new_price}_{new_apt_name[:12]}"),
         InlineKeyboardButton(text="⏭ Другой вариант", callback_data="refresh_flip")]
    ])
    
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except:
        pass
    await callback.answer("🏠 Квартира успешно куплена!", show_alert=True)

@router.message(F.text.casefold().in_(["🏠 квартиры", "мои квартиры", "квартиры"]))
async def text_my_apartments(message: Message):
    if not await check_ban_and_register(message): return
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА/КОМАНДА", "Квартиры")
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, apartment_name, price FROM user_apartments WHERE user_id = ?", (user_id,))
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
    cursor.execute("SELECT price FROM user_apartments WHERE id = ? AND user_id = ?", (apt_id, user_id))
    apt = cursor.fetchone()
    if not apt:
        await callback.answer("❌ Квартира не найдена.", show_alert=True)
        conn.close()
        return

    price = apt[0]
    percent = random.uniform(-0.05, 0.05)
    sell_price = int(price * (1 + percent))
    if sell_price < 1: sell_price = 1

    cursor.execute("DELETE FROM user_apartments WHERE id = ?", (apt_id,))
    cursor.execute("UPDATE users SET balance = balance + ?, invested = invested - ? WHERE user_id = ?", (sell_price, price, user_id))
    conn.commit()

    cursor.execute("SELECT id, apartment_name, price FROM user_apartments WHERE user_id = ?", (user_id,))
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
        except:
            pass

    await callback.answer(f"💰 Продано за {sell_price:,} ¢ (Разница: {diff_sign} ¢)".replace(",", " "), show_alert=True)

# --- БОНУС И РЕФЕРАЛЫ ---
@router.message(F.text.casefold().in_(["🎁 бонус", "бонус"]))
async def text_bonus(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,))
    last_bonus = cursor.fetchone()[0] or 0.0
    if time.time() - last_bonus < 86400:
        await message.answer("⏳ Бонус доступен раз в 24 часа!")
        conn.close()
        return
    
    # Награда изменена на 500 000 ¢
    cursor.execute("UPDATE users SET balance = balance + 500000, last_bonus = ? WHERE user_id = ?", (time.time(), user_id))
    conn.commit()
    conn.close()
    await message.answer("🎉 Бонус получен: +500 000 ¢!")

@router.message(F.text.casefold().in_(["👥 рефералы", "реф", "рефералы"]))
async def text_referral(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (user_id,))
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

# --- ПРОМОКОДЫ И КРЕАТОРЫ (ФОРМАТ: Промо [код] [сумма] [активации]) ---
@router.message(F.text.regexp(r"(?i)^промокод\s+\w+$"))
async def activate_promo_code(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    code = message.text.split()[1].upper()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT reward, activations_left FROM promo_codes WHERE code = ?", (code,))
    promo = cursor.fetchone()
    if not promo or promo[1] <= 0:
        await message.answer("❌ Промокод не найден или истек.")
        conn.close()
        return
    reward = promo[0]
    cursor.execute("INSERT OR IGNORE INTO user_promo (user_id, code) VALUES (?, ?)", (user_id, code))
    cursor.execute("UPDATE promo_codes SET activations_left = activations_left - 1 WHERE code = ?", (code,))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
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
    cursor.execute("UPDATE users SET balance = balance + 7500000 WHERE user_id = ?", (user_id,))
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
    cursor.execute("INSERT OR REPLACE INTO promo_codes (code, reward, activations_left) VALUES (?, ?, ?)", (code, reward, activations))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Промокод {code} создан на {reward:,} ¢ (активаций: {activations})!".replace(",", " "))

# --- АДМИН-ПАНЕЛЬ ---
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
    cursor.execute("SELECT user_id, custom_name, username FROM users WHERE LOWER(username) = ?", (username,))
    user = cursor.fetchone()
    if not user:
        conn.close(); await message.answer("❌ Игрок с таким username не найден."); return
    cursor.execute("SELECT action_type, action_details, timestamp FROM user_logs WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user[0],))
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
            cursor.execute("SELECT user_id FROM users WHERE username = ?", (target_raw.replace("@", ""),))
            res = cursor.fetchone()
            if res: uid = res[0]
        if not uid:
            await message.answer("❌ Игрок не найден.")
            conn.close()
            return
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, uid))
        conn.commit()
        conn.close()
        await state.clear()
        await message.answer(f"✅ Баланс игроку {target_raw} успешно изменен на {amount:,} ¢!".replace(",", " "))
    except:
        await message.answer("❌ Ошибка формата! Пример: `@user 1кк`")

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
    cursor.execute("UPDATE users SET banned = 1 WHERE username = ?", (uname,))
    conn.commit()
    conn.close()
    await message.answer(f"🚫 Игрок @{uname} забанен.")

@router.message(F.text.lower().startswith("разбан "))
async def admin_unban_user(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    uname = message.text.split(maxsplit=1)[1].strip().replace("@", "")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET banned = 0 WHERE username = ?", (uname,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Игрок @{uname} разбанен.")

@router.message(F.text.casefold().regexp(r"^выдавать\s+креатора\s+"))
async def admin_give_creator(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    uname = message.text.split(maxsplit=2)[2].strip().replace("@", "")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_creator = 1 WHERE username = ?", (uname,))
    conn.commit()
    conn.close()
    await message.answer(f"🎬 @{uname} назначен креатором.")

@router.message(F.text.casefold().regexp(r"^забрать\s+креатора\s+"))
async def admin_take_creator(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    uname = message.text.split(maxsplit=2)[2].strip().replace("@", "")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_creator = 0 WHERE username = ?", (uname,))
    conn.commit()
    conn.close()
    await message.answer(f"❌ У @{uname} забран креатор.")

@router.message(F.text.casefold().startswith("обнулить "))
async def admin_reset_user(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    uname = message.text.split(maxsplit=1)[1].strip().replace("@", "")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = 10000, bank_balance = 0, nyachka = 0, invested = 0, bottles = 0, metal = 0 WHERE username = ?", (uname,))
    conn.commit()
    conn.close()
    await message.answer(f"🔄 Игрок @{uname} обнулен.")

# --- СЕРВЕР И API ДЛЯ WEB APP КЕЙСОВ ---
async def index_handler(request):
    return web.FileResponse("index.html")

async def api_get_data(request):
    user_id = int(request.query.get("user_id", 0))
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 10000
    conn.close()
    return web.json_response({"balance": balance})

async def api_get_inventory(request):
    user_id = int(request.query.get("user_id", 0))
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, item_name, price FROM user_inventory WHERE user_id = ? AND sold = 0", (user_id,))
    items = [{"id": r[0], "name": r[1], "price": r[2]} for r in cursor.fetchall()]
    conn.close()
    return web.json_response(items)

async def api_open_case(request):
    data = await request.json()
    user_id = data.get("user_id")
    case_id = data.get("case_id")

    cases_config = {
        1: {"price": 5000000, "items": [
            ("Ключи от однушки", 500000, 45), ("Ключи от двушки", 2500000, 35),
            ("Ключи от трешки", 7500000, 15), ("Ключи от 4комнатной", 10000000, 5)
        ]},
        2: {"price": 10000000, "items": [
            ("Ключи от маленького домика", 3500000, 45), ("Средний домик", 6500000, 35),
            ("Большой дом", 12500000, 15), ("Пин Хаус", 20000000, 5)
        ]},
        3: {"price": 15000000, "items": [
            ("Жигуль", 5000000, 45), ("Камри", 10000000, 35),
            ("БМВ", 20000000, 15), ("БУГАТИ", 25000000, 5)
        ]},
        4: {"price": 25000000, "items": [
            ("Кп по кв", 15000000, 45), ("Кп по мш", 20000000, 35),
            ("Кп по дм", 30000000, 15), ("Кп по флт", 40000000, 5)
        ]}
    }

    if case_id not in cases_config:
        return web.json_response({"error": "Кейс не найден"}, status=400)

    case_info = cases_config[case_id]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row or row[0] < case_info["price"]:
        conn.close()
        return web.json_response({"error": "Недостаточно средств"}, status=400)

    new_balance = row[0] - case_info["price"]
    cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))

    rand_val = random.randint(1, 100)
    current_sum = 0
    won_item_name, won_item_price = case_info["items"][0][0], case_info["items"][0][1]
    for name, price, chance in case_info["items"]:
        current_sum += chance
        if rand_val <= current_sum:
            won_item_name, won_item_price = name, price
            break

    cursor.execute("INSERT INTO user_inventory (user_id, item_name, price) VALUES (?, ?, ?)", (user_id, won_item_name, won_item_price))
    conn.commit()
    conn.close()

    return web.json_response({"new_balance": new_balance, "item_name": won_item_name, "item_price": won_item_price})

async def api_sell_item(request):
    data = await request.json()
    user_id = data.get("user_id")
    item_id = data.get("item_id")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT price FROM user_inventory WHERE id = ? AND user_id = ? AND sold = 0", (item_id, user_id))
    item = cursor.fetchone()
    if not item:
        conn.close()
        return web.json_response({"success": False})

    price = item[0]
    cursor.execute("UPDATE user_inventory SET sold = 1 WHERE id = ?", (item_id,))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (price, user_id))
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.commit()
    conn.close()

    return web.json_response({"success": True, "new_balance": new_bal})

# --- НОВАЯ ФУНКЦИЯ ДЛЯ КАЗИНО (Шаг 1) ---
async def api_casino_spin(request):
    data = await request.json()
    user_id = data.get("user_id")
    bet = int(data.get("bet", 0))

    if bet <= 0:
        return web.json_response({"error": "Неверная ставка"}, status=400)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row or row[0] < bet:
        conn.close()
        return web.json_response({"error": "Недостаточно средств"}, status=400)

    balance = row[0] - bet
    cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (balance, user_id))

    rand_val = random.random()
    if rand_val < 0.15:  # 15% шанс на выигрыш
        if random.random() < 0.2:  # Редкие 777
            symbols = ["7️⃣", "7️⃣", "7️⃣"]
            payout = bet * 6  # x6
        else:
            sym = random.choice([["🍋", "🍋", "🍋"], ["🍇", "🍇", "🍇"], ["🎁", "🎁", "🎁"]])
            symbols = sym
            payout = bet * 4   # x4
        balance += payout
        won = True
    else:
        all_syms = ["7️⃣", "🍋", "🍇", "🎁"]
        symbols = [random.choice(all_syms), random.choice(all_syms), random.choice(all_syms)]
        if symbols[0] == symbols[1] and symbols[1] == symbols[2]:
            symbols[2] = "🍋" if symbols[0] != "🍋" else "🍇"
        payout = 0
        won = False

    cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (balance, user_id))
    conn.commit()
    conn.close()

    # Засчитываем прогресс квеста для первого дня (игра в казино)
    update_quest_progress(int(user_id), 1, bet if get_current_quest_day() == 1 else 0)

    return web.json_response({"new_balance": balance, "symbols": symbols, "won": won, "payout": payout})

# --- API ДЛЯ БЛЕКДЖЕКА ---
async def api_blackjack_action(request):
    data = await request.json()
    user_id = data.get("user_id")
    action = data.get("action")  # "start", "hit", "stand", "double"
    bet = int(data.get("bet", 0))
    game_state = data.get("game_state", {})

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return web.json_response({"error": "Пользователь не найден"}, status=400)
    
    balance = row[0]

    # Колода карт
    def create_deck():
        suits = ['♠', '♥', '♦', '♣']
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        deck = []
        for s in suits:
            for r in ranks:
                deck.append({"rank": r, "suit": s})
        random.shuffle(deck)
        return deck

    def get_card_value(hand):
        value = 0
        aces = 0
        for card in hand:
            r = card["rank"]
            if r in ['J', 'Q', 'K']:
                value += 10
            elif r == 'A':
                aces += 1
                value += 11
            else:
                value += int(r)
        while value > 21 and aces > 0:
            value -= 10
            aces -= 1
        return value

    if action == "start":
        if bet <= 0 or balance < bet:
            conn.close()
            return web.json_response({"error": "Недостаточно средств или неверная ставка"}, status=400)
        
        # Списываем ставку
        balance -= bet
        cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (balance, user_id))
        conn.commit()
        conn.close()

        deck = create_deck()
        player_hand = [deck.pop(), deck.pop()]
        dealer_hand = [deck.pop(), deck.pop()]

        p_val = get_card_value(player_hand)
        
        # Проверка на мгновенный Blackjack у игрока
        game_over = False
        message = ""
        payout = 0

        if p_val == 21:
            game_over = True
            payout = int(bet * 2.5)
            balance += payout
            message = "🔥 Блекджек! Вы выиграли!"
            
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (balance, user_id))
            conn.commit()
            conn.close()

        return web.json_response({
            "balance": balance,
            "player_hand": player_hand,
            "dealer_hand": [dealer_hand[0], {"rank": "?", "suit": "hidden"}], # скрываем вторую карту дилера
            "dealer_full_hidden": dealer_hand,
            "deck": deck,
            "bet": bet,
            "game_over": game_over,
            "message": message,
            "payout": payout
        })

    elif action == "hit":
        deck = game_state["deck"]
        player_hand = game_state["player_hand"]
        dealer_hidden = game_state["dealer_full_hidden"]
        bet = game_state["bet"]

        player_hand.append(deck.pop())
        p_val = get_card_value(player_hand)

        game_over = False
        message = ""
        payout = 0

        if p_val > 21:
            game_over = True
            message = "💥 Перебор! Вы проиграли."

        return web.json_response({
            "balance": balance,
            "player_hand": player_hand,
            "dealer_hand": [dealer_hidden[0], {"rank": "?", "suit": "hidden"}],
            "dealer_full_hidden": dealer_hidden,
            "deck": deck,
            "bet": bet,
            "game_over": game_over,
            "message": message,
            "payout": 0
        })

    elif action == "stand":
        deck = game_state["deck"]
        player_hand = game_state["player_hand"]
        dealer_hand = game_state["dealer_full_hidden"]
        bet = game_state["bet"]

        # Ход дилера (добирает пока меньше 17)
        while get_card_value(dealer_hand) < 17:
            dealer_hand.append(deck.pop())

        p_val = get_card_value(player_hand)
        d_val = get_card_value(dealer_hand)

        game_over = True
        payout = 0
        message = ""

        if d_val > 21:
            payout = bet * 2
            message = "🎉 У дилера перебор! Вы выиграли!"
        elif p_val > d_val:
            payout = bet * 2
            message = f"🎉 Победа! ({p_val} против {d_val})"
        elif p_val < d_val:
            message = f"😢 Дилер выиграл ({d_val} против {p_val})"
        else:
            payout = bet
            message = f"🤝 Ничья! ({p_val} : {d_val})"

        balance += payout
        cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (balance, user_id))
        conn.commit()
        conn.close()

        return web.json_response({
            "balance": balance,
            "player_hand": player_hand,
            "dealer_hand": dealer_hand,
            "bet": bet,
            "game_over": True,
            "message": message,
            "payout": payout
        })

    elif action == "double":
        if balance < bet:
            conn.close()
            return web.json_response({"error": "Недостаточно средств для удвоения"}, status=400)
        
        # Списываем доп ставку
        balance -= bet
        bet *= 2
        cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (balance, user_id))
        conn.commit()
        conn.close()

        deck = game_state["deck"]
        player_hand = game_state["player_hand"]
        dealer_hand = game_state["dealer_full_hidden"]

        player_hand.append(deck.pop())
        p_val = get_card_value(player_hand)

        if p_val > 21:
            return web.json_response({
                "balance": balance,
                "player_hand": player_hand,
                "dealer_hand": dealer_hand,
                "bet": bet,
                "game_over": True,
                "message": "💥 Перебор после удвоения! Вы проиграли.",
                "payout": 0
            })

        # Автоматический ход дилера после удвоения
        while get_card_value(dealer_hand) < 17:
            dealer_hand.append(deck.pop())

        d_val = get_card_value(dealer_hand)
        payout = 0
        message = ""

        if d_val > 21:
            payout = bet * 2
            message = "🎉 У дилера перебор! Вы выиграли удвоенную ставку!"
        elif p_val > d_val:
            payout = bet * 2
            message = f"🎉 Победа после удвоения! ({p_val} : {d_val})"
        elif p_val < d_val:
            message = f"😢 Дилер выиграл ({d_val} : {p_val})"
        else:
            payout = bet
            message = f"🤝 Ничья! Ставка возвращена."

        balance += payout
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (balance, user_id))
        conn.commit()
        conn.close()

        return web.json_response({
            "balance": balance,
            "player_hand": player_hand,
            "dealer_hand": dealer_hand,
            "bet": bet,
            "game_over": True,
            "message": message,
            "payout": payout
        })

    conn.close()
    return web.json_response({"error": "Неизвестное действие"}, status=400)

# --- API ДЛЯ МИНИ-ИГРЫ КРАШ (РАКЕТА) ---
# --- API ДЛЯ МИНИ-ИГРЫ КРАШ (РАКЕТА) ---
async def api_crash_bet(request):
    data = await request.json()
    user_id = data.get("user_id")
    bet = int(data.get("bet", 0))
    action = data.get("action")  # "start" или "cashout"
    cashout_multiplier = float(data.get("multiplier", 1.0))

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return web.json_response({"error": "Пользователь не найден"}, status=400)
    
    balance = row[0]

    if action == "start":
        if bet <= 0 or balance < bet:
            conn.close()
            return web.json_response({"error": "Недостаточно средств или неверная ставка"}, status=400)
        
        # Списываем ставку
        balance -= bet
        cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (balance, user_id))
        conn.commit()
        conn.close()

        # Генерация коэффициента краша с максимальным пределом 15.0x
        r = random.random()
        if r < 0.05:
            crash_point = 1.00  # Моментальный краш (5% шанс)
        else:
            crash_point = round(1.01 + (0.95 / (1.0 - random.random() * 0.95) - 0.95), 2)
            if crash_point > 15.0:
                crash_point = round(random.uniform(5.0, 15.0), 2)

        return web.json_response({
            "balance": balance,
            "crash_point": crash_point
        })

    elif action == "cashout":
        win_amount = int(bet * cashout_multiplier)
        balance += win_amount
        cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (balance, user_id))
        conn.commit()
        conn.close()

        return web.json_response({
            "balance": balance,
            "win_amount": win_amount
        })

    conn.close()
    return web.json_response({"error": "Неверное действие"}, status=400)

async def web_server():
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/api/get_data", api_get_data)
    app.router.add_get("/api/get_inventory", api_get_inventory)
    app.router.add_post("/api/open_case", api_open_case)
    app.router.add_post("/api/sell_item", api_sell_item)
    app.router.add_post("/api/casino_spin", api_casino_spin)
    app.router.add_post("/api/blackjack", api_blackjack_action)
    app.router.add_post("/api/crash", api_crash_bet)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    init_db()
    await web_server()
    
    from aiogram.types import MenuButtonWebApp, WebAppInfo
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="🎰 Кейсы",
            web_app=WebAppInfo(url="https://telegram-bot-tg9i.onrender.com")  # ЗАМЕНИТЕ НА ССЫЛКУ СВОЕГО СЕРВЕРА
        )
    )
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

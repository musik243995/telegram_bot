import random
import time
import os
import sqlite3
import asyncio
from aiogram import F, Bot, Dispatcher, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, FSInputFile
from aiogram.filters import Command, CommandObject
from aiohttp import web
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

API_TOKEN = "8885671207:AAEvMCPSWoiJZR8U_TXQvwgxJzK2sn28kKU"  # Твой токен

bot = Bot(token=API_TOKEN)
router = Router()
dp = Dispatcher()
dp.include_router(router)

from aiogram.fsm.state import State, StatesGroup

# Обязательно объявляем класс AdminStates вверху файла
class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_user_id_to_ban = State()
    waiting_for_balance_change = State()
    waiting_for_creator_action = State()  # Для выдачи/забора креатора через FSM (если нужно)
    waiting_for_reset = State()          # Для обнуления через FSM (если нужно)

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
            print("🚫 Заблокирована отправка спама!")
            return None
    return await original_send_message(chat_id, text, *args, **kwargs)

async def filtered_send_photo(chat_id, photo, caption=None, *args, **kwargs):
    if caption:
        caption_lower = str(caption).lower()
        if any(word in caption_lower for word in STOP_WORDS):
            print("🚫 Заблокирована отправка фото со спамом!")
            return None
    return await original_send_photo(chat_id, photo, caption=caption, *args, **kwargs)

bot.send_message = filtered_send_message
bot.send_photo = filtered_send_photo

# Словари для кулдаунов
work_cooldowns = {}
creator_salary_cooldowns = {}
company_claim_cooldowns = {}
game_cooldowns = {}

# --- СПИСОК АДМИНИСТРАТОРОВ ---
ADMIN_IDS = [1222239198, 8390540110]

# Красные числа на рулетке
RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

# Названия квартир для флипинга
APARTMENT_NAMES = [
    "Убитая хрущевка на окраине", "Сталинка в центре под ремонт", 
    "Студия в новостройке с черновой отделкой", "Элитный пентхаус с видом на парк", 
    "Бабушатник со старыми коврами", "Апартаменты в бизнес-центре", 
    "Малосемейка с общим балконом", "Просторная евротрешка", 
    "Аварийная квартира в старом фонде", "Люксовая вилла на закрытой территории"
]

# --- НИЖНЕЕ МЕНЮ С ЭМОДЗИ ---
def get_private_keyboard(is_creator_flag=False):
    kb = [
        [KeyboardButton(text="🎰 Казино"), KeyboardButton(text="👷 Работа")],
        [KeyboardButton(text="💸 Флипинг"), KeyboardButton(text="🏢 Компания по флипингу 💸")],
        [KeyboardButton(text="🏦 Банк"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🏆 Топ"), KeyboardButton(text="🏠 Квартиры")],
        [KeyboardButton(text="🎁 Бонус"), KeyboardButton(text="👥 Рефералы")],
        [KeyboardButton(text="📖 Помощь"), KeyboardButton(text="⚠️ Жалоба")]
    ]
    if is_creator_flag:
        kb.append([KeyboardButton(text="🎬 Креатор")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- БАЗА ДАННЫХ ---
def get_db():
    conn = sqlite3.connect("game.db")
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
            invested INTEGER DEFAULT 0,
            bottles INTEGER DEFAULT 0,
            metal INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            is_creator INTEGER DEFAULT 0,
            last_bank_calc REAL,
            last_bonus REAL DEFAULT 0,
            last_salary REAL DEFAULT 0,
            referrer_id INTEGER DEFAULT 0,
            ref_count INTEGER DEFAULT 0
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
        CREATE TABLE IF NOT EXISTS user_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action_type TEXT,
            action_details TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def log_user_action(user_id, username, action_type, details):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_logs (user_id, username, action_type, action_details) VALUES (?, ?, ?, ?)",
            (user_id, username, action_type, details)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Ошибка логирования: {e}")    

async def check_ban_and_register(message: Message, command: CommandObject = None):
    user = message.from_user
    user_id = user.id
    username = user.username or user.first_name

    conn = get_db()
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
                    if cursor.fetchone():
                        ref_id = potential_ref
            except:
                pass

        cursor.execute("""
            INSERT INTO users (user_id, username, balance, last_bank_calc, referrer_id) 
            VALUES (?, ?, 10000, ?, ?)
        """, (user_id, username, time.time(), ref_id))
        
        if ref_id != 0:
            cursor.execute("UPDATE users SET balance = balance + 100000, ref_count = ref_count + 1 WHERE user_id = ?", (ref_id,))
            try:
                await bot.send_message(
                    ref_id, 
                    f"🎉 По вашей реферальной ссылке зарегистрировался новый игрок! Вам начислено 100 000 ¢.".replace(",", " "), 
                    parse_mode="MARKDOWN"
                )
            except:
                pass

        conn.commit()
    elif res[0] == 1:
        conn.close()
        await message.answer("❌ Вы заблокированы и не можете пользоваться ботом.")
        return False
    else:
        cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
        conn.commit()
    conn.close()
    return True

async def is_creator(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
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
    files = []
    if os.path.exists(folder):
        files = [f for f in os.listdir(folder) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.mp4', '.gif'))]
    
    if files:
        chosen_file = random.choice(files)
        path = os.path.join(folder, chosen_file)
        input_file = FSInputFile(path)
        try:
            if chosen_file.lower().endswith(('.mp4', '.gif')):
                await message.answer_animation(input_file, caption=text, parse_mode="MARKDOWN")
            else:
                await message.answer_photo(input_file, caption=text, parse_mode="MARKDOWN")
            return
        except Exception:
            pass
    await message.answer(text, parse_mode="MARKDOWN")

def parse_sum(text_val):
    if not text_val:
        return None
    text_val = str(text_val).lower().strip().replace(" ", "")
    multiplier = 1
    if "кккк" in text_val or "tr" in text_val:
        multiplier = 1_000_000_000_000
        text_val = text_val.replace("кккк", "").replace("tr", "")
    elif "ккк" in text_val or "b" in text_val:
        multiplier = 1_000_000_000
        text_val = text_val.replace("ккк", "").replace("b", "")
    elif "кк" in text_val or "м" in text_val or "m" in text_val:
        multiplier = 1_000_000
        text_val = text_val.replace("кк", "").replace("м", "").replace("m", "")
    elif "к" in text_val or "k" in text_val:
        multiplier = 1_000
        text_val = text_val.replace("к", "").replace("k", "")
    try:
        return int(float(text_val) * multiplier)
    except:
        return None

# --- ГЛАВНОЕ МЕНЮ / СТАРТ ---
@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    if not await check_ban_and_register(message, command): return
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КОМАНДА", "/start")
    text = "🏠 Главное меню игры:\nДобро пожаловать! Используйте кнопки меню или команды."
    await smart_answer(message, text)

@router.message(F.text.casefold().in_(["главное меню", "меню"]))
async def text_main_menu(message: Message):
    if not await check_ban_and_register(message): return
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА", "Главное меню")
    await smart_answer(message, "🏠 Главное меню игры:")

HELP_TEXT = (
    "Играть в игры нажми на кнопку \"🎰 Казино\" в боте.\n"
    "Переводы пример: Пер @юз сумма\n"
    "Также по кнопке флипинг покупай квартиры в боте и продавай мб уйдешь в окуп:).\n"
    "Закидуй в банк деньги чтоб получать процент:).\n"
    "Нажми на кнопку рефералы и нажми на кнопку поделиться за 1 человека +100 000¢.💸\n"
    "Получай бонус по кнопке в бота каждые 24 часа.💸\n"
    "Работай собирай бутылки и металл и меняй на деньги💰.\n"
    "Пиши \"я\" \"профиль\" чтобы смотреть свой профиль, пиши \"чекнуть\" под сообщением человека, чтобы смотреть его профиль.👀\n"
    "Пиши \"топ\", чтобы знать лучший ли ты🏅.\n"
    "Смотри свои квартиры по кнопке в боте"
)

@router.message(F.text.casefold().in_(["помощь", "📖 помощь"]))
async def cmd_help(message: Message):
    if not await check_ban_and_register(message): return
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА/КОМАНДА", "Помощь")
    await message.answer(HELP_TEXT, parse_mode="MARKDOWN")
@router.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    await callback.message.answer(HELP_TEXT, parse_mode="MARKDOWN")
    await callback.answer()

# --- СИСТЕМА ЖАЛОБ ---
@router.message(F.text.casefold().in_(["жалоба", "⚠️ жалоба"]))
async def text_complaint_prompt(message: Message):
    if not await check_ban_and_register(message): return
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА", "Жалоба (старт)")
    await message.answer("⚠️ Напишите вашу жалобу следующим сообщением (одним текстом), и она будет отправлена администраторам игры.")

@router.message(F.text.regexp(r"(?i)^жалоба\s+(.+)"))
async def send_complaint(message: Message):
    if not await check_ban_and_register(message): return
    user = message.from_user
    text_comp = message.text.split(maxsplit=1)[1]
    username_str = f"@{user.username}" if user.username else f"ID: {user.id} (без юзернейма)"
    
    alert_text = (
        f"🚨 НОВАЯ ЖАЛОБА!\n\n"
        f"• От игрока: {username_str} (ID: {user.id})\n"
        f"• Текст: {text_comp}"
    )
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, alert_text, parse_mode="MARKDOWN")
        except:
            pass
            
    log_user_action(user.id, user.username or user.first_name, "ЖАЛОБА", text_comp)
    await message.answer("✅ Ваша жалоба успешно отправлена администрации ботам!")

@router.message(F.text.regexp(r"(?i)^\+ник\s+(.+)"))
async def set_custom_nickname(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Укажите новый ник! Пример: +ник Ларп", parse_mode="MARKDOWN")
        return
    new_name = args[1].strip()
    if len(new_name) > 20:
        await message.answer("❌ Ник слишком длинный! Максимум 20 символов.", parse_mode="MARKDOWN")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET custom_name = ? WHERE user_id = ?", (new_name, user_id))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Ваш игровой ник успешно изменен на: {new_name}", parse_mode="MARKDOWN")

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
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА/КОМАНДА", "Профиль")
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT custom_name, username, balance, bank_balance, invested, is_creator FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    
    if not u:
        custom_name, uname, balance, bank_balance, invested, is_creat = (None, "user", 10000, 0, 0, 0)
    else:
        custom_name, uname, balance, bank_balance, invested, is_creat = u

    display_name = custom_name if custom_name else (uname if uname else message.from_user.first_name)

    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = ?", (user_id,))
    apt_count = cursor.fetchone()[0]

    cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (user_id,))
    ref_res = cursor.fetchone()
    ref_count = ref_res[0] if ref_res and ref_res[0] is not None else 0
    conn.close()
    text = format_profile((user_id, display_name, balance, bank_balance, invested, apt_count, ref_count, is_creat))
    await message.answer(text)

@router.message(F.text.casefold() == "чекнуть")
async def msg_check_profile(message: Message):
    if not await check_ban_and_register(message): return
    if not message.reply_to_message:
        await message.answer("❌ Ответьте этой командой («чекнуть») на сообщение пользователя, профиль которого хотите посмотреть.")
        return
        
    target_id = message.reply_to_message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT custom_name, username, balance, bank_balance, invested, is_creator FROM users WHERE user_id = ?", (target_id,))
    u = cursor.fetchone()
    if not u:
        await message.answer("❌ Пользователь не найден в базе данных игры.")
        conn.close()
        return

    custom_name, uname, balance, bank_balance, invested, is_creat = u
    display_name = custom_name if custom_name else (uname if uname else message.reply_to_message.from_user.first_name)

    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = ?", (target_id,))
    apt_count = cursor.fetchone()[0]

    cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (target_id,))
    ref_res = cursor.fetchone()
    ref_count = ref_res[0] if ref_res and ref_res[0] is not None else 0
    conn.close()

    text = format_profile((target_id, display_name, balance, bank_balance, invested, apt_count, ref_count, is_creat))
    await smart_answer(message, text)

@router.message(F.text.casefold().in_(["🏆 топ", "топ", "топ игроков"]))
async def text_top(message: Message):
    if not await check_ban_and_register(message): return
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА/КОМАНДА", "Топ")
    conn = get_db()
    cursor = conn.cursor()
    
    placeholders = ','.join(['?'] * len(ADMIN_IDS)) if ADMIN_IDS else '0'
    query = f"""
        SELECT custom_name, username, (balance + bank_balance + invested) as total, is_creator 
        FROM users 
        WHERE user_id NOT IN ({placeholders}) 
        ORDER BY total DESC 
        LIMIT 10
    """
    cursor.execute(query, tuple(ADMIN_IDS))
    top_list = cursor.fetchall()
    conn.close()

    text = "🏆 Топ-10 самых богатых игроков:\n\n"
    for idx, (custom_name, uname, total, is_creat) in enumerate(top_list, 1):
        display_name = custom_name if custom_name else (uname if uname else "Игрок")
        safe_name = display_name.replace("*", "").replace("_", "").replace("`", "")
        badge = " 🎬" if is_creat == 1 else ""
        formatted_total = f"{total:,}".replace(",", " ")
        text += f"{idx}. {safe_name}{badge} — {formatted_total} ¢\n"

    await smart_answer(message, text)


# --- РАБОТА И МГНОВЕННОЕ ОБНОВЛЕНИЕ РЕСУРСОВ ---
@router.message(F.text.casefold().in_(["👷 работа", "работа", "ферма", "болото"]))
async def text_work(message: Message):
    if not await check_ban_and_register(message): return
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА/КОМАНДА", "Работа")
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, bottles, metal FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    conn.close()
    
    balance = u[0] if u else 10000
    bottles = u[1] if u else 0
    metal = u[2] if u else 0

    text = (
        f"👷 Центр сбора ресурсов:\n\n"
        f"🍾 Бутылок: {bottles} шт.\n"
        f"⚙️ Металла: {metal} шт.\n"
        f"💰 Наличные: {balance:,} ¢\n\n"
        "Собирайте ресурсы и продавайте их кнопкой ниже!"
    ).replace(",", " ")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍾 Собирать бутылки (КД 2с)", callback_data="work_bottles")],
        [InlineKeyboardButton(text="⚙ Собирать металл (КД 4с)", callback_data="work_metal")],
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
    conn.commit()
    cursor.execute("SELECT balance, bottles, metal FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    conn.close()

    bal, b, m = u
    new_text = (
        f"👷 Центр сбора ресурсов:\n\n"
        f"🍾 Бутылок: {b} шт.\n"
        f"⚙️ Металла: {m} шт.\n"
        f"💰 Наличные: {bal:,} ¢\n\n"
        "Собирайте ресурсы и продавайте их кнопкой ниже!"
    ).replace(",", " ")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍾 Собирать бутылки (КД 2с)", callback_data="work_bottles")],
        [InlineKeyboardButton(text="⚙ Собирать металл (КД 4с)", callback_data="work_metal")],
        [InlineKeyboardButton(text="💰 Продать ресурсы", callback_data="sell_resources")]
    ])
    
    try:
        await callback.message.edit_text(new_text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except:
        pass
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
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET metal = metal + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    cursor.execute("SELECT balance, bottles, metal FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    conn.close()

    bal, b, m = u
    new_text = (
        f"👷 Центр сбора ресурсов:\n\n"
        f"🍾 Бутылок: {b} шт.\n"
        f"⚙️ Металла: {m} шт.\n"
        f"💰 Наличные: {bal:,} ¢\n\n"
        "Собирайте ресурсы и продавайте их кнопкой ниже!"
    ).replace(",", " ")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍾 Собирать бутылки (КД 2с)", callback_data="work_bottles")],
        [InlineKeyboardButton(text="⚙ Собирать металл (КД 4с)", callback_data="work_metal")],
        [InlineKeyboardButton(text="💰 Продать ресурсы", callback_data="sell_resources")]
    ])
    
    try:
        await callback.message.edit_text(new_text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except:
        pass
    await callback.answer("⚙️ Вы нашли металлолом!")

@router.callback_query(F.data == "sell_resources")
async def sell_resources(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, bottles, metal FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    
    if not res:
        username = callback.from_user.username or callback.from_user.first_name
        cursor.execute("INSERT INTO users (user_id, username, balance, last_bank_calc) VALUES (?, ?, 10000, ?)", 
                       (user_id, username, time.time()))
        conn.commit()
        cursor.execute("SELECT balance, bottles, metal FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        
    bal, b, m = res
    if b == 0 and m == 0:
        await callback.answer("❌ У вас нет ресурсов для продажи!", show_alert=True)
        conn.close()
        return
    
    payout = (b * 1000) + (m * 5000)
    cursor.execute("UPDATE users SET balance = balance + ?, bottles = 0, metal = 0 WHERE user_id = ?", (payout, user_id))
    conn.commit()
    
    cursor.execute("SELECT balance, bottles, metal FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    conn.close()
    
    new_text = (
        f"👷 Центр сбора ресурсов:\n\n"
        f"🍾 Бутылок: {u[1]} шт.\n"
        f"⚙️ Металла: {u[2]} шт.\n"
        f"💰 Наличные: {u[0]:,} ¢\n\n"
        "Собирайте ресурсы и продавайте их кнопкой ниже!"
    ).replace(",", " ")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍾 Собирать бутылки (КД 2с)", callback_data="work_bottles")],
        [InlineKeyboardButton(text="⚙️ Собирать металл (КД 4с)", callback_data="work_metal")],
        [InlineKeyboardButton(text="💰 Продать ресурсы", callback_data="sell_resources")]
    ])
    
    try:
        await callback.message.edit_text(new_text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except:
        pass
        
    await callback.answer(f"💰 Продано!\n🍾 Бутылок: {b} | ⚙️ Металла: {m}\nПолучено: {payout:,} ¢".replace(",", " "), show_alert=True)


# --- НОВАЯ СИСТЕМА КОМПАНИЙ ПО ФЛИПИНГУ ---
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
            next_upgrade_text = "⭐ У вас максимальный 5 уровень компании!"
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
    
    # Плавное обновление интерфейса вместо отправки нового сообщения
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
    
    text, keyboard = get_company_data_and_keyword(user_id) if 'get_company_data_and_keyword' else get_company_data_and_keyboard(user_id)
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
        await callback.answer("⭐ У вас уже максимальный уровень компании!", show_alert=True)
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


# --- КАЗИНО И ИГРЫ ---
@router.message(F.text.casefold().in_(["🎰 казино", "казино"]))
async def text_casino(message: Message):
    if not await check_ban_and_register(message): return
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА/КОМАНДА", "Казино")
    text = (
        f"🎰 Игровой зал (Казино):\n\n"
        f"📜 Доступные игры и ставки (до 1кккк):\n\n"
        f"🎯 1. Рулетка:\n"
        f"• рул кра 10000 / рул кра 10к / рул кра вб\n"
        f"• рул чер 10000 / рул чер 10к\n"
        f"• рул 1-12 10000 / рул 13-24 10к / рул 25-36 10к\n"
        f"• рул чет 10к / рул нечет 10к / рул от 0-36 10000\n\n"
        f"🃏 2. Покер:\n"
        f"• покер 10000 или покер 10к (вб тоже работает)\n\n"
        f"🎡 3. Колесо Фортуны:\n"
        f"• фортуна 10000 или фортуна 10к\n\n"
        f"🎯 4. Дартс:\n"
        f"• дартс 100000 мимо (х5 при промахе)\n"
        f"• дартс 100000 центр (х4 при попадании в центр)\n\n"
        f"🏀 5. Баскетбол:\n"
        f"• баскет 10000 (х2 при попадании)"
    ).replace(",", " ")
    await smart_answer(message, text)

# --- РУЛЕТКА ---
@router.message(F.text.regexp(r"(?i)^рул\s+.+"))
async def casino_roulette(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    now = time.time()
    if now - game_cooldowns.get(user_id, 0) < 2:
        await message.answer("❌ Не так быстро!")
        return
    game_cooldowns[user_id] = now

    args = message.text.split()
    if len(args) < 3:
        await message.answer("❌ Неверный формат ставки. Пример: рул кра 10000", parse_mode="MARKDOWN")
        return

    target_str = " ".join(args[1:-1]).lower()
    raw_amount_str = args[-1].lower()
    log_user_action(user_id, message.from_user.username or message.from_user.first_name, "РУЛЕТКА", f"Ставка: {target_str} на сумму {raw_amount_str}")

    # Разрешенные варианты ставок (убран диапазон 0-36, добавлены 1-12, 13-24, 25-36)
    allowed_ranges = ["1-12", "13-24", "25-36"]
    valid_targets = ["кра", "красное", "чер", "черное", "чет", "нечет"] + allowed_ranges
    
    is_valid_target = False
    if target_str in valid_targets:
        is_valid_target = True
    else:
        try:
            chosen_num = int(target_str)
            if 0 <= chosen_num <= 36:
                is_valid_target = True
        except ValueError:
            pass
            
    if not is_valid_target:
        await message.answer("❌ Неправильная ставка! Доступно: 1-12, 13-24, 25-36, кра, чер, чет, нечет или число от 0 до 36.")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    bal_row = cursor.fetchone()
    bal = bal_row[0] if bal_row else 0

    if raw_amount_str in ["вб", "все", "all"]:
        amount = bal
    else:
        amount = parse_sum(raw_amount_str)

    if not amount or amount <= 0:
        await message.answer("❌ Неверная сумма ставки.")
        conn.close()
        return

    if bal < amount:
        await message.answer("❌ У вас недостаточно наличных.")
        conn.close()
        return

    rolled_num = random.randint(0, 36)
    if rolled_num == 0:
        rolled_color = "🟢"
    elif rolled_num in RED_NUMBERS:
        rolled_color = "🔴"
    else:
        rolled_color = "⚫"

    won = False
    payout = 0

    if target_str in ["кра", "красное"]:
        if rolled_num != 0 and rolled_num in RED_NUMBERS:
            won = True
            payout = amount * 2
    elif target_str in ["чер", "черное"]:
        if rolled_num != 0 and rolled_num not in RED_NUMBERS:
            won = True
            payout = amount * 2
    elif target_str in ["чет"]:
        if rolled_num != 0 and rolled_num % 2 == 0:
            won = True
            payout = amount * 2
    elif target_str in ["нечет"]:
        if rolled_num != 0 and rolled_num % 2 != 0:
            won = True
            payout = amount * 2
    elif target_str == "1-12":
        if 1 <= rolled_num <= 12:
            won = True
            payout = amount * 3
    elif target_str == "13-24":
        if 13 <= rolled_num <= 24:
            won = True
            payout = amount * 3
    elif target_str == "25-36":
        if 25 <= rolled_num <= 36:
            won = True
            payout = amount * 3
    else:
        try:
            chosen_num = int(target_str)
            if chosen_num == rolled_num:
                won = True
                payout = amount * 36
        except:
            pass

    if won:
        net_profit = int(payout) - amount
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net_profit, user_id))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        profit_str = f"+{net_profit:,}".replace(",", " ")
        balance_str = f"{new_bal:,}".replace(",", " ")
        text = f"Ты выиграл 🎉 Выпало {rolled_num} {rolled_color}\n\n{profit_str}¢\n\nВаш баланс: {balance_str}¢"
        await send_result_media(message, True, text)
    else:
        actual_loss = min(amount, bal)
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (actual_loss, user_id))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        loss_str = f"-{actual_loss:,}".replace(",", " ")
        balance_str = f"{new_bal:,}".replace(",", " ")
        text = f"Ты проиграл 🫠 Выпало {rolled_num} {rolled_color}\n\n{loss_str}¢\n\nВаш баланс: {balance_str}¢"
        await send_result_media(message, False, text)

# --- ПОКЕР ---
@router.message(F.text.regexp(r"(?i)^покер\s+.+"))
async def casino_poker_chat(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    now = time.time()
    if now - game_cooldowns.get(user_id, 0) < 2:
        await message.answer("❌ Не так быстро!")
        return
    game_cooldowns[user_id] = now

    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Формат: покер 10000 или покер 10к", parse_mode="MARKDOWN")
        return

    raw_amt = args[1].lower()
    log_user_action(user_id, message.from_user.username or message.from_user.first_name, "ПОКЕР", f"Ставка: {raw_amt}")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0

    if raw_amt in ["вб", "все", "all"]:
        amount = bal
    else:
        amount = parse_sum(raw_amt)

    if not amount or amount <= 0:
        await message.answer("❌ Неверная сумма ставки.")
        conn.close()
        return

    if bal < amount:
        await message.answer("❌ У вас недостаточно наличных.")
        conn.close()
        return

    hands = ["Старшая карта", "Пара", "Две пары", "Тройка", "Стрит", "Фулл-Хаус", "Каре", "Флеш-Рояль"]
    weights = [60, 30, 6, 2, 1, 0.7, 0.2, 0.1]

    player_hand = random.choices(hands, weights=weights)[0]
    dealer_hand = random.choices(hands, weights=weights)[0]
    hand_power = {h: i for i, h in enumerate(hands)}
    if hand_power[player_hand] > hand_power[dealer_hand]:
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = f"🎉 Победа в покере!\nВаша комбинация: {player_hand} (Дилер: {dealer_hand})\n💰 Вы выиграли: +{amount:,} ¢\n💰 Баланс: {new_bal:,} ¢".replace(",", " ")
        await send_result_media(message, True, text)
    else:
        actual_loss = min(amount, bal)
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (actual_loss, user_id))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = f"😢 Проигрыш в покере.\nВаша комбинация: {player_hand} (Дилер: {dealer_hand})\n💸 Потеряно: -{actual_loss:,} ¢\n💰 Баланс: {new_bal:,} ¢".replace(",", " ")
        await send_result_media(message, False, text)

# --- ФОРТУНА ---
@router.message(F.text.regexp(r"(?i)^фортуна\s+.+"))
async def casino_wheel_chat(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    now = time.time()
    if now - game_cooldowns.get(user_id, 0) < 2:
        await message.answer("❌ Не так быстро!")
        return
    game_cooldowns[user_id] = now

    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Формат: фортуна 10000 или фортуна 10к", parse_mode="MARKDOWN")
        return

    raw_amt = args[1].lower()
    log_user_action(user_id, message.from_user.username or message.from_user.first_name, "ФОРТУНА", f"Ставка: {raw_amt}")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0

    if raw_amt in ["вб", "все", "all"]:
        cost = bal
    else:
        cost = parse_sum(raw_amt)
    if not cost or cost <= 0:
        await message.answer("❌ Неверная сумма ставки.")
        conn.close()
        return

    if bal < cost:
        await message.answer("❌ У вас недостаточно наличных.")
        conn.close()
        return

    multipliers = [-1.0, -0.5, 0.5, 1.0, 2.0, 5.0, 10.0]
    weights = [25, 30, 15, 10, 5, 0.4, 0.1]
    mult = random.choices(multipliers, weights=weights)[0]
    net_change = int(cost * mult)

    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net_change, user_id))
    conn.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.close()

    if net_change > 0:
        text = f"🎡 Колесо Фортуны:\n✨ Удача! Вы выиграли +{net_change:,} ¢ (x{mult})\n💰 Баланс: {new_bal:,} ¢".replace(",", " ")
        await send_result_media(message, True, text)
    elif net_change < 0:
        text = f"🎡 Колесо Фортуны:\n💀 Неудача! Вы потеряли {abs(net_change):,} ¢\n💰 Баланс: {new_bal:,} ¢".replace(",", " ")
        await send_result_media(message, False, text)
    else:
        await message.answer(f"🎡 Колесо Фортуны:\n🤝 Ничья!\n💰 Баланс: {new_bal:,} ¢".replace(",", " "))

# --- ДАРТС ---
@router.message(F.text.regexp(r"(?i)^дартс\b"))
async def game_darts(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    now = time.time()
    if now - game_cooldowns.get(user_id, 0) < 2:
        await message.answer("❌ Не так быстро!")
        return
    game_cooldowns[user_id] = now

    args = message.text.split()
    if len(args) < 3:
        await message.answer("❌ Формат: дартс 100000 центр / дартс 100000 мимо", parse_mode="MARKDOWN")
        return

    text_lower = message.text.lower()
    if "центр" in text_lower:
        mode = "центр"
    elif "мимо" in text_lower:
        mode = "мимо"
    else:
        await message.answer("❌ Укажите режим: «центр» или «мимо». Пример: дартс 10к центр")
        return

    log_user_action(user_id, message.from_user.username or message.from_user.first_name, "ДАРТС", f"Режим: {mode}, текст: {message.text}")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0

    amount_str = args[1].lower()
    if amount_str in ["центр", "мимо"] and len(args) > 2:
        amount_str = args[2].lower()
    elif len(args) > 2 and args[2].lower() in ["центр", "мимо"]:
        amount_str = args[1].lower()

    if amount_str in ["вб", "все", "all"]:
        amount = bal
    else:
        amount = parse_sum(amount_str)

    if not amount or amount <= 0:
        await message.answer("❌ Неправильная сумма ставки.")
        conn.close()
        return

    if bal < amount:
        await message.answer("❌ Недостаточно наличных.")
        conn.close()
        return

    msg_dice = await message.answer_dice(emoji="🎯")
    dice_val = msg_dice.dice.value
    await asyncio.sleep(3)

    is_hit_center = dice_val == 6
    is_absolute_miss = dice_val == 1

    won = False
    payout = 0

    if mode == "центр" and is_hit_center:
        won = True
        payout = amount * 4
    elif mode == "мимо" and is_absolute_miss:
        won = True
        payout = amount * 5

    if won:
        net_profit = payout - amount
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net_profit, user_id))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = f"🎯 Дартс: Успех!\nВыпало: {dice_val}.\nВыигрыш: +{net_profit:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " ")
        await message.answer(text, parse_mode="MARKDOWN")
    else:
        actual_loss = min(amount, bal)
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (actual_loss, user_id))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = f"🎯 Дартс: Мимо кассы!\nВыпало: {dice_val}.\nПроигрыш: -{actual_loss:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " ")
        await message.answer(text, parse_mode="MARKDOWN")

# --- БАСКЕТБОЛ ---
@router.message(F.text.regexp(r"(?i)^баскет\b"))
async def game_basketball(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    now = time.time()
    if now - game_cooldowns.get(user_id, 0) < 2:
        await message.answer("❌ Не так быстро!")
        return
    game_cooldowns[user_id] = now

    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Формат ставки: баскет 10000 или баскет 10к", parse_mode="MARKDOWN")
        return

    if "мимо" in message.text.lower():
        await message.answer("❌ Ставки на промах в баскетболе больше недоступны! Пример правильной ставки: баскет 10к")
        return

    if len(args) > 2:
        await message.answer("❌ Неправильный формат ставки. Пример: баскет 10000")
        return
    amount_str = args[1].lower()
    log_user_action(user_id, message.from_user.username or message.from_user.first_name, "БАСКЕТБОЛ", f"Ставка: {amount_str}")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0

    if amount_str in ["вб", "все", "all"]:
        amount = bal
    else:
        amount = parse_sum(amount_str)

    if not amount or amount <= 0:
        await message.answer("❌ Неправильная сумма ставки.")
        conn.close()
        return

    if bal < amount:
        await message.answer("❌ Недостаточно наличных.")
        conn.close()
        return

    msg_dice = await message.answer_dice(emoji="🏀")
    dice_val = msg_dice.dice.value
    await asyncio.sleep(3)

    is_scored = dice_val in [4, 5]
    won = is_scored
    payout = amount * 2 if won else 0
    if won:
        net_profit = payout - amount
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net_profit, user_id))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = f"🏀 Баскетбол: Гол!\nМяч в кольце (кубик: {dice_val}).\nВыигрыш: +{net_profit:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " ")
        await message.answer(text, parse_mode="MARKDOWN")
    else:
        actual_loss = min(amount, bal)
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (actual_loss, user_id))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = f"🏀 Баскетбол: Мимо!\nМяч не попал (кубик: {dice_val}).\nПотеряно: -{actual_loss:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " ")
        await message.answer(text, parse_mode="MARKDOWN")


# --- БАНК И ПЕРЕВОДЫ ---
@router.message(F.text.casefold().in_(["🏦 банк", "баланс"]))
async def text_bank(message: Message):
    if not await check_ban_and_register(message): return
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА/КОМАНДА", "Банк")
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, bank_balance FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    conn.close()
    
    balance = u[0] if u else 10000
    bank_balance = u[1] if u else 0

    text = (
        f"🏦 Центральный Банк:\n\n"
        f"💰 Наличные: {balance:,} ¢\n"
        f"🏛 На счете в банке: {bank_balance:,} ¢\n"
        f"📈 Доходность: 1% каждые 2 часа.\n\n"
        "Команды в чат:\n"
        "• вложить сумма\n"
        "• снять сумма".replace(",", " ")
    )
    await smart_answer(message, text)

@router.message(F.text.regexp(r"(?i)^(вложить|вл)\s+.+"))
async def bank_deposit(message: Message):
    if not await check_ban_and_register(message): return
    amount = parse_sum(message.text.split()[1])
    if not amount or amount <= 0: return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, last_bank_calc FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    if u[0] < amount:
        await message.answer("❌ Недостаточно наличных средств.")
        conn.close()
        return
    cursor.execute("UPDATE users SET balance = balance - ?, bank_balance = bank_balance + ?, last_bank_calc = ? WHERE user_id = ?", 
                   (amount, amount, time.time() if u[1] is None else u[1], user_id))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Успешно вложено в банк: {amount:,} ¢".replace(",", " "), parse_mode="MARKDOWN")

@router.message(F.text.regexp(r"(?i)^снять\s+.+"))
async def bank_withdraw(message: Message):
    if not await check_ban_and_register(message): return
    amount = parse_sum(message.text.split()[1])
    if not amount or amount <= 0: return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT bank_balance FROM users WHERE user_id = ?", (user_id,))
    bank_bal = cursor.fetchone()[0]
    if bank_bal < amount:
        await message.answer("❌ На банковском счете нет столько денег.")
        conn.close()
        return
    cursor.execute("UPDATE users SET bank_balance = bank_balance - ?, balance = balance + ? WHERE user_id = ?", (amount, amount, user_id))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Успешно снято со счета: {amount:,} ¢".replace(",", " "), parse_mode="MARKDOWN")

@router.message(F.text.regexp(r"(?i)^пер\s+.+"))
async def transfer_money(message: Message):
    if not await check_ban_and_register(message): return
    args = message.text.split()
    sender_id = message.from_user.id
    target_id = None
    target_display_name = ""
    
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
        target_display_name = message.reply_to_message.from_user.first_name
        if len(args) < 2:
            await message.answer("❌ Формат: Пер [сумма] (в ответ на сообщение)")
            return
        amount = parse_sum(args[1])
    else:
        if len(args) < 3:
            await message.answer("❌ Формат: Пер @юзер сумма")
            return
        target_username = args[1].replace("@", "").lower()
        amount = parse_sum(args[2])
        target_display_name = f"@{target_username}"

    if not amount or amount <= 0:
        await message.answer("❌ Неверная сумма перевода.")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (sender_id,))
    sender_bal_res = cursor.fetchone()
    sender_bal = sender_bal_res[0] if sender_bal_res else 0
    
    if sender_bal < amount:
        await message.answer("❌ У вас недостаточно наличных средств для перевода.")
        conn.close()
        return

    if not message.reply_to_message:
        cursor.execute("SELECT user_id, custom_name, username FROM users WHERE LOWER(username) = ?", (target_username,))
        target_row = cursor.fetchone()
        if not target_row:
            await message.answer("❌ Пользователь с таким юзернеймом не найден.")
            conn.close()
            return
        target_id = target_row[0]
        target_display_name = target_row[1] if target_row[1] else (target_row[2] if target_row[2] else target_username)

    if target_id == sender_id:
        await message.answer("❌ Нельзя переводить деньги самому себе.")
        conn.close()
        return

    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, sender_id))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
    conn.commit()
    conn.close()

    formatted_amount = f"{amount:,}".replace(",", " ")
    sender_name = message.from_user.username or message.from_user.first_name
    log_user_action(sender_id, sender_name, "ПЕРЕВОД", f"Перевел {formatted_amount}¢ пользователю ID: {target_id}")

    if amount >= 500_000_000:
        alert_text = f"🚨 КРУПНЫЙ ПЕРЕВОД!\n• От: @{sender_name} (ID: {sender_id})\n• Кому ID: {target_id}\n• Сумма: {formatted_amount} ¢"
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, alert_text, parse_mode="MARKDOWN")
            except:
                pass

    await message.answer(f"✅ Успешно переведено {formatted_amount} ¢ пользователю {target_display_name}.")


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
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА/КОМАНДА", "Бонус")
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    last_bonus = row[0] if row and row[0] is not None else 0.0
    conn.close()

    now = time.time()
    cooldown = 24 * 3600

    if now - last_bonus < cooldown:
        time_left = int(cooldown - (now - last_bonus))
        hours = time_left // 3600
        minutes = (time_left % 3600) // 60
        await message.answer(f"⏳ Бонус уже получен! Следующий будет доступен через {hours} ч. {minutes} мин.")
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + 50000, last_bonus = ? WHERE user_id = ?", (now, user_id))
    conn.commit()
    conn.close()
    await message.answer("🎉 Вы успешно забрали ежедневный бонус: +50 000 ¢!")
    
@router.message(F.text.casefold().in_(["👥 рефералы", "реф", "рефералы", "реферал"]))
async def text_referral(message: Message):
    if not await check_ban_and_register(message): return
    log_user_action(message.from_user.id, message.from_user.username or message.from_user.first_name, "КНОПКА/КОМАНДА", "Рефералы")
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    ref_count = res[0] if res else 0
    ref_earned = ref_count * 100000
    ref_link = f"https://t.me/Flippincv_bot?start=ref_{user_id}"
    text = (
        f"👥 Рефералы\n\n"
        f"твоя ссылка:\n\n"
        f"{ref_link}\n\n"
        f"за каждого нового игрока:\n"
        f"💰 100 000 ¢\n\n"
        f"Приглашено: {ref_count}\n"
        f"Заработано: {ref_earned:,} ¢".replace(",", " ")
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Поделиться", url=f"https://t.me/share/url?url={ref_link}&text=Заходи в крутого бота по флипингу квартир и бизнесу!")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="MARKDOWN")


# --- МЕНЮ КРЕАТОРОВ И ПРОМОКОДЫ ---
@router.message(F.text.casefold().in_(["🎬 креатор"]))
async def text_creator_command(message: Message):
    user_id = message.from_user.id
    if not await is_creator(user_id):
        return
    log_user_action(user_id, message.from_user.username or message.from_user.first_name, "КОМАНДА", "Креатор")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Забрать ЗП (7.5 млн)", callback_data="creator_claim_salary")],
        [InlineKeyboardButton(text="🏷 Создать промокод", callback_data="creator_promo_info")]
    ])
    
    await message.answer(
        "🎬 Меню креатора:\n\n"
        "• Зарплата: 7 500 000 ¢ (доступно 1 раз в 24 часа)\n"
        "• Промокоды: Создание промокодов от 1 млн до 10 млн ¢ (напишите в чат: промо [код] [сумма] [активации])",
        reply_markup=keyboard,
        parse_mode="MARKDOWN"
    )

@router.callback_query(F.data == "creator_claim_salary")
async def callback_creator_salary(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not await is_creator(user_id):
        await callback.answer("❌ У вас нет прав креатора!", show_alert=True)
        return

    now = time.time()
    last = creator_salary_cooldowns.get(user_id, 0.0)
    cooldown = 24 * 3600

    if now - last < cooldown:
        time_left = int(cooldown - (now - last))
        hours = time_left // 3600
        minutes = (time_left % 3600) // 60
        await callback.answer(f"⏳ ЗП можно получать 1 раз в 24 часа. Ждите еще {hours} ч. {minutes} мин.", show_alert=True)
        return

    creator_salary_cooldowns[user_id] = now
    salary_amount = 7_500_000

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (salary_amount, user_id))
    conn.commit()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.close()

    await callback.answer(f"💼 Зарплата получена: +7 500 000 ¢!", show_alert=True)
    try:
        await callback.message.answer(f"💼 Вы успешно получили зарплату креатора: +7 500 000 ¢!\n💰 Баланс: {new_bal:,} ¢".replace(",", " "), parse_mode="MARKDOWN")
    except:
        pass

@router.callback_query(F.data == "creator_promo_info")
async def callback_creator_promo_info(callback: CallbackQuery):
    await callback.answer("💡 Чтобы создать промокод, напишите в чат:\nпромо [код] [сумма] [активации]\n(Сумма от 1кк до 10кк)", show_alert=True)

# --- СОЗДАНИЕ И АКТИВАЦИЯ ПРОМОКОДОВ ---
@router.message(F.text.regexp(r"(?i)^промо\s+\w+\s+.+\s+\d+$"))
async def create_promo_universal(message: Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    creatr = await is_creator(user_id)

    if not is_admin and not creatr:
        return

    args = message.text.split()
    if len(args) < 4:
        await message.answer("❌ Формат: промо [код] [сумма] [активации]")
        return

    code = args[1].upper()
    reward = parse_sum(args[2])
    try:
        activations = int(args[3])
    except:
        await message.answer("❌ Неверное количество активаций.")
        return

    if not reward or reward <= 0:
        await message.answer("❌ Неверная сумма награды.")
        return
    if not is_admin and creatr:
        if not (1_000_000 <= reward <= 10_000_000):
          await message.answer("❌ Креаторы могут создавать промокоды с наградой от 1 000 000 до 10 000 000 ¢.")
          return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM promo_codes WHERE code = ?", (code,))
    if cursor.fetchone():
        await message.answer(f"❌ Промокод {code} уже существует!")
        conn.close()
        return

    cursor.execute("INSERT INTO promo_codes (code, reward, activations_left) VALUES (?, ?, ?)", (code, reward, activations))
    conn.commit()
    conn.close()

    await message.answer(f"✅ Промокод {code} успешно создан!\n💰 Награда: {reward:,} ¢\n👥 Активаций: {activations}".replace(",", " "), parse_mode="MARKDOWN")

@router.message(F.text.regexp(r"(?i)^промокод\s+\w+$"))
async def activate_promo_code(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) < 2:
        return
    code = args[1].upper()

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT reward, activations_left FROM promo_codes WHERE code = ?", (code,))
    promo = cursor.fetchone()

    if not promo:
        await message.answer("❌ Такой промокод не найден или срок его действия истек.")
        conn.close()
        return

    reward, activations_left = promo

    if activations_left <= 0:
        await message.answer("❌ У этого промокода закончились активации.")
        conn.close()
        return

    cursor.execute("SELECT * FROM user_promo WHERE user_id = ? AND code = ?", (user_id, code))
    if cursor.fetchone():
        await message.answer("❌ Вы уже активировали этот промокод!")
        conn.close()
        return

    cursor.execute("INSERT INTO user_promo (user_id, code) VALUES (?, ?)", (user_id, code))
    cursor.execute("UPDATE promo_codes SET activations_left = activations_left - 1 WHERE code = ?", (code,))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
    conn.commit()

    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.close()

    await message.answer(f"🎉 Промокод {code} успешно активирован!\n💰 Получено: +{reward:,} ¢\n💼 Баланс: {new_bal:,} ¢".replace(",", " "), parse_mode="MARKDOWN")
    
# ================= ОБРАБОТЧИКИ КОМАНД =================

# Команда для вызова админ-панели (можно написать "админ" или "/admin")
@router.message(F.text.casefold().in_(["админ", "/admin"]))
async def cmd_admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return  # Игнорируем обычных пользователей
    
    await message.answer(
        "🛠 Панель администратора:\n\n"
        "Выберите нужное действие с помощью кнопок ниже или используйте команды:\n"
        "• выдавать креатора @юз (или ID)\n"
        "• забрать креатора @юз (или ID)\n"
        "• обнулить @юз (или ID)\n"
        "• бан @юз (или ID)\n"
        "• разбан @юз (или ID)",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )


# Кнопка: Статистика
@router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("У вас нет прав!", show_alert=True)
    
    total_users = 150  # Пример значения
    
    await callback.message.edit_text(
        f"📊 Статистика бота:\n\n"
        f"👥 Всего пользователей в базе: {total_users}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]]
        ),
        parse_mode="Markdown"
    )
    await callback.answer()


# Кнопка: Назад в главное меню админки
@router.callback_query(F.data == "admin_back")
async def admin_back_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    await callback.message.edit_text(
        "🛠 Панель администратора:\n\n"
        "Выберите нужное действие с помощью кнопок ниже:",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()


# ================= СИСТЕМА РАССЫЛКИ =================
@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("У вас нет прав!", show_alert=True)
    
    await callback.message.answer(
        "📢 Введите текст для рассылки всем пользователям бота:\n"
        "(Отмените действие, написав /cancel)"
    )
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.answer()


@router.message(AdminStates.waiting_for_broadcast)
async def admin_broadcast_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    if message.text.casefold() == "/cancel":
        await state.clear()
        await message.answer("❌ Рассылка отменена.")
        return

    text_to_send = message.text
    await state.clear()
    
    await message.answer("✅ Рассылка успешно завершена! (Пример)")


# Функция для перевода сокращений (1к, 1кк, 1ккк...) в числа
def parse_amount(text: str) -> int:
    text = text.lower().strip()
    multiplier = 1
    
    if text.endswith('ккккк'):
        multiplier = 1_000_000_000_000_000
        text = text[:-5]
    elif text.endswith('кккк'):
        multiplier = 1_000_000_000_000
        text = text[:-4]
    elif text.endswith('ккк'):
        multiplier = 1_000_000_000
        text = text[:-3]
    elif text.endswith('кк'):
        multiplier = 1_000_000
        text = text[:-2]
    elif text.endswith('к'):
        multiplier = 1_000
        text = text[:-1]
        
    return int(float(text) * multiplier)


# Кнопка: Изменить баланс
@router.callback_query(F.data == "admin_balance")
async def admin_balance_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("У вас нет прав!", show_alert=True)
    await callback.message.answer(
        "💰 Введите цель и сумму (например: @username 5кк или 123456789 1ккк):\n\n"
        "Поддерживаемые суффиксы:\n"
        "• к = 1 000\n"
        "• кк = 1 000 000\n"
        "• ккк = 1 000 000 000\n"
        "• кккк = 1 000 000 000 000\n"
        "• ккккк = 1 000 000 000 000 000\n\n"
        "(Для отмены напишите /cancel)"
    )
    await state.set_state(AdminStates.waiting_for_balance_change)
    await callback.answer()


@router.message(AdminStates.waiting_for_balance_change)
async def admin_balance_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    if message.text.casefold() == "/cancel":
        await state.clear()
        await message.answer("❌ Действие отменено.")
        return

    try:
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("❌ Ошибка! Неверный формат. Пример: @username 5кк")
            return
            
        target_raw = parts[0].strip()
        amount_raw = parts[1].strip()
        
        amount = parse_amount(amount_raw)
        
        import sqlite3
        conn = sqlite3.connect("game.db")
        cursor = conn.cursor()
        
        target_user_id = None
        if target_raw.startswith("@"):
            username = target_raw[1:]
            cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
            res = cursor.fetchone()
            if res:
                target_user_id = res[0]
        else:
            try:
                target_user_id = int(target_raw)
            except ValueError:
                pass
            
        if not target_user_id:
            conn.close()
            await message.answer(f"❌ Пользователь {target_raw} не найден в базе данных!")
            return
            
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_user_id))
        conn.commit()
        conn.close()
        
        await state.clear()
        await message.answer(f"✅ Баланс пользователя {target_raw} успешно изменен на {amount_raw} ({amount:,} монет)!")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка обработки: пропишите в формате @юз сумма (например: @durov 1кк)")


# Вспомогательная функция поиска user_id по ID или @username
def find_user_id(target_raw: str):
    import sqlite3
    conn = sqlite3.connect("game.db")
    cursor = conn.cursor()
    
    user_id = None
    target_raw = target_raw.strip()
    
    if target_raw.startswith("@"):
        username = target_raw[1:]
        cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
        res = cursor.fetchone()
        if res:
            user_id = res[0]
    else:
        try:
            user_id = int(target_raw)
        except ValueError:
            pass
            
    conn.close()
    return user_id


# Команда бана: бан @юз или бан ID
@router.message(F.text.lower().startswith("бан "))
async def admin_ban_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Укажите пользователя! Пример: бан @username или бан 123456789")
        return
        
    target_raw = parts[1].strip()
    target_user_id = find_user_id(target_raw)
    
    if not target_user_id:
        await message.answer(f"❌ Пользователь {target_raw} не найден в базе данных!")
        return
        
    import sqlite3
    conn = sqlite3.connect("game.db")
    cursor = conn.cursor()
    
    cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_user_id,))
    conn.commit()
    conn.close()
    
    await message.answer(f"🚫 Пользователь {target_raw} (ID: {target_user_id}) успешно забанен!")
# Команда разбана: разбан @юз или разбан ID
@router.message(F.text.lower().startswith("разбан "))
async def admin_unban_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Укажите пользователя! Пример: разбан @username или разбан 123456789")
        return
        
    target_raw = parts[1].strip()
    target_user_id = find_user_id(target_raw)
    
    if not target_user_id:
        await message.answer(f"❌ Пользователь {target_raw} не найден в базе данных!")
        return
        
    import sqlite3
    conn = sqlite3.connect("game.db")
    cursor = conn.cursor()
    
    cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_user_id,))
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Пользователь {target_raw} (ID: {target_user_id}) успешно разбанен!")


# ================= НОВЫЕ КОМАНДЫ (КРИАТОРЫ И ОБНУЛЕНИЕ) =================

# Выдать креатора: выдавать креатора @юз (или ID)
@router.message(F.text.casefold().regexp(r"^выдавать\s+креатора\s+"))
async def admin_give_creator(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("❌ Неверный формат! Пример: выдавать креатора @username или выдавать креатора 123456789")
        return
        
    target_raw = parts[2].strip()
    target_user_id = find_user_id(target_raw)
    
    if not target_user_id:
        await message.answer(f"❌ Пользователь {target_raw} не найден в базе данных!")
        return
        
    import sqlite3
    conn = sqlite3.connect("game.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_creator = 1 WHERE user_id = ?", (target_user_id,))
    conn.commit()
    conn.close()
    
    await message.answer(f"🎬 Пользователь {target_raw} (ID: {target_user_id}) успешно назначен креатором!")


# Забрать креатора: забрать креатора @юз (или ID)
@router.message(F.text.casefold().regexp(r"^забрать\s+креатора\s+"))
async def admin_take_creator(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("❌ Неверный формат! Пример: забрать креатора @username или забрать креатора 123456789")
        return
        
    target_raw = parts[2].strip()
    target_user_id = find_user_id(target_raw)
    
    if not target_user_id:
        await message.answer(f"❌ Пользователь {target_raw} не найден в базе данных!")
        return
        
    import sqlite3
    conn = sqlite3.connect("game.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_creator = 0 WHERE user_id = ?", (target_user_id,))
    conn.commit()
    conn.close()
    
    await message.answer(f"❌ У пользователя {target_raw} (ID: {target_user_id}) забран статус креатора.")


# Обнулить игрока: обнулить @юз (или ID)
@router.message(F.text.casefold().startswith("обнулить "))
async def admin_reset_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Укажите пользователя! Пример: обнулить @username или обнулить 123456789")
        return
        
    target_raw = parts[1].strip()
    target_user_id = find_user_id(target_raw)
    
    if not target_user_id:
        await message.answer(f"❌ Пользователь {target_raw} не найден в базе данных!")
        return
        
    import sqlite3
    conn = sqlite3.connect("game.db")
    cursor = conn.cursor()
    
    # Сбрасываем основные параметры игрока до стартовых значений
    # (можешь скорректировать столбцы под структуру своей базы данных)
    cursor.execute(
        "UPDATE users SET balance = 10000, bank_balance = 0, invested = 0, bottles = 0, metal = 0, ref_count = 0 WHERE user_id = ?", 
        (target_user_id,)
    )
    # Если у тебя есть таблица с недвижимостью/квартирами пользователя, удаляем её тоже
    try:
        cursor.execute("DELETE FROM user_apartments WHERE user_id = ?", (target_user_id,))
    except sqlite3.OperationalError:
        pass  # Если таблица называется иначе или её нет, просто пропускаем
        
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Игрок {target_raw} (ID: {target_user_id}) полностью обнулен.")


# Обновленная кнопка «Забанить / Разбанить» в меню админки
@router.callback_query(F.data == "admin_ban_menu")
async def admin_ban_menu_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return await callback.answer("У вас нет прав!", show_alert=True)
    
    await callback.message.edit_text(
        "🚫 Управление блокировками и правами:\n\n"
        "Вы можете использовать следующие текстовые команды прямо в чате:\n\n"
        "• бан @username (или ID)\n"
        "• разбан @username (или ID)\n"
        "• выдавать креатора @username (или ID)\n"
        "• забрать креатора @username (или ID)\n"
        "• обнулить @username (или ID)",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]]
        ),
        parse_mode="Markdown"
    )
    await callback.answer()
# Состояния для FSM (машин состояний), например, для рассылки или выдачи баланса
class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_user_id_to_ban = State()
    waiting_for_balance_change = State()
    waiting_for_creator_action = State()  # Если понадобится для FSM
    waiting_for_reset = State()          # Если понадобится для FSM


# КНОПКИ АДМИНКИ
def get_admin_keyboard():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="💰 Изменить баланс", callback_data="admin_balance")],
            [InlineKeyboardButton(text="🚫 Забанить / Разбанить", callback_data="admin_ban_menu")],
            [InlineKeyboardButton(text="🎬 Управление креаторами", callback_data="admin_creator_menu")],
            [InlineKeyboardButton(text="🔄 Обнулить игрока", callback_data="admin_reset_menu")]
        ]
    )
    return keyboard

    
async def handle(request):
    return web.Response(text="Бот работает 24/7! 🚀")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Веб-сервер запущен на порту {port}")

async def main():
    init_db()
    await web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

import random
import time
import os
import sqlite3
import asyncio
from aiogram import F, Bot, Dispatcher, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, FSInputFile
from aiogram.filters import Command, CommandObject
from aiohttp import web

API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения Render")

bot = Bot(token=API_TOKEN)
router = Router()
dp = Dispatcher()
dp.include_router(router)

# --- ВСТАВЛЯЕМ СЮДА ---
original_send_message = bot.send_message
original_send_photo = bot.send_photo

STOP_WORDS = [
    "special premium offers",
    "premium videos",
    "fresihbot_bot",
    "video club",
    "strawberries",
    "referral reward levels",
    "age confirmation",
    "terms of use",
    "100 stars =",
    "250 stars =",
    "500 stars =",
    "1000 stars ="
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
BIG_TRANSFER_THRESHOLD = 10_000_000

# --- СПИСОК АДМИНИСТРАТОРОВ ---
ADMIN_IDS = [1222239198, 8390540110]  # Укажи свои Telegram ID через запятую

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

# --- КНОПКИ НИЖНЕГО МЕНЮ (Только для ЛС) ---
def get_private_keyboard(is_creator_flag=False):
    kb = [
        [KeyboardButton(text="Казино"), KeyboardButton(text="Работа")],
        [KeyboardButton(text="Флипинг"), KeyboardButton(text="Банк")],
        [KeyboardButton(text="Профиль"), KeyboardButton(text="Топ")],
        [KeyboardButton(text="Квартиры"), KeyboardButton(text="Бонус")],
        [KeyboardButton(text="Рефералы"), KeyboardButton(text="Помощь")]
    ]
    if is_creator_flag:
        kb.append([KeyboardButton(text="Креатор")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- БАЗА ДАННЫХ ---
def get_db():
    conn = sqlite3.connect("game.db", timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
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
            stolen INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            is_creator INTEGER DEFAULT 0,
            last_bank_calc REAL,
            last_bonus REAL DEFAULT 0,
            last_salary REAL DEFAULT 0,
            referrer_id INTEGER DEFAULT 0,
            ref_count INTEGER DEFAULT 0
        )
    """)

    # Миграция старой БД: добавляем stolen, если колонка отсутствует.
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN stolen INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS action_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            created_at REAL NOT NULL
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
    conn.commit()
    conn.close()


def log_action(user_id: int, action: str):
    """Записывает последние действия игрока для админ-панели."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO action_logs (user_id, action, created_at) VALUES (?, ?, ?)",
            (user_id, action[:250], time.time())
        )
        # Храним только последние 50 действий каждого игрока.
        conn.execute("""
            DELETE FROM action_logs
            WHERE user_id = ? AND id NOT IN (
                SELECT id FROM action_logs WHERE user_id = ?
                ORDER BY id DESC LIMIT 50
            )
        """, (user_id, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"LOG ACTION ERROR: {e}")


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
        
        ref_bonus = 1_000_000
        if ref_id != 0:
            cursor.execute("UPDATE users SET balance = balance + ?, ref_count = ref_count + 1 WHERE user_id = ?", (ref_bonus, ref_id))

        conn.commit()
        conn.close()

        if ref_id != 0:
            log_action(ref_id, f"Приглашён новый игрок: +{ref_bonus:,} ¢ за реферала".replace(",", " "))
            try:
                await bot.send_message(
                    ref_id,
                    "🎉 По вашей реферальной ссылке зарегистрировался новый игрок! Вам начислено 1 000 000 ¢.",
                    parse_mode="MARKDOWN"
                )
            except:
                pass
        return True
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

# --- ПАРСЕР СУММ С МАКСИМАЛЬНЫМИ СТАВКАМИ ДО 1КККК ---
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
    text = "🏠 Главное меню игры:\nДобро пожаловать! Используйте кнопки меню или команды."
    await smart_answer(message, text)

@router.message(F.text.casefold() == "главное меню")
async def text_main_menu(message: Message):
    if not await check_ban_and_register(message): return
    await smart_answer(message, "🏠 Главное меню игры:")

@router.message(F.text.casefold() == "помощь")
async def text_help(message: Message):
    if not await check_ban_and_register(message): return
    text = (
        "📖 Справка по боту:\n\n"
        "• Профиль: Напишите я или нажмите кнопку.\n"
        "• Топ: Напишите топ.\n"
        "• Как играть в казино: Ставки через слово рул (например: рул кра 10000, рул чер 10к, рул 1-12 10к, рул чет 10к, рул нечет 10к, рул от 0-36 10000 или вб).\n"
        "• Покер: Напишите покер 10000 или покер 10к.\n"
        "• Фортуна: Напишите фортуна 10000 или фортуна 10к.\n"
        "• Дартс: Напишите дартс 100000 мимо или дартс 100000 центр.\n"
        "• Баскетбол: Напишите баскет 100000 или баскет 100000 мимо.\n"
        "• Промокоды: Как вводить — код сам_промокод (или активировать код).\n"
        "• Банк: вложить [сумма] и снять [сумма]."
    )
    await smart_answer(message, text)
    
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
    log_action(user_id, f"Изменил игровой ник на: {new_name}")
    await message.answer(f"✅ Ваш игровой ник успешно изменен на: {new_name}", parse_mode="MARKDOWN")

# Текст помощи, который вы указали
HELP_TEXT = (
    "Играть в игры нажми на кнопку \"казик\" в боте.\n"
    "Переводы пример: Пер @юз сумма\n"
    "Также по кнопке флипинг покупай квартиры в боте и продавай мб уйдешь в окуп:).\n"
    "Закидуй в банк деньги чтоб получать процент:).\n"
    "Нажми на кнопку рефералы и нажми на кнопку поделиться за 1 человека +1 000 000¢.💸\n"
    "Получай бонус по кнопке в бота каждые 24 часа.💸\n"
    "Работай собирай бутылки и металл и меняй на деньги💰.\n"
    "Пиши \"я\" \"профиль\" чтобы смотреть свой профиль, пиши \"чекнуть\" под сообщением человека, чтобы смотреть его профиль.👀\n"
    "Пиши \"топ\", чтобы знать лучший ли ты🏅.\n"
    "Смотри свои квартиры по кнопке в боте"
)

# 1. Обработка текстового сообщения или кнопки с текстом "помощь" (регистр не важен)
@dp.message(F.text.lower() == "помощь")
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, parse_mode="MARKDOWN")

# 2. Обработка нажатия на инлайн-кнопку (если кнопка сделана через callback_data="help")
@dp.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    await callback.message.answer(HELP_TEXT, parse_mode="MARKDOWN")
    await callback.answer() # Закрываем часики анимации на кнопке    

# --- ПРОФИЛЬ И ТОП (АДМИНЫ СКРЫТЫ) ---
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

@router.message(F.text.casefold().in_(["профиль", "я"]))
async def msg_profile(message: Message):
    if not await check_ban_and_register(message): return
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

@router.message(F.text.casefold().in_((("топ", "топ игроков"))))
async def text_top(message: Message):
    if not await check_ban_and_register(message): return
    conn = get_db()
    cursor = conn.cursor()
    
    # Исключаем админов из топа
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

    log_action(message.from_user.id, "Открыл топ игроков")
    await smart_answer(message, text)


# --- РАБОТА И СБОР РЕСУРСОВ ---
@router.message(F.text.casefold().in_(["работа", "ферма", "болото"]))
async def text_work(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    conn.close()
    
    balance = u[0] if u else 10000
    bottles = u[1] if u else 0
    metal = u[2] if u else 0
    stolen = u[3] if u else 0

    text = (
        f"👷 Центр сбора ресурсов:\n\n"
        f"🍾 Бутылок: {bottles} шт.\n"
        f"⚙️ Металла: {metal} шт.\n"
        f"🥷 Украдено: {stolen} шт.\n"
        f"💰 Наличные: {balance:,} ¢\n\n"
        "Собирайте ресурсы и продавайте их кнопкой ниже!"
    ).replace(",", " ")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍾 Собирать бутылки (КД 2с)", callback_data="work_bottles")],
        [InlineKeyboardButton(text="⚙ Собирать металл (КД 4с)", callback_data="work_metal")],
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
    conn.commit()
    conn.close()
    log_action(user_id, "Работа: собрал 1 бутылку")
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
    conn.close()
    log_action(user_id, "Работа: собрал 1 металл")
    await callback.answer("⚙️ Вы нашли металлолом!")

@router.callback_query(F.data == "work_steal")
async def work_steal(callback: CallbackQuery):
    user_id = callback.from_user.id
    now = time.time()
    last = work_cooldowns.get(f"s_{user_id}", 0)
    if now - last < 6:
        await callback.answer(f"⏳ Подождите еще {round(6 - (now - last), 1)} сек.!", show_alert=True)
        return
    work_cooldowns[f"s_{user_id}"] = now

    conn = get_db()
    conn.execute("UPDATE users SET stolen = stolen + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    log_action(user_id, "Работа: украл 1 шт. (+10 000 ¢ стоимости)")
    await callback.answer("🥷 Вы украли 1 ценную вещь! КД 6 сек.")


@router.callback_query(F.data == "sell_resources")
async def sell_resources(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    
    if not res:
        username = callback.from_user.username or callback.from_user.first_name
        cursor.execute("INSERT INTO users (user_id, username, balance, last_bank_calc) VALUES (?, ?, 10000, ?)", 
                       (user_id, username, time.time()))
        conn.commit()
        cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        
    bal, b, m, stolen = res
    if b == 0 and m == 0 and stolen == 0:
        await callback.answer("❌ У вас нет ресурсов для продажи!", show_alert=True)
        conn.close()
        return
    
    payout = (b * 1000) + (m * 5000) + (stolen * 10000)
    cursor.execute("UPDATE users SET balance = balance + ?, bottles = 0, metal = 0, stolen = 0 WHERE user_id = ?", (payout, user_id))
    conn.commit()
    
    cursor.execute("SELECT balance, bottles, metal, stolen FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    conn.close()
    
    new_text = (
        f"👷 Центр сбора ресурсов:\n\n"
        f"🍾 Бутылок: {u[1]} шт.\n"
        f"⚙️ Металла: {u[2]} шт.\n"
        f"🥷 Украдено: {u[3]} шт.\n"
        f"💰 Наличные: {u[0]:,} ¢\n\n"
        "Собирайте ресурсы и продавайте их кнопкой ниже!"
    ).replace(",", " ")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍾 Собирать бутылки (КД 2с)", callback_data="work_bottles")],
        [InlineKeyboardButton(text="⚙️ Собирать металл (КД 4с)", callback_data="work_metal")],
        [InlineKeyboardButton(text="🥷 Воровать (КД 6с)", callback_data="work_steal")],
        [InlineKeyboardButton(text="💰 Продать ресурсы", callback_data="sell_resources")]
    ])
    
    try:
        await callback.message.edit_text(new_text, reply_markup=keyboard, parse_mode="MARKDOWN")
    except:
        pass
        
    log_action(user_id, f"Продал ресурсы: бутылки {b}, металл {m}, украденное {stolen}, получил {payout:,} ¢".replace(",", " "))
    await callback.answer(f"💰 Продано!\n🍾 Бутылок: {b} | ⚙️ Металла: {m} | 🥷 Украдено: {stolen}\nПолучено: {payout:,} ¢".replace(",", " "), show_alert=True)


# --- КАЗИНО И ИГРЫ ---
@router.message(F.text.casefold() == "казино")
async def text_casino(message: Message):
    if not await check_ban_and_register(message): return
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
        f"• баскет 100000 (х2 при попадании)\n"
        f"• баскет 100000 мимо (х2 если не попал)"
    ).replace(",", " ")
    await smart_answer(message, text)

# КУЛДАУН ДЛЯ ИГР (защита от спама)
game_cooldowns = {}


# --- РУЛЕТКА ---
@router.message(F.text.regexp(r"(?i)^рул\s+.+"))
async def casino_roulette(message: Message):
    if not await check_ban_and_register(message):
        return

    user_id = message.from_user.id
    log_action(message.from_user.id, "Игра: Рулетка")
    now = time.time()
    if now - game_cooldowns.get(user_id, 0) < 2:
        await message.answer("❌ Нет так быстро!")
        return
    game_cooldowns[user_id] = now

    args = message.text.split()
    if len(args) < 3:
        await message.answer(
            "❌ Неверный формат ставки. Пример:\n"
            "• рул кра 10000\n• рул чер 10к\n• рул 1-12 10к\n• рул чет 10к\n• рул нечет 10к\n• рул от 0-36 10000\n• рул кра вб",
            parse_mode="MARKDOWN",
        )
        return

    target_str = " ".join(args[1:-1]).lower()
    raw_amount_str = args[-1].lower()

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
        await message.answer("❌ У вас недостаточно наличных для такой ставки.")
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
    elif target_str in ["от 0-36", "0-36"]:
        if 0 <= rolled_num <= 36:
            won = True
            payout = amount * 1.5
    elif "-" in target_str:
        try:
            low, high = map(int, target_str.split("-"))
            if low <= rolled_num <= high:
                won = True
                payout = amount * 3
        except:
            pass
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
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (net_profit, user_id),
        )
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
        cursor.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (actual_loss, user_id),
        )
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
    if not await check_ban_and_register(message):
        return

    user_id = message.from_user.id
    log_action(message.from_user.id, "Игра: Покер")
    now = time.time()
    if now - game_cooldowns.get(user_id, 0) < 2:
        await message.answer("❌ Нет так быстро!")
        return
    game_cooldowns[user_id] = now

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ Формат: покер 10000 или покер 10к", parse_mode="MARKDOWN"
        )
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0

    raw_amt = args[1].lower()
    if raw_amt in ["вб", "все", "all"]:
        amount = bal
    else:
        amount = parse_sum(raw_amt)

    if not amount or amount <= 0:
        await message.answer("❌ Неверная сумма ставки.")
        conn.close()
        return

    if bal < amount:
        await message.answer(
            "❌ У вас недостаточно наличных для такой ставки."
        )
        conn.close()
        return

    hands = [
        "Старшая карта",
        "Пара",
        "Две пары",
        "Тройка",
        "Стрит",
        "Фулл-Хаус",
        "Каре",
        "Флеш-Рояль",
    ]
    weights = [60, 30, 6, 2, 1, 0.7, 0.2, 0.1]

    player_hand = random.choices(hands, weights=weights)[0]
    dealer_hand = random.choices(hands, weights=weights)[0]
    hand_power = {h: i for i, h in enumerate(hands)}

    if hand_power[player_hand] > hand_power[dealer_hand]:
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id),
        )
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = (
            f"🎉 Победа в покере!\nВаша комбинация: {player_hand} (Дилер: {dealer_hand})\n💰 Вы выиграли: +{amount:,} ¢\n💰 Баланс: {new_bal:,} ¢"
            .replace(",", " ")
        )
        await send_result_media(message, True, text)
    else:
        actual_loss = min(amount, bal)
        cursor.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (actual_loss, user_id),
        )
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = (
            f"😢 Проигрыш в покере.\nВаша комбинация: {player_hand} (Дилер: {dealer_hand})\n💸 Потеряно: -{actual_loss:,} ¢\n💰 Баланс: {new_bal:,} ¢"
            .replace(",", " ")
        )
        await send_result_media(message, False, text)


# --- ФОРТУНА ---
@router.message(F.text.regexp(r"(?i)^фортуна\s+.+"))
async def casino_wheel_chat(message: Message):
    if not await check_ban_and_register(message):
        return

    user_id = message.from_user.id
    log_action(message.from_user.id, "Игра: Фортуна")
    now = time.time()
    if now - game_cooldowns.get(user_id, 0) < 2:
        await message.answer("❌ Нет так быстро!")
        return
    game_cooldowns[user_id] = now

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ Формат: фортуна 10000 или фортуна 10к", parse_mode="MARKDOWN"
        )
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0

    raw_amt = args[1].lower()
    if raw_amt in ["вб", "все", "all"]:
        cost = bal
    else:
        cost = parse_sum(raw_amt)
    if not cost or cost <= 0:
        await message.answer("❌ Неверная сумма ставки.")
        conn.close()
        return

    if bal < cost:
        await message.answer(
            "❌ У вас недостаточно наличных для такой ставки."
        )
        conn.close()
        return

    multipliers = [-1.0, -0.5, 0.5, 1.0, 2.0, 5.0, 10.0]
    weights = [25, 30, 15, 10, 5, 0.4, 0.1]
    mult = random.choices(multipliers, weights=weights)[0]
    net_change = int(cost * mult)

    if bal + net_change < 0:
        actual_loss = bal
        cursor.execute("UPDATE users SET balance = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = (
            f"🎡 Колесо Фортуны:\n💀 Катастрофа! Вы потеряли все наличные (-{actual_loss:,} ¢). Баланс: 0 ¢"
            .replace(",", " ")
        )
        await send_result_media(message, False, text)
    else:
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (net_change, user_id),
        )
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()

        if net_change > 0:
            text = (
                f"🎡 Колесо Фортуны:\n✨ Удача! Вы выиграли +{net_change:,} ¢ (x{mult})\n💰 Баланс: {new_bal:,} ¢"
                .replace(",", " ")
            )
            await send_result_media(message, True, text)
        elif net_change < 0:
            text = (
                f"🎡 Колесо Фортуны:\n💀 Неудача! Вы потеряли {net_change:,} ¢\n💰 Баланс: {new_bal:,} ¢"
                .replace(",", " ")
            )
            await send_result_media(message, False, text)
        else:
            await message.answer(
                f"🎡 Колесо Фортуны:\n🤝 Ничья!\n💰 Баланс: {new_bal:,} ¢"
                .replace(",", " ")
            )


# --- ДАРТС ---
@router.message(F.text.regexp(r"(?i)^дартс\s+.+"))
async def game_darts(message: Message):
    if not await check_ban_and_register(message):
        return

    user_id = message.from_user.id
    log_action(message.from_user.id, "Игра: Дартс")
    now = time.time()
    if now - game_cooldowns.get(user_id, 0) < 2:
        await message.answer("❌ Нет так быстро!")
        return
    game_cooldowns[user_id] = now

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ Формат: дартс 100000 центр / дартс 100000 мимо",
            parse_mode="MARKDOWN",
        )
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0

    mode = (
        "центр"
        if "центр" in message.text.lower()
        else ("мимо" if "мимо" in message.text.lower() else "центр")
    )

    amount_str = args[1].lower()
    if amount_str in ["центр", "мимо"] and len(args) > 2:
        amount_str = args[2].lower()

    if amount_str in ["вб", "все", "all"]:
        amount = bal
    else:
        amount = parse_sum(amount_str)

    if not amount or amount <= 0:
        await message.answer("❌ Укажите корректную сумму ставки.")
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
    is_absolute_miss = dice_val == 1  # Дротик вообще никуда не попал (в самый край/мимо мишени)

    won = False
    payout = 0

    if mode == "центр":
        if is_hit_center:
            won = True
            payout = amount * 4
    elif mode == "мимо":
        if is_absolute_miss:
            won = True
            payout = amount * 5  # Коэффициент выигрыша при абсолютном промахе

    if won:
        net_profit = payout - amount
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (net_profit, user_id),
        )
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = (
            f"🎯 Дартс: Успех!\nВы ставили на '{mode}', выпало значение {dice_val}.\nВыигрыш: +{net_profit:,} ¢\nБаланс: {new_bal:,} ¢"
            .replace(",", " ")
        )
        await message.answer(text, parse_mode="MARKDOWN")
    else:
        actual_loss = min(amount, bal)
        cursor.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (actual_loss, user_id),
        )
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = (
            f"🎯 Дартс: Мимо кассы!\nВы ставили на '{mode}', выпало значение {dice_val}.\nПроигрыш: -{actual_loss:,} ¢\nБаланс: {new_bal:,} ¢"
            .replace(",", " ")
        )
        await message.answer(text, parse_mode="MARKDOWN")

# --- БАСКЕТБОЛ ---
@router.message(F.text.regexp(r"(?i)^баскет\s+.+"))
async def game_basketball(message: Message):
    if not await check_ban_and_register(message):
        return

    user_id = message.from_user.id
    log_action(message.from_user.id, "Игра: Баскетбол")
    now = time.time()
    if now - game_cooldowns.get(user_id, 0) < 2:
        await message.answer("❌ Нет так быстро!")
        return
    game_cooldowns[user_id] = now

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ Формат: баскет 100000 или баскет 100000 мимо",
            parse_mode="MARKDOWN",
        )
        return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0

    mode = "мимо" if "мимо" in message.text.lower() else "попал"

    amount_str = args[1].lower()
    if amount_str in ["мимо"] and len(args) > 2:
        amount_str = args[2].lower()

    if amount_str in ["вб", "все", "all"]:
        amount = bal
    else:
        amount = parse_sum(amount_str)

    if not amount or amount <= 0:
        await message.answer("❌ Укажите корректную сумму ставки.")
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
    won = False
    payout = 0

    if mode == "попал":
        if is_scored:
            won = True
            payout = amount * 2
    elif mode == "мимо":
        if not is_scored:
            won = True
            payout = amount * 2

    if won:
        net_profit = payout - amount
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (net_profit, user_id),
        )
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = (
            f"🏀 Баскетбол: Гол!\nМяч брошен. Вы угадали исходы!\nВыигрыш: +{net_profit:,} ¢\nБаланс: {new_bal:,} ¢"
            .replace(",", " ")
        )
        await message.answer(text, parse_mode="MARKDOWN")
    else:
        actual_loss = min(amount, bal)
        cursor.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (actual_loss, user_id),
        )
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        text = (
            f"🏀 Баскетбол: Мимо!\nМяч брошен. Вы проиграли ставку.\nПотеряно: -{actual_loss:,} ¢\nБаланс: {new_bal:,} ¢"
            .replace(",", " ")
        )
        await message.answer(text, parse_mode="MARKDOWN")


# --- БАНК И ПЕРЕВОДЫ ---
@router.message(F.text.casefold().in_(["банк", "баланс"]))
async def text_bank(message: Message):
    if not await check_ban_and_register(message): return
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
            await message.answer("❌ Формат: Пер @юзер сумма (или ответьте на сообщение: Пер сумма)")
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
            await message.answer("❌ Пользователь с таким юзернеймом не найден в базе игры.")
            conn.close()
            return
        target_id = target_row[0]
        target_display_name = target_row[1] if target_row[1] else (target_row[2] if target_row[2] else target_username)

    if target_id == sender_id:
        await message.answer("❌ Нельзя переводить деньги самому себе.")
        conn.close()
        return

    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,))
    target_check = cursor.fetchone()
    if not target_check:
        await message.answer("❌ Получатель не зарегистрирован в базе данных игры.")
        conn.close()
        return

    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, sender_id))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
    conn.commit()
    conn.close()

    formatted_amount = f"{amount:,}".replace(",", " ")
    sender_name = message.from_user.full_name
    log_action(sender_id, f"Перевод {formatted_amount} ¢ → {target_display_name}")
    log_action(target_id, f"Получил перевод {formatted_amount} ¢ ← {sender_name}")

    if amount >= BIG_TRANSFER_THRESHOLD:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🚨 БОЛЬШОЙ ПЕРЕВОД\n\n"
                    f"💰 Сумма: {formatted_amount} ¢\n"
                    f"👤 Кто: {sender_name} (@{message.from_user.username or 'нет'})\n"
                    f"➡️ Кому: {target_display_name}\n"
                    f"🆔 Отправитель ID: {sender_id}\n"
                    f"🆔 Получатель ID: {target_id}"
                )
            except Exception as e:
                print(f"ADMIN TRANSFER ALERT ERROR: {e}")

    await message.answer(f"✅ Успешно переведено {formatted_amount} ¢ пользователю {target_display_name}.")


# --- ФЛИПИНГ И КВАРТИРЫ ---
@router.message(F.text.casefold() == "флипинг")
async def text_flipping(message: Message):
    if not await check_ban_and_register(message): return
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
        await callback.answer("❌ Достигнут лимит недвижимости (максимум 10)!", show_alert=True)
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

    await callback.answer("🏠 Квартира успешно куплена! Появился новый вариант.", show_alert=True)

@router.message(F.text.casefold().in_(["мои квартиры", "квартиры"]))
async def text_my_apartments(message: Message):
    if not await check_ban_and_register(message): return
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
@router.message(F.text.casefold() == "бонус")
async def text_bonus(message: Message):
    if not await check_ban_and_register(message): return
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
    
@router.message(F.text.casefold().in_(["реф", "рефералы", "реферал"]))
async def text_referral(message: Message):
    if not await check_ban_and_register(message): return
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    
    ref_count = res[0] if res else 0
    ref_earned = ref_count * 1_000_000
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
        [InlineKeyboardButton(text="Поделиться", url=f"https://t.me/share/url?url={ref_link}&text=Заходи в бота! Приглашено: {ref_count}. Собрано за рефералов: {ref_earned:,} ¢".replace(",", " "))]
    ])
    log_action(user_id, "Открыл раздел рефералов")
    await message.answer(text, reply_markup=keyboard, parse_mode="MARKDOWN")


# --- СИСТЕМА КРЕАТОРОВ ---
@router.message(F.text.casefold() == "креатор")
async def text_creator_menu(message: Message):
    user_id = message.from_user.id
    if not await is_creator(user_id):
        return
    await message.answer(
        "🎬 Меню креатора:\n\n"
        "• зп — получить зарплату 7.5кк (1 раз в 24 часа)\n"
        "• промо [код] [сумма] [активации] — создать промокод (сумма от 1кк до 10кк)",
        parse_mode="MARKDOWN"
    )

@router.message(F.text.casefold() == "зп")
async def creator_salary(message: Message):
    user_id = message.from_user.id
    if not await is_creator(user_id):
        return

    now = time.time()
    last = creator_salary_cooldowns.get(user_id, 0.0)
    cooldown = 24 * 3600

    if now - last < cooldown:
        time_left = int(cooldown - (now - last))
        hours = time_left // 3600
        minutes = (time_left % 3600) // 60
        await message.answer(f"⏳ Зарплату можно получать 1 раз в 24 часа. Ждите еще {hours} ч. {minutes} мин.")
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

    await message.answer(f"💼 Вы успешно получили зарплату креатора: +7 500 000 ¢!\n💰 Баланс: {new_bal:,} ¢".replace(",", " "), parse_mode="MARKDOWN")


# --- ПРОМОКОДЫ ---
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
            await message.answer("❌ Креаторы могут создавать промокоды с наградой от 1 000 000 ¢ до 10 000 000 ¢.")
            return

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR REPLACE INTO promo_codes (code, reward, activations_left) VALUES (?, ?, ?)", 
                       (code, reward, activations))
        conn.commit()
        await message.answer(f"✅ Промокод {code} создан!\n💰 Награда: {reward:,} ¢\n👥 Активаций: {activations}".replace(",", " "), parse_mode="MARKDOWN")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        conn.close()

@router.message(F.text.regexp(r"(?i)^(код|активировать)\s+\w+$"))
async def user_activate_promo(message: Message):
    if not await check_ban_and_register(message): return
    args = message.text.split()
    code = args[1].upper()
    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT reward, activations_left FROM promo_codes WHERE code = ?", (code,))
    promo = cursor.fetchone()
    
    if not promo:
        await message.answer("❌ Такого промокода не существует или он истек.")
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

    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
    cursor.execute("UPDATE promo_codes SET activations_left = activations_left - 1 WHERE code = ?", (code,))
    cursor.execute("INSERT INTO user_promo (user_id, code) VALUES (?, ?)", (user_id, code))
    conn.commit()
    
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    new_bal = cursor.fetchone()[0]
    conn.close()

    await message.answer(f"🎉 Промокод {code} активирован!\n🎁 Получено: +{reward:,} ¢\n💰 Баланс: {new_bal:,} ¢".replace(",", " "), parse_mode="MARKDOWN")


# --- АДМИН-ПАНЕЛЬ ---
@router.message(F.text.casefold() == "админ")
async def cmd_admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer(
        "🛠 Панель администратора:\n\n"
        "• выдать @юз сумма\n"
        "• обнулить @юз\n"
        "• бан @юз\n"
        "• разбан @юз\n"
        "• +креатор @юз — назначить креатором\n"
        "• -креатор @юз — снять креатора\n"
        "• промо [код] [сумма] [кол-во]\n"
        "• история @юз — последние действия игрока\n"
        f"• большой перевод — уведомление от {BIG_TRANSFER_THRESHOLD:,} ¢".replace(",", " "),
        parse_mode="MARKDOWN"
    )

@router.message(F.text.regexp(r"(?i)^(история|действия)\s+@\w+"))
async def admin_history(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    target_username = message.text.split()[1].replace("@", "").lower()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, custom_name, username FROM users WHERE LOWER(username) = ?", (target_username,))
    user = cur.fetchone()
    if not user:
        conn.close()
        await message.answer("❌ Игрок не найден.")
        return
    user_id, custom_name, username = user
    cur.execute("SELECT action, created_at FROM action_logs WHERE user_id = ? ORDER BY id DESC LIMIT 15", (user_id,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await message.answer("📭 У этого игрока пока нет записей о действиях.")
        return
    name = custom_name or username or target_username
    lines = [f"📋 Последние действия: {name}", ""]
    for action, created in rows:
        stamp = time.strftime("%d.%m %H:%M", time.localtime(created))
        lines.append(f"• {stamp} — {action}")
    await message.answer("\n".join(lines))


@router.message(F.text.regexp(r"(?i)^выдать\s+@\w+\s+.+"))
async def admin_give(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    target_username = args[1].replace("@", "").lower()
    amount = parse_sum(args[2])
    if not amount: return

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE LOWER(username) = ?", (amount, target_username))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Администратор выдал {amount:,} ¢ игроку @{target_username}!".replace(",", " "), parse_mode="MARKDOWN")

@router.message(F.text.regexp(r"(?i)^обнулить\s+@\w+"))
async def admin_reset(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    target_username = message.text.split()[1].replace("@", "").lower()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = 10000, bank_balance = 0, invested = 0, bottles = 0, metal = 0, stolen = 0, ref_count = 0 WHERE LOWER(username) = ?", (target_username,))
    cursor.execute("DELETE FROM user_apartments WHERE user_id IN (SELECT user_id FROM users WHERE LOWER(username) = ?)", (target_username,))
    conn.commit()
    conn.close()
    await message.answer(f"✅ Игрок @{target_username} полностью обнулен.")

@router.message(F.text.regexp(r"(?i)^бан\s+@\w+"))
async def admin_ban(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    target_username = message.text.split()[1].replace("@", "").lower()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET banned = 1 WHERE LOWER(username) = ?", (target_username,))
    conn.commit()
    conn.close()
    await message.answer(f"🚫 Пользователь @{target_username} заблокирован.")

@router.message(F.text.regexp(r"(?i)^разбан\s+@\w+"))
async def admin_unban(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    target_username = message.text.split()[1].replace("@", "").lower()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET banned = 0 WHERE LOWER(username) = ?", (target_username,))
    conn.commit()
    conn.close()
    await message.answer(f"🟢 Пользователь @{target_username} был разбанен.")

@router.message(F.text.regexp(r"(?i)^\+креатор\s+@\w+"))
async def admin_add_creator(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    target_username = message.text.split()[1].replace("@", "").lower()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_creator = 1 WHERE LOWER(username) = ?", (target_username,))
    conn.commit()
    if cursor.rowcount > 0:
        await message.answer(f"🎬 Пользователь @{target_username} назначен креатором.", parse_mode="MARKDOWN")
    else:
        await message.answer(f"❌ Пользователь @{target_username} не найден в базе данных.", parse_mode="MARKDOWN")
    conn.close()

@router.message(F.text.regexp(r"(?i)^\-креатор\s+@\w+"))
async def admin_remove_creator(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    target_username = message.text.split()[1].replace("@", "").lower()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_creator = 0 WHERE LOWER(username) = ?", (target_username,))
    conn.commit()
    if cursor.rowcount > 0:
        await message.answer(f"❌ Пользователь @{target_username} снят с поста креатора.", parse_mode="MARKDOWN")
    else:
        await message.answer(f"⚠️ Пользователь @{target_username} не найден.", parse_mode="MARKDOWN")
    conn.close()

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

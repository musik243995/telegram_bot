import random
import time
import sqlite3
from aiogram import F, Bot, Dispatcher, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command, CommandObject

API_TOKEN = "8885671207:AAGpxxnH2HQqg3o09bE2ZPinj4Dvdof1WrQ"  # Замени на свой токен

bot = Bot(token=API_TOKEN)
router = Router()
dp = Dispatcher()
dp.include_router(router)

# Словари для кулдаунов работ
work_cooldowns = {}

# --- СПИСОК АДМИНИСТРАТОРОВ ---
ADMIN_IDS = [1222239198]  # Укажи свои Telegram ID через запятую

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
def get_private_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Казино"), KeyboardButton(text="Работа")],
            [KeyboardButton(text="Флипинг"), KeyboardButton(text="Банк")],
            [KeyboardButton(text="Профиль"), KeyboardButton(text="Топ")],
            [KeyboardButton(text="Квартиры"), KeyboardButton(text="Бонус")],
            [KeyboardButton(text="Рефералы"), KeyboardButton(text="Помощь")]
        ],
        resize_keyboard=True
    )

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
            last_bank_calc REAL,
            last_bonus REAL DEFAULT 0,
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

# Функция выдачи клавиатуры только в ЛС (в группах клавиатура удаляется)
async def smart_answer(message: Message, text: str, reply_markup=None, parse_mode="MARKDOWN"):
    if message.chat.type == "private":
        await message.answer(text, reply_markup=get_private_keyboard(), parse_mode=parse_mode)
    else:
        await message.answer(text, reply_markup=ReplyKeyboardRemove(), parse_mode=parse_mode)

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
        "• Чекнуть игрока: Ответьте реплаем чекнуть на сообщение другого игрока.\n"
        "• Топ: Напишите топ.\n"
        "• Перевод: Пер @юз сумма\n"
        "• Казино: Ставки через слово рул (например: рул кра 10к или рул 7 5000).\n"
        "• Банк: вложить [сумма] и снять [сумма]."
    )
    await smart_answer(message, text)
    
# Изменение игрового ника (+ник Имя)
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

# --- ПРОФИЛЬ ("я" и "чеекнуть") ---
def format_profile(user_data):
    user_id, display_name, balance, bank_balance, invested, apt_count, ref_count = user_data
    return (
        f"👤 Профиль игрока: {display_name}\n\n"
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
    
    # Достаем custom_name, username и финансовые данные
    cursor.execute("SELECT custom_name, username, balance, bank_balance, invested, bottles, metal FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    
    if not u:
        custom_name, uname, balance, bank_balance, invested, bottles, metal = (None, "user", 10000, 0, 0, 0, 0)
    else:
        custom_name, uname, balance, bank_balance, invested, bottles, metal = u

    # Выбираем имя: кастомный ник -> телеграм юзернейм -> имя пользователя -> "Игрок"
    display_name = custom_name if custom_name else (uname if uname else message.from_user.first_name)

    # Считаем квартиры
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = ?", (user_id,))
    apt_res = cursor.fetchone()
    apt_count = apt_res[0] if apt_res else 0

    # Считаем рефералов
    cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (user_id,))
    ref_res = cursor.fetchone()
    ref_count = ref_res[0] if ref_res and ref_res[0] is not None else 0
    
    conn.close()

    text = format_profile((user_id, display_name, balance, bank_balance, invested, apt_count, ref_count))
    await message.answer(text)
# --- ЧЕКНУТЬ ПРОФИЛЬ ЧЕРЕЗ РЕПЛАЙ ("чекнуть") ---
@router.message(F.text.casefold() == "чекнуть")
async def msg_check_profile(message: Message):
    if not await check_ban_and_register(message): return
    if not message.reply_to_message:
        await message.answer("❌ Ответьте этой командой («чекнуть») на сообщение пользователя, профиль которого хотите посмотреть.")
        return
        
    target_id = message.reply_to_message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    
    # Запрашиваем custom_name, username и основные финансы (без бутылок и металла)
    cursor.execute("SELECT custom_name, username, balance, bank_balance, invested FROM users WHERE user_id = ?", (target_id,))
    u = cursor.fetchone()
    if not u:
        await message.answer("❌ Пользователь не найден в базе данных игры.")
        conn.close()
        return

    custom_name, uname, balance, bank_balance, invested = u
    # Выбираем: если у игрока есть кастомный ник, берем его, иначе username или имя
    display_name = custom_name if custom_name else (uname if uname else message.reply_to_message.from_user.first_name)

    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = ?", (target_id,))
    apt_count = cursor.fetchone()[0]

    cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (target_id,))
    ref_res = cursor.fetchone()
    ref_count = ref_res[0] if ref_res and ref_res[0] is not None else 0
    conn.close()

    text = format_profile((target_id, display_name, balance, bank_balance, invested, apt_count, ref_count))
    await smart_answer(message, text)

# --- ТОП ИГРОКОВ («топ») ---
@router.message(F.text.casefold().in_((["топ", "топ игроков"])))
async def text_top(message: Message):
    if not await check_ban_and_register(message): return
    conn = get_db()
    cursor = conn.cursor()
    # Запрашиваем custom_name, username и общую сумму баланса
    cursor.execute("SELECT custom_name, username, (balance + bank_balance + invested) as total FROM users ORDER BY total DESC LIMIT 10")
    top_list = cursor.fetchall()
    conn.close()

    text = "🏆 Топ-10 самых богатых игроков:\n\n"
    for idx, (custom_name, uname, total) in enumerate(top_list, 1):
        # Если есть кастомный ник — берем его, иначе username или "Игрок"
        display_name = custom_name if custom_name else (uname if uname else "Игрок")
        text += f"{idx}. {display_name} — {total:,} ¢\n"
    
    text = text.replace(",", " ")
    await smart_answer(message, text)


# --- РАБОТА И СБОР РЕСУРСОВ ---
@router.message(F.text.casefold().in_(["работа", "ферма", "болото"]))
async def text_work(message: Message):
    if not await check_ban_and_register(message): return
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
        [InlineKeyboardButton(text="⚙️️ Собирать металл (КД 4с)", callback_data="work_metal")],
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
    await callback.answer("⚙️ Вы нашли металлолом!")

@router.callback_query(F.data == "sell_resources")
async def sell_resources(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    
    # Сначала проверяем, есть ли пользователь в базе, если нет — регистрируем на лету
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
    
    # Получаем обновленный баланс
    cursor.execute("SELECT balance, bottles, metal FROM users WHERE user_id = ?", (user_id,))
    u = cursor.fetchone()
    conn.close()
    
    # Обновляем текст сообщения в чате
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

# --- КАЗИНО И ИГРЫ ---
@router.message(F.text.casefold() == "казино")
async def text_casino(message: Message):
    if not await check_ban_and_register(message): return
    text = (
        f"🎰 Игровой зал (Казино):\n\n"
        f"📜 Правила и игры:\n\n"
        f"🎯 1. Рулетка:\n"
        f"• рул кра [сумма] (или чер, бол, мал, 1-12, число 0-36)\n"
        f"*Пример:* рул кра 10ккк\n\n"
        f"🃏 2. Покер:\n"
        f"• Напишите в чат: покер [сумма]\n"
        f"*Пример:* покер 10ккк\n\n"
        f"🎡 3. Колесо Фортуны:\n"
        f"• Напишите в чат: фортуна [сумма]\n"
        f"*Пример:* фортуна 10кк"
    ).replace(",", " ")
    await smart_answer(message, text)

def parse_sum(text_val):
    text_val = text_val.lower().replace(" ", "")
    multiplier = 1
    if "ккк" in text_val or "b" in text_val:
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

@router.message(F.text.regexp(r"(?i)^рул\s+.+"))
async def casino_roulette(message: Message):
    if not await check_ban_and_register(message): return
    args = message.text.split()
    if len(args) < 3:
        await message.answer(
            "❌ Формат ставки:\n"
            "• рул кра [сумма] / рул чер [сумма]\n"
            "• рул бол [сумма] / рул мал [сумма]\n"
            "• рул 13-24 [сумма]\n"
            "• рул 7 [сумма]\n"
            "Пример: рул кра 13кк", 
            parse_mode="MARKDOWN"
        )
        return
    
    target_str = args[1].lower()
    amount = parse_sum(args[2])
    
    if not amount or amount <= 0:
        await message.answer("❌ Неверная сумма ставки.")
        return

    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    bal = cursor.fetchone()[0]
    
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
    elif target_str in ["бол", "больше"]:
        if 19 <= rolled_num <= 36:
            won = True
            payout = amount * 2
    elif target_str in ["мал", "меньше"]:
        if 1 <= rolled_num <= 18:
            won = True
            payout = amount * 2
    elif "-" in target_str:
        try:
            low, high = map(int, target_str.split("-"))
            if low <= rolled_num <= high:
                won = True
                payout = amount * 3
        except:
            await message.answer("❌ Неверный диапазон. Пример: 1-12, 13-24, 25-36.", parse_mode="MARKDOWN")
            conn.close()
            return
    else:
        try:
            chosen_num = int(target_str)
            if chosen_num == rolled_num:
                won = True
                payout = amount * 36
        except:
            await message.answer("❌ Неверное число или тип ставки.")
            conn.close()
            return

    if won:
        net_profit = payout - amount
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net_profit, user_id))
        conn.commit()
        
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()

        await message.answer(
            f"Ты выйграл 🎉 Выпало {rolled_num} {rolled_color}\n\n"
            f"+{payout:,}¢\n\n"
            f"Мой баланс:{new_bal:,}¢".replace(",", " ")
        )
    else:
        actual_loss = min(amount, bal)
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (actual_loss, user_id))
        conn.commit()
        
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()

        await message.answer(
            f"Ты проиграл 🫠 Выпало {rolled_num} {rolled_color}\n\n"
            f"-{actual_loss:,}¢\n\n"
            f"Ваш баланс:{new_bal:,}¢".replace(",", " ")
        )

# Покер по ставке из чата (покер 10ккк)
@router.message(F.text.regexp(r"(?i)^покер\s+.+"))
async def casino_poker_chat(message: Message):
    if not await check_ban_and_register(message): return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Формат: покер 10ккк", parse_mode="MARKDOWN")
        return
    
    amount = parse_sum(args[1])
    if not amount or amount <= 0:
        await message.answer("❌ Неверная сумма ставки.")
        return

    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0
    
    if bal < amount:
        await message.answer("❌ У вас недостаточно наличных для такой ставки.")
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
        await message.answer(f"🎉 Победа в покере!\nВаша комбинация: {player_hand} (Дилер: {dealer_hand})\n💰 Вы выиграли: +{amount:,} ¢\n💰 Баланс: {new_bal:,} ¢".replace(",", " "), parse_mode="MARKDOWN")
    elif hand_power[player_hand] < hand_power[dealer_hand]:
        actual_loss = min(amount, bal)
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (actual_loss, user_id))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        await message.answer(f"😢 Проигрыш в покере.\nВаша комбинация: {player_hand} (Дилер: {dealer_hand} — сильнее)\n💸 Потеряно: -{actual_loss:,} ¢\n💰 Баланс: {new_bal:,} ¢".replace(",", " "), parse_mode="MARKDOWN")
    else:
        # При ничьей побеждает дилер (в пользу казино)
        actual_loss = min(amount, bal)
        cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (actual_loss, user_id))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        await message.answer(f"❌ Ничья в пользу дилера!\n\nУ вас и у дилера: {player_hand} (дилер побеждает в спорных).\n\nПотеряно: -{actual_loss:,} ¢\nБаланс: {new_bal:,} ¢".replace(",", " "))


# Колесо фортуны по ставке из чата (фортуна 10ккк)
@router.message(F.text.regexp(r"(?i)^фортуна\s+.+"))
async def casino_wheel_chat(message: Message):
    if not await check_ban_and_register(message): return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Формат: фортуна 10ккк", parse_mode="MARKDOWN")
        return
    
    cost = parse_sum(args[1])
    if not cost or cost <= 0:
        await message.answer("❌ Неверная сумма ставки.")
        return

    user_id = message.from_user.id
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    bal = res[0] if res else 0
    
    if bal < cost:
        await message.answer("❌ У вас недостаточно наличных для такой ставки.")
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
        await message.answer(f"🎡 Колесо Фортуны:\n💀 Катастрофа! Вы потеряли все свои наличные (-{actual_loss:,} ¢). Баланс: 0 ¢".replace(",", " "), parse_mode="MARKDOWN")
    else:
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (net_change, user_id))
        conn.commit()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        new_bal = cursor.fetchone()[0]
        conn.close()
        
        if net_change > 0:
            await message.answer(f"🎡 Колесо Фортуны:\n✨ Удача! Вы выиграли +{net_change:,} ¢ (x{mult})\n💰 Баланс: {new_bal:,} ¢".replace(",", " "), parse_mode="MARKDOWN")
        elif net_change < 0:
            await message.answer(f"🎡 Колесо Фортуны:\n💀 Неудача! Вы потеряли {net_change:,} ¢\n💰 Баланс: {new_bal:,} ¢".replace(",", " "), parse_mode="MARKDOWN")
        else:
            await message.answer(f"🎡 Колесо Фортуны:\n🤝 Ничья! Ни выигрыша, ни проигрыша.\n💰 Баланс: {new_bal:,} ¢".replace(",", " "), parse_mode="MARKDOWN")

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
    
    # Вариант 1: Перевод через реплай (ответ на сообщение)
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
        target_display_name = message.reply_to_message.from_user.first_name
        if len(args) < 2:
            await message.answer("❌ Формат: Пер [сумма] (в ответ на сообщение)")
            return
        amount = parse_sum(args[1])
        
    # Вариант 2: Перевод по юзернейму (как было раньше)
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
    
    # Проверяем баланс отправителя
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (sender_id,))
    sender_bal_res = cursor.fetchone()
    sender_bal = sender_bal_res[0] if sender_bal_res else 0
    
    if sender_bal < amount:
        await message.answer("❌ У вас недостаточно наличных средств для перевода.")
        conn.close()
        return

    # Если переводили через реплай, то target_id у нас уже есть. Если по юзернейму — ищем в базе.
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

    # Проверяем, существует ли получатель в базе (если это был реплай, но он еще не зареган в игре)
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,))
    target_check = cursor.fetchone()
    if not target_check:
        await message.answer("❌ Получатель не зарегистрирован в базе данных игры.")
        conn.close()
        return

    # Проводим транзакцию
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, sender_id))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
    conn.commit()
    conn.close()

    formatted_amount = f"{amount:,}".replace(",", " ")
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

    price = random.randint(10000, 100000000000)
    apt_name = random.choice(APARTMENT_NAMES)

    text = (
        f"🏢 Рынок недвижимости (Флипинг):\n\n"
        f"• {apt_name}\n"
        f"💰 Цена покупки: {price:,} ₽\n\n"
        f"📊 У вас недвижимости: {apt_count}/10 шт."
    ).replace(",", " ")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить квартиру", callback_data=f"buy_apt_{price}_{apt_name[:12]}"),
         InlineKeyboardButton(text="⏭ Другой вариант", callback_data="refresh_flip")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="MARKDOWN")

@router.callback_query(F.data == "refresh_flip")
async def refresh_flipping(callback: CallbackQuery):
    price = random.randint(10000, 100000000000)
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
        f"💰 Цена покупки: {price:,} ₽\n\n"
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

    # Сразу генерируем новую случайную квартиру для магазина
    new_price = random.randint(10000, 100000000000)
    new_apt_name = random.choice(APARTMENT_NAMES)
    
    # Получаем актуальный новый счетчик квартир
    cursor.execute("SELECT COUNT(*) FROM user_apartments WHERE user_id = ?", (user_id,))
    new_apt_count = cursor.fetchone()[0]
    conn.close()

    text = (
        f"🏢 Рынок недвижимости (Флипинг):\n\n"
        f"• {new_apt_name}\n"
        f"💰 Цена покупки: {new_price:,} ₽\n\n"
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

    # Получаем оставшиеся квартиры пользователя, чтобы пересобрать клавиатуру без проданной
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
    # Запрашиваем только существующий столбец ref_count
    cursor.execute("SELECT ref_count FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    
    ref_count = res[0] if res else 0
    ref_earned = ref_count * 100000  # Автоматический расчет заработка

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

# --- ПРОМОКОДЫ ---
@router.message(F.text.regexp(r"(?i)^промо\s+\w+\s+\d+\s+\d+$"))
async def admin_create_promo(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    code = args[1].upper()
    reward = parse_sum(args[2])
    activations = int(args[3])

    if not reward or reward <= 0:
        await message.answer("❌ Неверная сумма награды.")
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
        "• промо [код] [сумма] [кол-во]",
        parse_mode="MARKDOWN"
    )

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
    cursor.execute("UPDATE users SET balance = 10000, bank_balance = 0, invested = 0, bottles = 0, metal = 0, ref_count = 0 WHERE LOWER(username) = ?", (target_username,))
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


from aiohttp import web

# Простой веб-сервер для того, чтобы Render не усыплял бота
async def handle(request):
    return web.Response(text="Бот работает 24/7! 🚀")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    # Render передает порт через системные переменные окружения, либо используем 8080
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Веб-сервер запущен на порту {port}")

# И самое главное — меняем запуск бота, чтобы он запускал и сервер, и самого бота одновременно:
async def main():
    # Запускаем веб-сервер в фоне
    await web_server()
    # Запускаем самого Telegram-бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    import os
    asyncio.run(main())

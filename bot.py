import asyncio
import aiohttp
import logging
import os
import base64
import io
import aiosqlite
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

# ========== НАСТРОЙКИ ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ADMIN_ID = 509340766
ADMIN_CONTACT = "https://t.me/igor_osipov_1996"

BOT_USERNAME = "Osipov_ii_bot"   # замените на реальный username вашего бота

PRICE_GENERATION = 10
REFERRAL_BONUS = 20
RAFFLE_BONUS = 50

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_NAME = "google/gemini-3-pro-image-preview"   # 🚀 НОВАЯ МОДЕЛЬ
BOT_LINK = "https://t.me/My_Osipov_big_bot"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

application = None
DB_NAME = "users.db"

# ---------- БАЗА ДАННЫХ ----------
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 0,
                free_generation_used BOOLEAN DEFAULT 0,
                referrer_id INTEGER DEFAULT NULL,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        try:
            await db.execute('ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL')
        except:
            pass
        await db.execute('''
            CREATE TABLE IF NOT EXISTS deposit_requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                coins INTEGER,
                status TEXT DEFAULT 'pending',
                screenshot_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        try:
            await db.execute('ALTER TABLE deposit_requests ADD COLUMN coins INTEGER')
        except:
            pass
        try:
            await db.execute('ALTER TABLE deposit_requests ADD COLUMN screenshot_id TEXT')
        except:
            pass
        await db.execute('''
            CREATE TABLE IF NOT EXISTS raffle_requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                bonus INTEGER DEFAULT 50,
                status TEXT DEFAULT 'pending',
                screenshot_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()

async def register_user(user_id, username, first_name, referrer_id=None):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            'INSERT OR IGNORE INTO users (user_id, username, first_name, referrer_id) VALUES (?, ?, ?, ?)',
            (user_id, username or "None", first_name or "None", referrer_id)
        )
        await db.commit()

async def get_total_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT COUNT(*) FROM users') as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_user_balance(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_free_generation_status(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT free_generation_used FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] == 1 if row else False

async def use_free_generation(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE users SET free_generation_used = 1 WHERE user_id = ?', (user_id,))
        await db.commit()

async def deduct_balance(user_id, amount):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))
        await db.commit()

async def add_balance(user_id, amount):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
        await db.commit()

async def get_referrer(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT referrer_id FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def create_deposit_request(user_id, amount_rub, coins):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            'INSERT INTO deposit_requests (user_id, amount, coins) VALUES (?, ?, ?)',
            (user_id, amount_rub, coins)
        )
        await db.commit()
        return cursor.lastrowid

async def confirm_deposit(request_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute('SELECT user_id, coins FROM deposit_requests WHERE request_id = ?', (request_id,))
        row = await cur.fetchone()
        if not row:
            return False
        uid, coins = row
        await db.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (coins, uid))
        await db.execute('UPDATE deposit_requests SET status = "approved" WHERE request_id = ?', (request_id,))
        await db.commit()
        referrer = await get_referrer(uid)
        if referrer:
            await add_balance(referrer, REFERRAL_BONUS)
            try:
                await application.bot.send_message(referrer, f"🎉 Ваш реферал пополнил баланс! Вы получили +{REFERRAL_BONUS} монет.")
            except:
                pass
        return True

async def reject_deposit(request_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE deposit_requests SET status = "rejected" WHERE request_id = ?', (request_id,))
        await db.commit()

async def get_pending_deposits():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT request_id, user_id, amount, coins, screenshot_id FROM deposit_requests WHERE status = "pending" ORDER BY created_at') as cursor:
            return await cursor.fetchall()

async def create_raffle_request(user_id, screenshot_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            'INSERT INTO raffle_requests (user_id, screenshot_id) VALUES (?, ?)',
            (user_id, screenshot_id)
        )
        await db.commit()
        return cursor.lastrowid

async def confirm_raffle(request_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute('SELECT user_id FROM raffle_requests WHERE request_id = ?', (request_id,))
        row = await cur.fetchone()
        if not row:
            return False
        uid = row[0]
        await add_balance(uid, RAFFLE_BONUS)
        await db.execute('UPDATE raffle_requests SET status = "approved" WHERE request_id = ?', (request_id,))
        await db.commit()
        return True

async def reject_raffle(request_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE raffle_requests SET status = "rejected" WHERE request_id = ?', (request_id,))
        await db.commit()

async def get_pending_raffles():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT request_id, user_id, screenshot_id FROM raffle_requests WHERE status = "pending" ORDER BY created_at') as cursor:
            return await cursor.fetchall()

def compress_image(image_bytes: bytes, max_size_mb: float = 9.0) -> bytes:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        max_dim = 2000
        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        quality = 85
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=quality, optimize=True)
        result = buffer.getvalue()
        while len(result) > max_size_mb * 1024 * 1024 and quality > 50:
            quality -= 10
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=True)
            result = buffer.getvalue()
        return result
    except Exception as e:
        logger.warning(f"Сжатие не удалось: {e}")
        return image_bytes

# ---------- ГЕНЕРАЦИЯ ЧЕРЕЗ GEMINI 3 PRO ----------
async def process_image_request(prompt: str, photo_bytes: bytes = None):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/transparent_generator_bot",
        "X-Title": "Transparent Generator"
    }
    content_parts = [{"type": "text", "text": prompt}]
    if photo_bytes:
        compressed = compress_image(photo_bytes, max_size_mb=8)
        photo_base64 = base64.b64encode(compressed).decode('utf-8')
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{photo_base64}"}
        })
    payload = {
        "model": MODEL_NAME,   # google/gemini-3-pro-image-preview
        "messages": [{"role": "user", "content": content_parts}],
        "modalities": ["image", "text"],
        "max_tokens": 4096
    }
    async with aiohttp.ClientSession() as session:
        for attempt in range(3):
            try:
                async with session.post(OPENROUTER_URL, headers=headers, json=payload, timeout=180) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        return None, f"Ошибка API {resp.status}: {err_text[:200]}"
                    data = await resp.json()
                    try:
                        image_data_url = data["choices"][0]["message"]["images"][0]["image_url"]["url"]
                        if image_data_url.startswith("data:image"):
                            base64_str = image_data_url.split(',', 1)[1]
                            img_bytes = base64.b64decode(base64_str)
                            return img_bytes, None
                        else:
                            return image_data_url, None
                    except Exception as e:
                        if attempt < 2:
                            await asyncio.sleep(2)
                            continue
                        return None, f"Не удалось извлечь изображение: {str(e)}"
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
                return None, str(e)
    return None, "Не удалось完成生成"

# ---------- КЛАВИАТУРЫ ----------
def get_main_keyboard(user_id: int):
    keyboard = [
        [InlineKeyboardButton("🎨 Сгенерировать", callback_data='generate')],
        [InlineKeyboardButton("💰 Баланс", callback_data='balance')],
        [InlineKeyboardButton("💳 Пополнить", callback_data='deposit')],
        [InlineKeyboardButton("ℹ️ Цены и бонусы", callback_data='info')],
        [InlineKeyboardButton("🔗 Реферальная ссылка", callback_data='referral')],
        [InlineKeyboardButton("🎲 Наши розыгрыши", url=BOT_LINK)]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("🔧 Админ", callback_data='admin_panel')])
    return InlineKeyboardMarkup(keyboard)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = await get_user_balance(user_id)
    free_used = await get_free_generation_status(user_id)
    total_users = await get_total_users()
    free_status = "✅ Доступна" if not free_used else "❌ Использована"
    text = (
        f"👥 *Всего пользователей:* {total_users}\n\n"
        f"🖼️ *Прозрачный генератор*\n\n"
        f"💰 Ваш баланс: {balance} монет\n"
        f"🎁 Бесплатная генерация: {free_status}\n"
        f"💵 Стоимость: {PRICE_GENERATION} монет\n\n"
        f"👇 Нажмите 'Сгенерировать' и опишите, что хотите получить."
    )
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=get_main_keyboard(user_id), parse_mode='Markdown')
        except:
            await update.callback_query.message.reply_text(text, reply_markup=get_main_keyboard(user_id), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=get_main_keyboard(user_id), parse_mode='Markdown')

async def can_generate(user_id: int):
    if user_id == ADMIN_ID:
        return True, 0, "admin"
    if not await get_free_generation_status(user_id):
        return True, 0, "free"
    balance = await get_user_balance(user_id)
    if balance >= PRICE_GENERATION:
        return True, PRICE_GENERATION, "paid"
    return False, PRICE_GENERATION, "paid"

async def process_generation(user_id: int):
    if user_id == ADMIN_ID:
        return
    if not await get_free_generation_status(user_id):
        await use_free_generation(user_id)
        return
    await deduct_balance(user_id, PRICE_GENERATION)

# ---------- ОБРАБОТЧИКИ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    referrer_id = None
    if context.args and len(context.args) > 0:
        try:
            referrer_id = int(context.args[0])
            if referrer_id == user_id:
                referrer_id = None
        except:
            pass
    await register_user(user_id, username, first_name, referrer_id)
    total_users = await get_total_users()
    welcome_text = (
        f"🌟 *Привет, {first_name}!*\n\n"
        f"👥 *Всего пользователей бота:* {total_users}\n\n"
        f"🤖 *Прозрачный генератор* — создавай и редактируй фото с ИИ.\n\n"
        f"✨ *Что я умею:*\n"
        f"• Генерировать картинки по тексту\n"
        f"• Редактировать ваши фото\n"
        f"• Первая генерация бесплатно\n"
        f"• Цена: {PRICE_GENERATION} монет\n"
        f"• Бонусы за пополнение и рефералов\n\n"
        f"👇 Нажмите на кнопку меню, чтобы начать!"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')
    await show_main_menu(update, context)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == 'generate':
        context.user_data.clear()
        context.user_data['waiting_for_prompt'] = True
        await query.edit_message_text(
            "🎨 *Ожидание описания*\n\n"
            "Отправьте ТЕКСТ (на русском или английском) — я создам картинку.\n"
            "Можно также отправить ФОТО + текст — я отредактирую его по вашему запросу.\n\n"
            "❌ /cancel",
            parse_mode='Markdown'
        )
    elif data == 'balance':
        balance = await get_user_balance(user_id)
        free_used = await get_free_generation_status(user_id)
        free_status = "✅ Доступна" if not free_used else "❌ Использована"
        text = (
            f"💰 *Ваш баланс:* {balance} монет\n"
            f"🎁 *Бесплатная генерация:* {free_status}\n\n"
            f"Генерация стоит {PRICE_GENERATION} монет.\n\n"
            f"🔧 *По всем вопросам:* [связь с админом]({ADMIN_CONTACT})"
        )
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data='main_menu')]])
        )
    elif data == 'deposit':
        keyboard = [
            [InlineKeyboardButton("50₽ → 50 монет", callback_data='deposit_50')],
            [InlineKeyboardButton("100₽ → 110 монет", callback_data='deposit_100')],
            [InlineKeyboardButton("200₽ → 220 монет", callback_data='deposit_200')],
            [InlineKeyboardButton("300₽ → 340 монет", callback_data='deposit_300')],
            [InlineKeyboardButton("500₽ → 580 монет", callback_data='deposit_500')],
            [InlineKeyboardButton("1000₽ → 1200 монет", callback_data='deposit_1000')],
            [InlineKeyboardButton("🎁 Бонус за розыгрыш", callback_data='raffle_bonus')],
            [InlineKeyboardButton("🏠 Главное меню", callback_data='main_menu')]
        ]
        await query.edit_message_text(
            "💳 *Пополнение баланса*\n\n"
            "Выберите сумму пополнения (в рублях). После оплаты вы получите бонусные монеты:\n\n"
            "• 50₽ → 50 монет\n"
            "• 100₽ → 110 монет\n"
            "• 200₽ → 220 монет\n"
            "• 300₽ → 340 монет\n"
            "• 500₽ → 580 монет\n"
            "• 1000₽ → 1200 монет\n\n"
            "Или нажмите «Бонус за розыгрыш», если вы оплатили билет в боте «Прозрачный розыгрыш».",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    elif data.startswith('deposit_'):
        amount_map = {
            'deposit_50': (50, 50),
            'deposit_100': (100, 110),
            'deposit_200': (200, 220),
            'deposit_300': (300, 340),
            'deposit_500': (500, 580),
            'deposit_1000': (1000, 1200)
        }
        rub, coins = amount_map.get(data, (0,0))
        if rub == 0:
            return
        context.user_data['deposit_rub'] = rub
        context.user_data['deposit_coins'] = coins
        context.user_data['waiting_for_deposit_screenshot'] = True
        await query.edit_message_text(
            f"💳 *Пополнение на {rub} ₽*\n\n"
            f"💰 Вы получите: {coins} монет.\n\n"
            f"1️⃣ Переведите {rub} ₽ по реквизитам:\n"
            f"`СБП: +7 XXX XXX-XX-XX`\n\n"
            f"2️⃣ После оплаты отправьте СКРИНШОТ чека в этот чат.\n\n"
            f"❌ /cancel",
            parse_mode='Markdown'
        )
    elif data == 'raffle_bonus':
        context.user_data['waiting_for_raffle_screenshot'] = True
        await query.edit_message_text(
            "🎁 *Бонус за розыгрыш*\n\n"
            "Вы оплатили билет в боте «Прозрачный розыгрыш»?\n"
            "Отправьте сюда **скриншот чека** (подтверждение оплаты).\n\n"
            "После проверки администратор начислит вам 50 монет.\n\n"
            "❌ /cancel",
            parse_mode='Markdown'
        )
    elif data == 'referral':
        bot_username = BOT_USERNAME if BOT_USERNAME else (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={user_id}"
        await query.edit_message_text(
            f"🔗 *Ваша реферальная ссылка*\n\n"
            f"Приглашайте друзей по этой ссылке. Когда они пополнят баланс, вы получите +{REFERRAL_BONUS} монет.\n\n"
            f"{ref_link}\n\n"
            f"Поделитесь ссылкой с друзьями!",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data='main_menu')]])
        )
    elif data == 'info':
        await query.edit_message_text(
            f"ℹ️ *Цены и бонусы*\n\n"
            f"🎁 *Первая генерация* — бесплатно для новых пользователей.\n"
            f"🎨 *Стоимость одной генерации:* {PRICE_GENERATION} монет.\n\n"
            f"💰 *Пополнение баланса с бонусами:*\n"
            f"• 50₽ → 50 монет\n"
            f"• 100₽ → 110 монет\n"
            f"• 200₽ → 220 монет\n"
            f"• 300₽ → 340 монет\n"
            f"• 500₽ → 580 монет\n"
            f"• 1000₽ → 1200 монет\n\n"
            f"🎲 *Бонус за розыгрыш:*\n"
            f"Купите билет в боте [Прозрачный розыгрыш]({BOT_LINK}), отправьте чек сюда и получите +{RAFFLE_BONUS} монет.\n\n"
            f"👥 *Реферальная программа:* +{REFERRAL_BONUS} монет за приглашённого, который пополнил баланс.\n\n"
            f"💎 *Монеты нельзя вывести, только тратить на генерацию.*",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data='main_menu')]])
        )
    elif data == 'main_menu':
        context.user_data.clear()
        await show_main_menu(update, context)
    elif data == 'admin_panel' and user_id == ADMIN_ID:
        total_users = await get_total_users()
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute('SELECT COUNT(*) FROM users WHERE free_generation_used = 1')
            free_used_count = (await cur.fetchone())[0] if (await cur.fetchone()) else 0
        pending_deposits = await get_pending_deposits()
        pending_raffles = await get_pending_raffles()
        text = (
            f"🔧 *Админ панель*\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"✅ Бесплатных: {free_used_count}\n"
            f"⏳ Заявок на пополнение: {len(pending_deposits)}\n"
            f"🎲 Заявок на бонус розыгрыша: {len(pending_raffles)}"
        )
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Заявки на пополнение", callback_data='view_deposits')],
                [InlineKeyboardButton("🎁 Заявки на бонус розыгрыша", callback_data='view_raffles')],
                [InlineKeyboardButton("🏠 Главное меню", callback_data='main_menu')]
            ])
        )
    elif data == 'view_deposits' and user_id == ADMIN_ID:
        pending = await get_pending_deposits()
        if not pending:
            await query.edit_message_text("📭 Нет заявок на пополнение", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')]]))
            return
        await query.edit_message_text("📋 Заявки на пополнение отправлены вам в личные сообщения")
        for rid, uid, rub, coins, sid in pending:
            kb = [[InlineKeyboardButton("✅ Подтвердить", callback_data=f'approve_deposit_{rid}'), InlineKeyboardButton("❌ Отклонить", callback_data=f'reject_deposit_{rid}')]]
            try:
                if sid:
                    await context.bot.send_photo(ADMIN_ID, photo=sid, caption=f"📋 Заявка #{rid}\n👤 ID: {uid}\n💰 {rub} ₽ → {coins} монет", reply_markup=InlineKeyboardMarkup(kb))
                else:
                    await context.bot.send_message(ADMIN_ID, text=f"📋 Заявка #{rid}\n👤 ID: {uid}\n💰 {rub} ₽ → {coins} монет", reply_markup=InlineKeyboardMarkup(kb))
            except Exception as e:
                logger.error(f"Ошибка отправки заявки: {e}")
    elif data == 'view_raffles' and user_id == ADMIN_ID:
        pending = await get_pending_raffles()
        if not pending:
            await query.edit_message_text("📭 Нет заявок на бонус розыгрыша", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')]]))
            return
        await query.edit_message_text("🎁 Заявки на бонус розыгрыша отправлены вам в личные сообщения")
        for rid, uid, sid in pending:
            kb = [[InlineKeyboardButton("✅ Начислить 50 монет", callback_data=f'approve_raffle_{rid}'), InlineKeyboardButton("❌ Отклонить", callback_data=f'reject_raffle_{rid}')]]
            try:
                if sid:
                    await context.bot.send_photo(ADMIN_ID, photo=sid, caption=f"🎲 Заявка на бонус #{rid}\n👤 ID: {uid}\nНачислить 50 монет?", reply_markup=InlineKeyboardMarkup(kb))
                else:
                    await context.bot.send_message(ADMIN_ID, text=f"🎲 Заявка на бонус #{rid}\n👤 ID: {uid}\nНачислить 50 монет?", reply_markup=InlineKeyboardMarkup(kb))
            except Exception as e:
                logger.error(f"Ошибка отправки заявки: {e}")
    elif data.startswith('approve_deposit_') and user_id == ADMIN_ID:
        rid = int(data.split('_')[2])
        success = await confirm_deposit(rid)
        if success:
            await query.answer("✅ Пополнение подтверждено", show_alert=True)
            try:
                await query.edit_message_caption(caption=f"✅ ЗАЯВКА #{rid} ПОДТВЕРЖДЕНА")
            except:
                pass
        else:
            await query.answer("❌ Заявка не найдена", show_alert=True)
    elif data.startswith('reject_deposit_') and user_id == ADMIN_ID:
        rid = int(data.split('_')[2])
        await reject_deposit(rid)
        await query.answer("❌ Пополнение отклонено", show_alert=True)
        try:
            await query.edit_message_caption(caption=f"❌ ЗАЯВКА #{rid} ОТКЛОНЕНА")
        except:
            pass
    elif data.startswith('approve_raffle_') and user_id == ADMIN_ID:
        rid = int(data.split('_')[2])
        success = await confirm_raffle(rid)
        if success:
            await query.answer("✅ Бонус начислен", show_alert=True)
            async with aiosqlite.connect(DB_NAME) as db:
                cur = await db.execute('SELECT user_id FROM raffle_requests WHERE request_id = ?', (rid,))
                row = await cur.fetchone()
                if row:
                    try:
                        await application.bot.send_message(row[0], f"🎉 Ваш бонус за розыгрыш одобрен! Вам начислено {RAFFLE_BONUS} монет.")
                    except:
                        pass
            try:
                await query.edit_message_caption(caption=f"✅ ЗАЯВКА #{rid} ПОДТВЕРЖДЕНА, начислено {RAFFLE_BONUS} монет")
            except:
                pass
        else:
            await query.answer("❌ Заявка не найдена", show_alert=True)
    elif data.startswith('reject_raffle_') and user_id == ADMIN_ID:
        rid = int(data.split('_')[2])
        await reject_raffle(rid)
        await query.answer("❌ Бонус отклонён", show_alert=True)
        try:
            await query.edit_message_caption(caption=f"❌ ЗАЯВКА #{rid} ОТКЛОНЕНА")
        except:
            pass

async def handle_deposit_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    rub = context.user_data.get('deposit_rub')
    coins = context.user_data.get('deposit_coins')
    if not rub or not coins:
        await update.message.reply_text("❌ Сначала выберите сумму пополнения через меню 'Пополнить'")
        context.user_data.clear()
        await show_main_menu(update, context)
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Пожалуйста, отправьте скриншот чека")
        return
    photo = update.message.photo[-1]
    request_id = await create_deposit_request(user_id, rub, coins)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE deposit_requests SET screenshot_id = ? WHERE request_id = ?', (photo.file_id, request_id))
        await db.commit()
    kb = [[InlineKeyboardButton("✅ Подтвердить", callback_data=f'approve_deposit_{request_id}'), InlineKeyboardButton("❌ Отклонить", callback_data=f'reject_deposit_{request_id}')]]
    try:
        await context.bot.send_photo(
            ADMIN_ID,
            photo=photo.file_id,
            caption=f"📋 Новая заявка на пополнение #{request_id}\n👤 {first_name}\n🆔 {user_id}\n💰 {rub} ₽ → {coins} монет",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.message.reply_text(f"✅ Заявка #{request_id} отправлена на проверку. Ожидайте зачисления монет.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка отправки заявки: {e}")
    context.user_data.clear()
    await show_main_menu(update, context)

async def handle_raffle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    if not update.message.photo:
        await update.message.reply_text("❌ Пожалуйста, отправьте скриншот чека об оплате билета")
        return
    photo = update.message.photo[-1]
    request_id = await create_raffle_request(user_id, photo.file_id)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE raffle_requests SET screenshot_id = ? WHERE request_id = ?', (photo.file_id, request_id))
        await db.commit()
    kb = [[InlineKeyboardButton("✅ Начислить 50 монет", callback_data=f'approve_raffle_{request_id}'), InlineKeyboardButton("❌ Отклонить", callback_data=f'reject_raffle_{request_id}')]]
    try:
        await context.bot.send_photo(
            ADMIN_ID,
            photo=photo.file_id,
            caption=f"🎁 Новая заявка на бонус розыгрыша #{request_id}\n👤 {first_name}\n🆔 {user_id}\nНачислить 50 монет?",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await update.message.reply_text(f"✅ Заявка #{request_id} отправлена на проверку. При одобрении вы получите 50 монет.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка отправки заявки: {e}")
    context.user_data.clear()
    await show_main_menu(update, context)

async def handle_generation(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, photo_bytes: bytes = None):
    user_id = update.effective_user.id
    can, price, ptype = await can_generate(user_id)
    if not can:
        await update.message.reply_text(f"❌ Недостаточно средств! Нужно {price} монет.\nПополните баланс через меню 'Пополнить'")
        context.user_data.clear()
        await show_main_menu(update, context)
        return
    price_text = "бесплатно 🎁" if ptype in ("free", "admin") else f"{price} монет"
    msg = await update.message.reply_text(f"🎨 Генерирую изображение (Gemini 3 Pro)...\n💰 {price_text}\n⏳ 20–40 секунд")
    img_data, err = await process_image_request(prompt, photo_bytes)
    if img_data:
        await process_generation(user_id)
        await msg.delete()
        if isinstance(img_data, bytes):
            compressed = compress_image(img_data)
            if len(prompt) > 400:
                await update.message.reply_photo(photo=compressed, caption="✨ Результат генерации")
                await update.message.reply_text(f"📝 Ваш запрос:\n{prompt}")
            else:
                await update.message.reply_photo(photo=compressed, caption=f"✨ {prompt}")
        else:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(img_data) as resp:
                    img_bytes = await resp.read()
                    compressed = compress_image(img_bytes)
                    if len(prompt) > 400:
                        await update.message.reply_photo(photo=compressed, caption="✨ Результат генерации")
                        await update.message.reply_text(f"📝 Ваш запрос:\n{prompt}")
                    else:
                        await update.message.reply_photo(photo=compressed, caption=f"✨ {prompt}")
    else:
        await msg.edit_text(f"❌ Ошибка: {err}\n\n💡 Попробуйте написать на английском или упростить запрос")
    context.user_data.clear()
    await show_main_menu(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if context.user_data.get('waiting_for_deposit_screenshot'):
        await handle_deposit_screenshot(update, context)
    elif context.user_data.get('waiting_for_raffle_screenshot'):
        await handle_raffle_screenshot(update, context)
    elif context.user_data.get('waiting_for_prompt'):
        if context.user_data.get('reference_photo'):
            photo_bytes = context.user_data.get('reference_photo')
            context.user_data.pop('reference_photo', None)
            await handle_generation(update, context, text, photo_bytes)
        else:
            await handle_generation(update, context, text)
    else:
        await show_main_menu(update, context)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('waiting_for_deposit_screenshot'):
        await handle_deposit_screenshot(update, context)
    elif context.user_data.get('waiting_for_raffle_screenshot'):
        await handle_raffle_screenshot(update, context)
    elif context.user_data.get('waiting_for_prompt'):
        try:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            photo_bytes = await file.download_as_bytearray()
            context.user_data['reference_photo'] = photo_bytes
            await update.message.reply_text(
                "📸 *Фото получено!*\n\n"
                "Теперь отправьте текстовое описание того, что вы хотите изменить (желательно на английском):\n"
                "• 'make it on a beach with a coconut'\n"
                "• 'turn this into anime style'\n\n"
                "❌ /cancel",
                parse_mode='Markdown'
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка загрузки фото: {e}")
            context.user_data.clear()
            await show_main_menu(update, context)
    else:
        await update.message.reply_text("📸 Сначала нажмите 'Сгенерировать' в меню")
        await show_main_menu(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Действие отменено")
    await show_main_menu(update, context)

def run_bot():
    global application
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async def start_bot():
        global application
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        await init_db()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("cancel", cancel))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        print("=" * 50)
        print("🖼️ ПРОЗРАЧНЫЙ ГЕНЕРАТОР (GEMINI 3 PRO) ЗАПУЩЕН")
        print(f"👑 Админ: {ADMIN_ID}")
        print("=" * 50)
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            await application.stop()
            await application.shutdown()
    try:
        loop.run_until_complete(start_bot())
    except KeyboardInterrupt:
        print("\n❌ Бот остановлен")
    finally:
        loop.close()

if __name__ == '__main__':
    run_bot()

# watermark_id: wm_11_66_e700ce69-68ba-4d2a-b6d3-422c9c8b0615
import asyncio
import logging
import aiohttp
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import hashlib
import sqlite3
from datetime import datetime

try:
    from settings import *
except ImportError:
    print("❌ Создайте файл settings.py с содержимым:")
    print("BOT_TOKEN = 'ваш_токен'")
    print("SUBGRAM_TOKEN = 'ваш_субграм_токен'")
    print("ADMIN_IDS = [ваш_айди]")
    exit()

logging.basicConfig(level=logging.INFO)
router = Router()

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        anonymous_link TEXT UNIQUE,
        created_at TIMESTAMP,
        passed_check BOOLEAN DEFAULT 0
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER,
        receiver_id INTEGER,
        message_text TEXT,
        is_read BOOLEAN DEFAULT 0,
        is_blocked BOOLEAN DEFAULT 0,
        created_at TIMESTAMP,
        FOREIGN KEY (sender_id) REFERENCES users (user_id),
        FOREIGN KEY (receiver_id) REFERENCES users (user_id)
    )
    ''')
    
    conn.commit()
    conn.close()

def add_user(user_id, username, first_name):
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    
    anonymous_link = hashlib.md5(f"{user_id}{datetime.now()}".encode()).hexdigest()[:10]
    
    cursor.execute('''
    INSERT OR IGNORE INTO users (user_id, username, first_name, anonymous_link, created_at)
    VALUES (?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, anonymous_link, datetime.now()))
    
    conn.commit()
    conn.close()

def update_user_check(user_id):
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET passed_check = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_user_by_link(link):
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, first_name FROM users WHERE anonymous_link = ?', (link,))
    result = cursor.fetchone()
    conn.close()
    return {'user_id': result[0], 'first_name': result[1]} if result else None

def get_user_link(user_id):
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT anonymous_link FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def add_message(sender_id, receiver_id, message_text):
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO messages (sender_id, receiver_id, message_text, created_at)
    VALUES (?, ?, ?, ?)
    ''', (sender_id, receiver_id, message_text, datetime.now()))
    conn.commit()
    conn.close()
    return cursor.lastrowid

def get_user_messages(user_id):
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
    SELECT id, message_text, created_at, is_read
    FROM messages 
    WHERE receiver_id = ? AND is_blocked = 0
    ORDER BY created_at DESC
    LIMIT 20
    ''', (user_id,))
    messages = cursor.fetchall()
    conn.close()
    return messages

def get_unread_count(user_id):
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM messages WHERE receiver_id = ? AND is_read = 0 AND is_blocked = 0', (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def mark_as_read(message_id):
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE messages SET is_read = 1 WHERE id = ?', (message_id,))
    conn.commit()
    conn.close()

def block_message(message_id):
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE messages SET is_blocked = 1 WHERE id = ?', (message_id,))
    conn.commit()
    conn.close()

def has_user_passed_check(user_id):
    """Проверяет, прошел ли пользователь проверку SubGram"""
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT passed_check FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

# ========== STATES ==========
class MessageStates(StatesGroup):
    waiting_message = State()

class AdminStates(StatesGroup):
    waiting_broadcast = State()

# ========== SubGram ИНТЕГРАЦИЯ ==========
async def check_subgram(user_id, chat_id, first_name, bot: Bot):
    headers = {
        'Content-Type': 'application/json',
        'Auth': SUBGRAM_TOKEN,
    }
    data = {
        'UserId': user_id,
        'ChatId': chat_id,
        'first_name': first_name,
        'language_code': 'ru'
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post('https://api.subgram.ru/request-op-tokenless/', 
                                  headers=headers, json=data) as response:
                if response.status == 200:
                    response_json = await response.json()
                    status = response_json.get('status', 'ok')
                    
                    if status == 'warning':
                        links = response_json.get("links", [])
                        await show_subgram_channels(chat_id, links, bot)
                        return False
                    elif status == 'gender':
                        await ask_gender(chat_id, bot)
                        return False
                    else:
                        update_user_check(user_id)
                        return True
                else:
                    update_user_check(user_id)
                    return True
    except:
        update_user_check(user_id)
        return True

async def show_subgram_channels(chat_id, links, bot: Bot):
    markup = InlineKeyboardMarkup(inline_keyboard=[])
    
    for i, url in enumerate(links[:3], 1):
        markup.inline_keyboard.append([
            InlineKeyboardButton(text=f'📢 Подписаться {i}', url=url)
        ])
    
    markup.inline_keyboard.append([
        InlineKeyboardButton(text='✅ Я подписался', callback_data='check_subs')
    ])
    
    await bot.send_message(
        chat_id,
        "🎯 <b>Почти готово!</b>\n\n"
        "Подпишись на наши каналы, чтобы получить доступ к боту:\n\n"
        "После подписки нажми кнопку <b>✅ Я подписался</b>",
        parse_mode='HTML',
        reply_markup=markup
    )

async def ask_gender(chat_id, bot: Bot):
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='👱‍♂️ Парень', callback_data='gender_male'),
         InlineKeyboardButton(text='👩‍🦰 Девушка', callback_data='gender_female')]
    ])
    
    await bot.send_message(
        chat_id,
        "🎯 <b>Выбери свой пол:</b>",
        parse_mode='HTML',
        reply_markup=markup
    )

async def send_notification_to_receiver(receiver_id: int, message_text: str, bot: Bot):
    """Отправляет уведомление получателю о новой валентинке"""
    try:
        # Проверяем, прошел ли получатель проверку
        passed = has_user_passed_check(receiver_id)
        
        if not passed:
            # Если не прошел проверку, не отправляем уведомление
            return
        
        # Обрезаем сообщение для предпросмотра
        preview = message_text[:100] + "..." if len(message_text) > 100 else message_text
        
        text = f"""
💌 <b>У тебя новая анонимная валентинка!</b>

📝 {preview}

✨ <i>Перейди в бота, чтобы прочитать сообщение полностью!</i>

👉 Нажми /start в боте @{(await bot.get_me()).username}
        """
        
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📨 Перейти к сообщениям", url=f"https://t.me/{(await bot.get_me()).username}")]
        ])
        
        await bot.send_message(
            receiver_id,
            text,
            parse_mode='HTML',
            reply_markup=markup
        )
        logging.info(f"✅ Уведомление отправлено пользователю {receiver_id}")
        
    except Exception as e:
        logging.error(f"❌ Ошибка отправки уведомления пользователю {receiver_id}: {e}")

# ========== ОСНОВНОЙ ФУНКЦИОНАЛ ==========
async def show_main_menu(user_id: int, bot: Bot):
    """Главное меню"""
    link = get_user_link(user_id)
    bot_username = (await bot.get_me()).username
    full_link = f"https://t.me/{bot_username}?start=anon_{link}"
    unread = get_unread_count(user_id)
    
    text = f"""
💌 <b>АНОНИМНЫЕ ВАЛЕНТИНКИ</b>

🔗 <b>Твоя ссылка:</b>
<code>{full_link}</code>

📬 <b>Новых сообщений:</b> {unread}

🎯 Добавь ссылку в описание профиля и получай анонимные сообщения!
    """
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Мои сообщения", callback_data="my_messages")],
        [InlineKeyboardButton(text="🔗 Поделиться ссылкой", callback_data="share_link")],
        [InlineKeyboardButton(text="💌 Отправить валентинку", callback_data="send_valentine")]
    ])
    
    await bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)

async def show_messages(user_id: int, bot: Bot):
    """Показывает сообщения пользователя"""
    messages = get_user_messages(user_id)
    unread = get_unread_count(user_id)
    
    if not messages:
        text = """
📭 <b>Сообщений пока нет</b>

Поделись своей ссылкой с друзьями!
        """
        
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Получить ссылку", callback_data="share_link")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_main")]
        ])
    else:
        text = f"""
💌 <b>Твои сообщения</b>

📬 <b>Новых:</b> {unread}

Последние сообщения:
        """
        
        for msg in messages[:5]:
            msg_id, msg_text, created_at, is_read = msg
            status = "🆕" if not is_read else "✅"
            date = created_at.split()[0] if created_at else "сегодня"
            preview = msg_text[:50] + "..." if len(msg_text) > 50 else msg_text
            text += f"\n{status} <i>{date}</i>\n<code>{preview}</code>\n"
        
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Читать все", callback_data="view_all")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_main")]
        ])
    
    await bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)

async def show_link(user_id: int, bot: Bot):
    """Показывает ссылку пользователя"""
    link = get_user_link(user_id)
    bot_username = (await bot.get_me()).username
    full_link = f"https://t.me/{bot_username}?start=anon_{link}"
    
    text = f"""
🔗 <b>Твоя ссылка для валентинок</b>

<code>{full_link}</code>

📱 Добавь её в описание профиля:
• Telegram
• Instagram
• TikTok
• Или отправь друзьям!
    """
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Скопировать ссылку", callback_data=f"copy_{full_link}")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_main")]
    ])
    
    await bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup)

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@router.message(CommandStart())
async def start_command(message: Message, bot: Bot, state: FSMContext):
    user = message.from_user
    args = message.text.split()
    
    # Проверяем анонимную ссылку
    if len(args) > 1 and args[1].startswith('anon_'):
        link = args[1][5:]
        receiver = get_user_by_link(link)
        
        if receiver and receiver['user_id'] != user.id:
            # Проверяем, прошел ли отправитель проверку
            passed = has_user_passed_check(user.id)
            
            if not passed:
                # Отправитель не прошел проверку - просим пройти
                await message.answer(
                    "❌ <b>Чтобы отправить валентинку, нужно пройти проверку!</b>\n\n"
                    "Введи команду /start в этом чате, чтобы подписаться на каналы и получить доступ к боту.",
                    parse_mode='HTML'
                )
                return
            
            # Отправитель прошел проверку - переходим к отправке
            await state.update_data(receiver_id=receiver['user_id'])
            await state.set_state(MessageStates.waiting_message)
            
            await message.answer(
                f"💌 <b>Пишем валентинку для {receiver['first_name']}</b>\n\n"
                f"Напиши свое анонимное сообщение:",
                parse_mode='HTML'
            )
            return
    
    # Обычный старт
    add_user(user.id, user.username, user.first_name)
    
    # Проверяем SubGram
    passed = await check_subgram(user.id, message.chat.id, user.first_name, bot)
    
    if passed:
        await show_main_menu(user.id, bot)

@router.message(MessageStates.waiting_message)
async def process_message(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    receiver_id = data.get('receiver_id')
    
    if receiver_id and message.text:
        # Добавляем сообщение в БД
        message_id = add_message(message.from_user.id, receiver_id, message.text)
        
        # Отправляем уведомление получателю
        await send_notification_to_receiver(receiver_id, message.text, bot)
        
        # Получаем имя получателя для ответа
        conn = sqlite3.connect('valentine_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT first_name FROM users WHERE user_id = ?', (receiver_id,))
        receiver_name = cursor.fetchone()
        conn.close()
        
        receiver_name = receiver_name[0] if receiver_name else "пользователь"
        
        await message.answer(
            f"✅ <b>Валентинка отправлена анонимно для {receiver_name}!</b>\n\n"
            "Теперь ты тоже можешь получать валентинки!\n\n"
            "Чтобы пользоваться ботом, введите команду /start",
            parse_mode='HTML'
        )
    
    await state.clear()

# ========== ОБРАБОТЧИКИ КНОПОК ==========
@router.callback_query(F.data == "back_to_main")
async def back_handler(callback: CallbackQuery, bot: Bot):
    await callback.message.delete()
    await show_main_menu(callback.from_user.id, bot)

@router.callback_query(F.data == "my_messages")
async def messages_handler(callback: CallbackQuery, bot: Bot):
    await callback.message.delete()
    await show_messages(callback.from_user.id, bot)

@router.callback_query(F.data == "share_link")
async def share_handler(callback: CallbackQuery, bot: Bot):
    await callback.message.delete()
    await show_link(callback.from_user.id, bot)

@router.callback_query(F.data == "send_valentine")
async def send_handler(callback: CallbackQuery, bot: Bot):
    await callback.message.delete()
    
    await callback.message.answer(
        "💌 <b>Отправить валентинку</b>\n\n"
        "Попроси друга поделиться его ссылкой или отправь валентинку себе по своей ссылке!",
        parse_mode='HTML'
    )

@router.callback_query(F.data.startswith("copy_"))
async def copy_handler(callback: CallbackQuery):
    link = callback.data[5:]
    await callback.answer(f"✅ Ссылка скопирована: {link}", show_alert=True)

@router.callback_query(F.data == "view_all")
async def view_all_handler(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    messages = get_user_messages(user_id)
    
    if not messages:
        await callback.answer("Нет сообщений", show_alert=True)
        return
    
    text = "💌 <b>Все сообщения:</b>\n\n"
    
    for msg in messages:
        msg_id, msg_text, created_at, is_read = msg
        date = created_at.split()[0] if created_at else "сегодня"
        status = "🆕" if not is_read else "✅"
        text += f"{status} <i>{date}</i>\n<code>{msg_text}</code>\n\n"
    
    await callback.message.edit_text(text, parse_mode='HTML')

# ========== SubGram ОБРАБОТЧИКИ ==========
@router.callback_query(F.data == "check_subs")
async def check_subs_handler(callback: CallbackQuery, bot: Bot):
    user = callback.from_user
    passed = await check_subgram(user.id, callback.message.chat.id, user.first_name, bot)
    
    if passed:
        await callback.message.delete()
        await show_main_menu(user.id, bot)
    else:
        await callback.answer("❌ Подпишись на все каналы!", show_alert=True)

@router.callback_query(F.data.startswith("gender_"))
async def gender_handler(callback: CallbackQuery, bot: Bot):
    # Просто пропускаем выбор пола
    update_user_check(callback.from_user.id)
    await callback.message.delete()
    await show_main_menu(callback.from_user.id, bot)

# ========== АДМИН ПАНЕЛЬ ==========
def is_admin(user_id: int) -> bool:
    try:
        return user_id in ADMIN_IDS
    except:
        return False

@router.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return
    
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM messages')
    total_messages = cursor.fetchone()[0]
    conn.close()
    
    text = f"""
👑 <b>Админ панель</b>

📊 <b>Статистика:</b>
👥 Пользователей: {total_users}
💌 Сообщений: {total_messages}

Выберите действие:
    """
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users")],
        [InlineKeyboardButton(text="💌 Последние сообщения", callback_data="admin_messages")]
    ])
    
    await message.answer(text, parse_mode='HTML', reply_markup=markup)

@router.callback_query(F.data.startswith("admin_"))
async def admin_handler(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(callback.from_user.id):
        return
    
    action = callback.data.split("_")[1]
    
    if action == "broadcast":
        await state.set_state(AdminStates.waiting_broadcast)
        
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel")]
        ])
        
        await callback.message.edit_text(
            "📢 <b>Отправьте сообщение для рассылки:</b>\n\n"
            "Все пользователи получат это сообщение.",
            parse_mode='HTML',
            reply_markup=markup
        )
    
    elif action == "users":
        conn = sqlite3.connect('valentine_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, username, first_name, created_at FROM users ORDER BY created_at DESC LIMIT 20')
        users = cursor.fetchall()
        conn.close()
        
        text = "👥 <b>Последние пользователи:</b>\n\n"
        
        for user in users:
            user_id, username, first_name, created_at = user
            date = created_at.split()[0] if created_at else "сегодня"
            text += f"• {first_name} (@{username or 'нет'})\n"
            text += f"  ID: {user_id}\n"
            text += f"  📅 {date}\n\n"
        
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад в админку", callback_data="admin_back")]
        ])
        
        await callback.message.edit_text(text, parse_mode='HTML', reply_markup=markup)
    
    elif action == "messages":
        conn = sqlite3.connect('valentine_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
        SELECT m.message_text, m.created_at, s.first_name as sender, r.first_name as receiver
        FROM messages m
        LEFT JOIN users s ON m.sender_id = s.user_id
        LEFT JOIN users r ON m.receiver_id = r.user_id
        ORDER BY m.created_at DESC
        LIMIT 10
        ''')
        messages = cursor.fetchall()
        conn.close()
        
        text = "💌 <b>Последние сообщения:</b>\n\n"
        
        for msg in messages:
            msg_text, created_at, sender, receiver = msg
            date = created_at.split()[0] if created_at else "сегодня"
            preview = msg_text[:50] + "..." if len(msg_text) > 50 else msg_text
            text += f"📨 {sender} → {receiver}\n"
            text += f"📝 {preview}\n"
            text += f"📅 {date}\n\n"
        
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад в админку", callback_data="admin_back")]
        ])
        
        await callback.message.edit_text(text, parse_mode='HTML', reply_markup=markup)
    
    elif action == "back":
        await admin_command(callback.message)
    
    elif action == "cancel":
        await state.clear()
        await callback.message.edit_text("❌ Рассылка отменена")
    
    await callback.answer()

@router.message(AdminStates.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    
    conn = sqlite3.connect('valentine_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE passed_check = 1')
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        await message.answer("❌ Нет пользователей для рассылки")
        await state.clear()
        return
    
    total = len(users)
    sent = 0
    failed = 0
    
    await message.answer(f"📤 Начинаю рассылку для {total} пользователей...")
    
    for user in users:
        try:
            await bot.send_message(user[0], message.text, parse_mode='HTML')
            sent += 1
        except:
            failed += 1
        
        if (sent + failed) % 10 == 0:
            await message.edit_text(f"📤 Отправлено: {sent + failed}/{total}")
    
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📊 Результаты:\n"
        f"• Всего: {total}\n"
        f"• Отправлено: {sent}\n"
        f"• Не отправлено: {failed}",
        parse_mode='HTML'
    )
    
    await state.clear()

# ========== ЗАПУСК ==========
async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
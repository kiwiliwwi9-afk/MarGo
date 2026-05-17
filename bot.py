import os
import aiohttp
import asyncio
import random
import logging
import threading
import time
import sqlite3
import re
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import xml.etree.ElementTree as ET

# ========== RSS ДЛЯ НОВОСТЕЙ ==========
NEWS_RSS = {
    'us': 'https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml',
    'ru': 'https://meduza.io/rss/all',
    'uk': 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',
    'fr': 'https://rss.nytimes.com/services/xml/rss/nyt/Europe.xml',
    'de': 'https://rss.nytimes.com/services/xml/rss/nyt/Europe.xml',
    'jp': 'https://rss.nytimes.com/services/xml/rss/nyt/AsiaPacific.xml',
    'cn': 'https://rss.nytimes.com/services/xml/rss/nyt/AsiaPacific.xml',
}

async def fetch_news(country='us'):
    url = NEWS_RSS.get(country, NEWS_RSS['us'])
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    root = ET.fromstring(text)
                    items = root.findall('.//item')
                    news = []
                    for item in items[:5]:
                        title = item.find('title').text
                        link = item.find('link').text
                        news.append(f"[{title[:80]}]({link})")
                    return news
    except:
        pass
    return None

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
OPENWEATHER_KEY = os.environ.get("OPENWEATHER_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")

logging.basicConfig(level=logging.INFO)

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('margo.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT DEFAULT 'друг',
        messages INTEGER DEFAULT 0,
        images INTEGER DEFAULT 0,
        games INTEGER DEFAULT 0,
        reminders INTEGER DEFAULT 0,
        premium INTEGER DEFAULT 0,
        created_at TIMESTAMP,
        last_active TIMESTAMP
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        text TEXT,
        remind_at TIMESTAMP,
        created_at TIMESTAMP
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS memory (
        user_id INTEGER,
        role TEXT,
        content TEXT,
        created_at TIMESTAMP
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS poll_answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        answer TEXT,
        created_at TIMESTAMP
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS poll_sent (
        user_id INTEGER PRIMARY KEY,
        sent_at TIMESTAMP
    )
''')
conn.commit()

def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute('''
            INSERT INTO users (user_id, name, created_at, last_active)
            VALUES (?, ?, ?, ?)
        ''', (user_id, "друг", datetime.now(), datetime.now()))
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
    return row

def update_stats(user_id, field):
    cursor.execute(f"UPDATE users SET {field} = {field} + 1, last_active = ? WHERE user_id = ?", (datetime.now(), user_id))
    conn.commit()

def save_memory(user_id, role, content):
    cursor.execute("INSERT INTO memory (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                   (user_id, role, content[:500], datetime.now()))
    conn.commit()
    cursor.execute("DELETE FROM memory WHERE user_id = ? AND created_at < (SELECT created_at FROM memory WHERE user_id = ? ORDER BY created_at DESC LIMIT 1 OFFSET 30)", (user_id, user_id))
    conn.commit()

def get_memory(user_id):
    cursor.execute("SELECT role, content FROM memory WHERE user_id = ? ORDER BY created_at DESC LIMIT 15", (user_id,))
    rows = cursor.fetchall()
    return list(reversed(rows))

def add_reminder(user_id, text, remind_at):
    cursor.execute('''
        INSERT INTO reminders (user_id, text, remind_at, created_at)
        VALUES (?, ?, ?, ?)
    ''', (user_id, text, remind_at, datetime.now()))
    conn.commit()
    return cursor.lastrowid

def get_active_reminders(user_id):
    cursor.execute("SELECT id, text, remind_at FROM reminders WHERE user_id = ? AND remind_at > ? ORDER BY remind_at", 
                   (user_id, datetime.now()))
    return cursor.fetchall()

def delete_reminder(reminder_id, user_id):
    cursor.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id))
    conn.commit()
    return cursor.rowcount > 0

def delete_reminder_by_id(reminder_id):
    cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()

def get_due_reminders():
    cursor.execute("SELECT id, user_id, text FROM reminders WHERE remind_at <= ?", (datetime.now(),))
    return cursor.fetchall()

def parse_reminder_time(text):
    text = text.lower()
    now = datetime.now()
    match = re.search(r'через\s+(\d+)\s*(минут|минуты|минуту|час|часа|часов)', text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if 'час' in unit:
            return now + timedelta(hours=amount)
        return now + timedelta(minutes=amount)
    match = re.search(r'в\s+(\d{1,2}):(\d{2})', text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        remind = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind <= now:
            remind += timedelta(days=1)
        return remind
    match = re.search(r'завтра\s+в\s+(\d{1,2}):(\d{2})', text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        remind = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return remind
    return None

# ========== ПЛАНИРОВЩИК НАПОМИНАНИЙ ==========
reminder_running = False
application = None
loop = None

def check_and_send_reminders():
    due_reminders = get_due_reminders()
    for reminder_id, user_id, text in due_reminders:
        try:
            asyncio.run_coroutine_threadsafe(
                application.bot.send_message(
                    chat_id=user_id, 
                    text=f"⏰ **Напоминание!**\n\n{text}",
                    parse_mode="Markdown"
                ),
                loop
            )
            print(f"✅ Напоминание {reminder_id} отправлено")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
        delete_reminder_by_id(reminder_id)

def run_scheduler():
    global reminder_running
    if reminder_running:
        return
    reminder_running = True
    while True:
        check_and_send_reminders()
        time.sleep(30)

# ========== КЛАВИАТУРА ==========
def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("🌍 Переводчик"), KeyboardButton("⏰ Напомнить")],
        [KeyboardButton("🎮 Игры"), KeyboardButton("💬 Цитата")],
        [KeyboardButton("📊 Профиль"), KeyboardButton("📰 Новости"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_image_keyboard():
    buttons = [
        [KeyboardButton("❌ Отмена"), KeyboardButton("🎨 Картинка")],
        [KeyboardButton("🌤️ Погода"), KeyboardButton("🌍 Переводчик")],
        [KeyboardButton("⏰ Напомнить"), KeyboardButton("📰 Новости")],
        [KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_news_keyboard():
    buttons = [
        [KeyboardButton("🌍 Главные"), KeyboardButton("🇷🇺 Россия")],
        [KeyboardButton("🇺🇸 США"), KeyboardButton("🇬🇧 Великобритания")],
        [KeyboardButton("🇫🇷 Франция"), KeyboardButton("🇩🇪 Германия")],
        [KeyboardButton("🇯🇵 Япония"), KeyboardButton("🇨🇳 Китай")],
        [KeyboardButton("🔙 Назад")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ========== GROQ ==========
async def ask_groq(prompt):
    if not GROQ_KEY:
        return "🔌 Groq не настроен"
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.8
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, json=payload, timeout=15) as r:
                if r.status == 200:
                    data = await r.json()
                    return data['choices'][0]['message']['content']
                return f"❌ Ошибка: {r.status}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

async def ask_groq_with_memory(user_id, prompt):
    history = get_memory(user_id)
    context = ""
    for role, content in history[-6:]:
        context += f"{role}: {content}\n"
    full_prompt = f"""Ты — марGO, живой умный помощник. Отвечай на русском. Учитывай историю разговора.

История:
{context}
Пользователь: {prompt}
марGO:"""
    return await ask_groq(full_prompt)

# ========== КАРТИНКИ ==========
async def generate_image(prompt):
    enhanced = f"masterpiece, best quality, highly detailed, {prompt}"
    seed = random.randint(1, 999999)
    url = f"https://image.pollinations.ai/prompt/{enhanced.replace(' ', '%20')}?width=1024&height=1024&nologo=true&seed={seed}"
    return url

# ========== ПОГОДА ==========
def extract_city_from_text(text):
    text = text.lower()
    text = text.replace("погода", "").strip()
    text = re.sub(r'^(в|во|у|на)\s+', '', text)
    text = re.sub(r'\s+(в|во|у|на)\s+', ' ', text)
    text = re.sub(r'([а-я])е$', r'\1а', text)
    text = re.sub(r'([а-я])у$', r'\1а', text)
    text = re.sub(r'([а-я])ой$', r'\1а', text)
    text = re.sub(r'([а-я])ей$', r'\1а', text)
    return text.strip()

async def get_weather(city):
    if not OPENWEATHER_KEY:
        return "🔌 Погода не настроена"
    city = extract_city_from_text(city)
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_KEY}&units=metric&lang=ru"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status == 200:
                    data = await r.json()
                    temp = round(data['main']['temp'])
                    desc = data['weather'][0]['description']
                    return f"🌤️ {city.capitalize()}: {desc}, {temp}°C"
                return f"❌ Город '{city}' не найден"
    except:
        return "❌ Ошибка погоды"

# ========== ПЕРЕВОДЧИК ==========
async def translate_text(text, target='ru'):
    url = "https://translate.googleapis.com/translate_a/single"
    params = {'client': 'gtx', 'sl': 'auto', 'tl': target, 'dt': 't', 'q': text}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=10) as r:
                if r.status == 200:
                    data = await r.json()
                    return data[0][0][0]
                return None
    except:
        return None

# ========== НОВОСТИ ==========
async def cmd_news(update, context, country=None):
    if country is None:
        text = update.message.text.replace('/news', '').strip().lower()
        countries_map = {
            'россия': 'ru', 'russia': 'ru',
            'сша': 'us', 'usa': 'us', 'америка': 'us',
            'великобритания': 'uk', 'britain': 'uk', 'англия': 'uk',
            'франция': 'fr', 'france': 'fr',
            'германия': 'de', 'germany': 'de',
            'япония': 'jp', 'japan': 'jp',
            'китай': 'cn', 'china': 'cn'
        }
        country_code = countries_map.get(text, 'us')
    else:
        country_code = country
    
    await update.message.reply_text("📰 Загружаю новости...")
    news = await fetch_news(country_code)
    
    if news:
        result = "📰 **Новости:**\n\n"
        for i, item in enumerate(news, 1):
            result += f"{i}. {item}\n"
        await update.message.reply_text(result, parse_mode="Markdown", disable_web_page_preview=True)
    else:
        await update.message.reply_text("❌ Не удалось загрузить новости. Попробуй позже.")

# ========== СТАТИСТИКА ==========
async def cmd_stats(update, context):
    user_id = update.effective_user.id
    stats = get_user(user_id)
    reminders = len(get_active_reminders(user_id))
    await update.message.reply_text(
        f"📊 **Твоя статистика:**\n\n"
        f"💬 Сообщений: {stats[2]}\n"
        f"⏰ Активных напоминаний: {reminders}\n"
        f"🎲 Игр сыграно: {stats[4]}\n"
        f"🎨 Картинок сгенерировано: {stats[3]}\n\n"
        f"📅 Пользователь с: {stats[7][:10] if stats[7] else 'сегодня'}",
        parse_mode="Markdown"
    )

# ========== ЦИТАТЫ ==========
async def cmd_quote(update, context):
    quotes = [
        "💡 Код — это поэзия, которую понимает компьютер.",
        "🚀 Лучший способ предсказать будущее — создать его самому.",
        "🤍 Простота — высшая сложность.",
        "🌍 GO World — твой мир. Твои правила.",
        "🎨 марGO рисует, отвечает, напоминает — всё в одном!"
    ]
    await update.message.reply_text(random.choice(quotes))

# ========== НАПОМИНАНИЯ ==========
async def cmd_remind(update, context):
    user_id = update.effective_user.id
    text = update.message.text.replace('/remind', '').strip()
    if not text:
        await update.message.reply_text("⏰ Напиши: `/remind купить хлеб в 15:30`", parse_mode="Markdown")
        return
    remind_time = parse_reminder_time(text)
    if not remind_time:
        await update.message.reply_text("❌ Не понял время. Формат: `в 15:30` или `через 10 минут`", parse_mode="Markdown")
        return
    reminder_text = re.sub(r'через \d+ минут|в \d{1,2}:\d{2}', '', text).strip()
    if not reminder_text:
        reminder_text = "Напоминание"
    reminder_id = add_reminder(user_id, reminder_text, remind_time)
    time_str = remind_time.strftime("%d.%m.%Y в %H:%M")
    await update.message.reply_text(
        f"✅ **Напоминание создано!**\n\n"
        f"📝 {reminder_text}\n"
        f"⏰ {time_str}\n\n"
        f"ID: {reminder_id}",
        parse_mode="Markdown"
    )
    update_stats(user_id, "reminders")

async def cmd_my_reminders(update, context):
    user_id = update.effective_user.id
    reminders = get_active_reminders(user_id)
    if not reminders:
        await update.message.reply_text("📭 Нет активных напоминаний.", parse_mode="Markdown")
        return
    result = "⏰ **Твои напоминания:**\n\n"
    for rid, text, remind_at in reminders:
        time_str = remind_at.strftime("%d.%m.%Y в %H:%M")
        result += f"**{rid}.** {text}\n   📅 {time_str}\n\n"
    result += "🗑️ Удалить: `/del_remind [номер]`"
    await update.message.reply_text(result, parse_mode="Markdown")

async def cmd_del_remind(update, context):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("❌ Укажи ID: `/del_remind 1`", parse_mode="Markdown")
        return
    try:
        reminder_id = int(context.args[0])
        if delete_reminder(reminder_id, user_id):
            await update.message.reply_text(f"✅ Напоминание {reminder_id} удалено!", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Напоминание {reminder_id} не найдено", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом", parse_mode="Markdown")

# ========== ИГРЫ ==========
async def cmd_dice(update, context):
    user_id = update.effective_user.id
    value = random.randint(1, 6)
    dice_faces = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
    await update.message.reply_text(f"🎲 Тебе выпало: {dice_faces[value-1]} **{value}**", parse_mode="Markdown")
    update_stats(user_id, "games")

async def cmd_coin(update, context):
    user_id = update.effective_user.id
    result = random.choice(["Орёл", "Решка"])
    await update.message.reply_text(f"🪙 **{result}**", parse_mode="Markdown")
    update_stats(user_id, "games")

quiz_questions = [
    {"q": "Сколько планет в Солнечной системе?", "o": ["7", "8", "9", "10"], "a": "8"},
    {"q": "Кто создал Telegram?", "o": ["Илон Маск", "Павел Дуров", "Марк Цукерберг", "Билл Гейтс"], "a": "Павел Дуров"},
    {"q": "Столица Франции?", "o": ["Лондон", "Берлин", "Париж", "Мадрид"], "a": "Париж"},
]
user_quiz = {}

async def cmd_quiz(update, context):
    user_id = update.effective_user.id
    q = random.choice(quiz_questions)
    user_quiz[user_id] = {"question": q["q"], "answer": q["a"], "options": q["o"]}
    options = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q["o"])])
    await update.message.reply_text(f"❓ {q['q']}\n\n{options}\n\nОтветь номером (1-{len(q['o'])})")
    update_stats(user_id, "games")

user_number_game = {}

async def cmd_guess(update, context):
    user_id = update.effective_user.id
    number = random.randint(1, 100)
    user_number_game[user_id] = {"number": number, "attempts": 0}
    await update.message.reply_text("🔢 Я загадал число от 1 до 100. Попробуй угадать! Напиши число.")
    update_stats(user_id, "games")

# ========== ОПРОС ==========
async def send_poll_to_user(user_id):
    questions = [
        "🤍 **Опрос для улучшения марGO**\n\n"
        "Ответь на вопросы (можно по одному, можно сразу):\n\n"
        "1️⃣ Какой последний вопрос ты задавал нейросети и ответ тебя не устроил?\n\n"
        "2️⃣ Что бесит тебя в голосовых помощниках (Алиса, Салют)?\n\n"
        "3️⃣ Что бы ты хотел уметь делать с помощью нейросети, но пока не можешь?\n\n"
        "4️⃣ Какую информацию ты НИКОГДА не передашь нейросети?\n\n"
        "5️⃣ Если бы марGO могла делать одну вещь идеально, что бы это было?\n\n"
        "Напиши ответ в одном сообщении (или частями). Спасибо! 🤍"
    ]
    
    for q in questions:
        try:
            await application.bot.send_message(chat_id=user_id, text=q, parse_mode="Markdown")
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Ошибка отправки опроса {user_id}: {e}")
            return False
    return True

async def send_poll_to_all():
    cursor.execute("SELECT DISTINCT user_id FROM users")
    users = cursor.fetchall()
    
    sent_count = 0
    for (user_id,) in users:
        cursor.execute("SELECT * FROM poll_sent WHERE user_id = ?", (user_id,))
        if cursor.fetchone():
            continue
        
        print(f"Отправляю опрос пользователю {user_id}...")
        success = await send_poll_to_user(user_id)
        
        if success:
            cursor.execute("INSERT INTO poll_sent (user_id, sent_at) VALUES (?, ?)", 
                           (user_id, datetime.now()))
            conn.commit()
            sent_count += 1
        
        await asyncio.sleep(1)
    
    return sent_count

async def cmd_start_poll(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет прав для этой команды.")
        return
    
    await update.message.reply_text("📊 Запускаю опрос для всех пользователей...")
    
    def run_poll():
        sent = asyncio.run(send_poll_to_all())
        asyncio.run_coroutine_threadsafe(
            update.message.reply_text(f"✅ Опрос отправлен {sent} пользователям!"),
            loop
        )
    
    threading.Thread(target=run_poll).start()

async def cmd_check_users(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM poll_sent")
    sent_count = cursor.fetchone()[0]
    
    await update.message.reply_text(
        f"📊 Статистика:\n"
        f"👥 Пользователей в БД: {count}\n"
        f"📨 Опросов отправлено: {sent_count}"
    )

async def cmd_reset_poll(update, context):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет прав для этой команды.")
        return
    
    cursor.execute("DELETE FROM poll_sent")
    conn.commit()
    await update.message.reply_text("✅ История рассылки очищена.")

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
waiting_for_image = {}
waiting_for_city = {}
waiting_for_translate = {}
waiting_for_reminder = {}

async def start(update, context):
    user_id = update.effective_user.id
    
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, name, created_at, last_active)
        VALUES (?, ?, ?, ?)
    ''', (user_id, update.effective_user.first_name, datetime.now(), datetime.now()))
    conn.commit()
    
    waiting_for_image[user_id] = False
    waiting_for_city[user_id] = False
    waiting_for_translate[user_id] = False
    waiting_for_reminder[user_id] = False
    save_memory(user_id, "user", "/start")
    
    await update.message.reply_text(
        "🤍 **Привет! Я марGO — твой умный помощник!**\n\n"
        "🎨 **Картинка** — нажми кнопку и опиши\n"
        "🌤️ **Погода** — нажми кнопку и напиши город\n"
        "🌍 **Переводчик** — нажми кнопку и напиши текст\n"
        "⏰ **Напомнить** — нажми кнопку и напиши время\n"
        "📰 **Новости** — нажми кнопку и выбери страну\n"
        "🎮 **Игры** — /dice, /coin, /quiz, /guess\n"
        "💬 **Цитата** — мудрая фраза\n"
        "📊 **Профиль** — твоя статистика\n\n"
        "❌ **Отмена** — выйти из режима\n\n"
        "Просто задавай вопросы — я отвечу!",
        parse_mode="Markdown",
        reply_markup=get_keyboard()
    )

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    update_stats(user_id, "messages")

    # Обработка ответов на опрос
    cursor.execute("SELECT * FROM poll_sent WHERE user_id = ?", (user_id,))
    has_poll_sent = cursor.fetchone() is not None

    cursor.execute("SELECT * FROM poll_answers WHERE user_id = ?", (user_id,))
    has_answered = cursor.fetchone() is not None

    if has_poll_sent and not has_answered and user_id != ADMIN_ID:
        cursor.execute('''
            INSERT INTO poll_answers (user_id, username, answer, created_at)
            VALUES (?, ?, ?, ?)
        ''', (user_id, update.effective_user.username or "без username", text, datetime.now()))
        conn.commit()
        
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📊 **Новый ответ на опрос!**\n\n"
                 f"👤 @{update.effective_user.username or 'без username'}\n"
                 f"🆔 {user_id}\n\n"
                 f"📝 {text}"
        )
        
        await update.message.reply_text("✅ Спасибо за ответ! Твоё мнение поможет улучшить марGO 🤍")
        return

    if user_id not in waiting_for_image:
        waiting_for_image[user_id] = False
    if user_id not in waiting_for_city:
        waiting_for_city[user_id] = False
    if user_id not in waiting_for_translate:
        waiting_for_translate[user_id] = False
    if user_id not in waiting_for_reminder:
        waiting_for_reminder[user_id] = False

    # Отмена
    if text.lower() == "отмена":
        waiting_for_image[user_id] = False
        waiting_for_city[user_id] = False
        waiting_for_translate[user_id] = False
        waiting_for_reminder[user_id] = False
        await update.message.reply_text("✅ Режим отменён.", reply_markup=get_keyboard())
        return

    # Режим напоминания
    if waiting_for_reminder.get(user_id, False):
        await cmd_remind(update, context)
        waiting_for_reminder[user_id] = False
        return

    # Режим перевода
    if waiting_for_translate.get(user_id, False):
        result = await translate_text(text, 'ru')
        if result:
            await update.message.reply_text(f"🌍 Перевод: {result}")
        else:
            await update.message.reply_text("❌ Не удалось перевести.")
        waiting_for_translate[user_id] = False
        return

    # Новости
    if text == "📰 Новости":
        await update.message.reply_text("📰 **Выбери страну:**", reply_markup=get_news_keyboard())
        return

    if text in ["🌍 Главные", "🇷🇺 Россия", "🇺🇸 США", "🇬🇧 Великобритания", "🇫🇷 Франция", "🇩🇪 Германия", "🇯🇵 Япония", "🇨🇳 Китай"]:
        country_map = {
            "🌍 Главные": 'us',
            "🇷🇺 Россия": 'ru',
            "🇺🇸 США": 'us',
            "🇬🇧 Великобритания": 'uk',
            "🇫🇷 Франция": 'fr',
            "🇩🇪 Германия": 'de',
            "🇯🇵 Япония": 'jp',
            "🇨🇳 Китай": 'cn'
        }
        country_code = country_map.get(text, 'us')
        await update.message.reply_text("📰 Загружаю новости...")
        news = await fetch_news(country_code)
        if news:
            result = "📰 **Новости:**\n\n"
            for i, item in enumerate(news, 1):
                result += f"{i}. {item}\n"
            await update.message.reply_text(result, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            await update.message.reply_text("❌ Не удалось загрузить новости.")
        await update.message.reply_text("🔙 Главное меню:", reply_markup=get_keyboard())
        return

    if text == "🔙 Назад":
        await update.message.reply_text("🔙 Главное меню:", reply_markup=get_keyboard())
        return

    # Картинка
    if text == "🎨 Картинка":
        waiting_for_image[user_id] = True
        await update.message.reply_text(
            "🎨 **Опиши, что нарисовать:**\n\n❌ Отмена — напиши «отмена»",
            parse_mode="Markdown",
            reply_markup=get_image_keyboard()
        )
        return

    if waiting_for_image.get(user_id, False):
        await update.message.reply_text("🎨 Генерирую картинку...")
        image_url = await generate_image(text)
        await update.message.reply_photo(image_url, caption=f"🎨 {text[:100]}")
        update_stats(user_id, "images")
        waiting_for_image[user_id] = False
        return

    # Погода
    if text == "🌤️ Погода":
        waiting_for_city[user_id] = True
        await update.message.reply_text(
            "🌤️ **Напиши название города:**\n\n❌ Отмена — напиши «отмена»",
            parse_mode="Markdown",
            reply_markup=get_image_keyboard()
        )
        return

    if waiting_for_city.get(user_id, False):
        await update.message.reply_text("🌤️ Узнаю погоду...")
        weather = await get_weather(text)
        await update.message.reply_text(weather)
        waiting_for_city[user_id] = False
        return

    # Погода в городе в любом сообщении
    if "погода" in text.lower():
        city_match = re.search(r'погода\s+в\s+([а-яА-ЯёЁa-zA-Z\s-]+)', text.lower())
        if city_match:
            city = city_match.group(1).strip()
            weather = await get_weather(city)
            await update.message.reply_text(weather)
            return

    # Переводчик
    if text == "🌍 Переводчик":
        waiting_for_translate[user_id] = True
        await update.message.reply_text(
            "🌍 **Напиши текст для перевода на русский:**\n\n❌ Отмена — напиши «отмена»",
            parse_mode="Markdown"
        )
        return

    # Напомнить
    if text == "⏰ Напомнить":
        waiting_for_reminder[user_id] = True
        await update.message.reply_text(
            "⏰ **Когда напомнить?**\n\n"
            "• `в 15:30` — сегодня\n"
            "• `завтра в 10:00`\n"
            "• `через 10 минут`\n\n"
            "Напиши время и что напомнить",
            parse_mode="Markdown"
        )
        return

    # Игры
    if text == "🎮 Игры":
        await update.message.reply_text(
            "🎮 **Доступные игры:**\n\n"
            "/dice — бросить кубик\n"
            "/coin — монетка\n"
            "/quiz — викторина\n"
            "/guess — угадай число",
            parse_mode="Markdown"
        )
        return

    if text == "💬 Цитата":
        await cmd_quote(update, context)
        return

    if text == "📊 Профиль":
        await cmd_stats(update, context)
        return

    if text == "❓ Помощь":
        await start(update, context)
        return

    # Обработка ответов на игры
    if user_id in user_quiz:
        try:
            num = int(text)
            q = user_quiz[user_id]
            if 1 <= num <= len(q['options']):
                user_answer = q['options'][num-1]
                if user_answer == q['answer']:
                    await update.message.reply_text("✅ Правильно!")
                else:
                    await update.message.reply_text(f"❌ Неправильно! Ответ: {q['answer']}")
            else:
                await update.message.reply_text(f"Введи число от 1 до {len(q['options'])}")
        except:
            await update.message.reply_text("Напиши номер ответа цифрой")
        del user_quiz[user_id]
        return

    if user_id in user_number_game:
        try:
            guess = int(text)
            game = user_number_game[user_id]
            game["attempts"] += 1
            number = game["number"]
            if guess < number:
                await update.message.reply_text("📈 **Больше!**")
            elif guess > number:
                await update.message.reply_text("📉 **Меньше!**")
            else:
                await update.message.reply_text(f"🎉 Поздравляю! Число {number} за {game['attempts']} попыток!")
                del user_number_game[user_id]
        except:
            await update.message.reply_text("Введи число!")
        return

    # Обычный диалог с Groq
    save_memory(user_id, "user", text)
    await update.message.reply_text("🤔 Думаю...")
    answer = await ask_groq_with_memory(user_id, text)
    save_memory(user_id, "assistant", answer)
    await update.message.reply_text(answer)

# ========== ЗАПУСК ==========
async def main():
    global application, loop
    application = Application.builder().token(TOKEN).build()
    loop = asyncio.get_event_loop()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("quote", cmd_quote))
    application.add_handler(CommandHandler("remind", cmd_remind))
    application.add_handler(CommandHandler("my_reminders", cmd_my_reminders))
    application.add_handler(CommandHandler("del_remind", cmd_del_remind))
    application.add_handler(CommandHandler("dice", cmd_dice))
    application.add_handler(CommandHandler("coin", cmd_coin))
    application.add_handler(CommandHandler("quiz", cmd_quiz))
    application.add_handler(CommandHandler("guess", cmd_guess))
    application.add_handler(CommandHandler("news", cmd_news))
    application.add_handler(CommandHandler("start_poll", cmd_start_poll))
    application.add_handler(CommandHandler("check_users", cmd_check_users))
    application.add_handler(CommandHandler("reset_poll", cmd_reset_poll))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    threading.Thread(target=run_scheduler, daemon=True).start()
    
    print("✅ Бот запущен!")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
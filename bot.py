import os
import aiohttp
import asyncio
import random
import logging
import threading
import time
import requests
import sqlite3
import json
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
conn.commit()

def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute('''
            INSERT INTO users (user_id, created_at, last_active)
            VALUES (?, ?, ?)
        ''', (user_id, datetime.now(), datetime.now()))
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

# ========== ПОГОДА (С ПОДДЕРЖКОЙ СКЛОНЕНИЙ) ==========
def extract_city_from_text(text):
    """Извлекает название города из разных форм запроса"""
    text = text.lower()
    
    # Убираем слово "погода"
    text = text.replace("погода", "").strip()
    
    # Убираем предлоги "в", "во", "у", "на"
    text = re.sub(r'^(в|во|у|на)\s+', '', text)
    text = re.sub(r'\s+(в|во|у|на)\s+', ' ', text)
    
    # Убираем окончания склонений (Москве -> Москва)
    text = re.sub(r'([а-я])е$', r'\1а', text)  # Москве -> Москва
    text = re.sub(r'([а-я])у$', r'\1а', text)  # Москву -> Москва
    text = re.sub(r'([а-я])ой$', r'\1а', text) # Москвой -> Москва
    text = re.sub(r'([а-я])ей$', r'\1а', text) # Россией -> Россия
    
    # Убираем лишние пробелы
    text = text.strip()
    
    return text

async def get_weather(city):
    if not OPENWEATHER_KEY:
        return "🔌 Погода не настроена"
    
    # Извлекаем город из текста
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
    params = {
        'client': 'gtx',
        'sl': 'auto',
        'tl': target,
        'dt': 't',
        'q': text
    }
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

async def process_natural_reminder(update, reminder_text, time_str):
    try:
        hour, minute = map(int, time_str.split(':'))
        now = datetime.now()
        remind_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_time <= now:
            remind_time += timedelta(days=1)
        user_id = update.effective_user.id
        reminder_id = add_reminder(user_id, reminder_text, remind_time)
        time_str_full = remind_time.strftime("%d.%m.%Y в %H:%M")
        await update.message.reply_text(
            f"✅ **Напоминание создано!**\n\n"
            f"📝 {reminder_text}\n"
            f"⏰ {time_str_full}\n\n"
            f"ID: {reminder_id}",
            parse_mode="Markdown"
        )
    except:
        await update.message.reply_text("❌ Неправильный формат времени")

async def process_natural_reminder_minutes(update, reminder_text, minutes):
    user_id = update.effective_user.id
    remind_time = datetime.now() + timedelta(minutes=minutes)
    reminder_id = add_reminder(user_id, reminder_text, remind_time)
    time_str_full = remind_time.strftime("%d.%m.%Y в %H:%M")
    await update.message.reply_text(
        f"✅ **Напоминание создано!**\n\n"
        f"📝 {reminder_text}\n"
        f"⏰ Через {minutes} минут (в {time_str_full})\n\n"
        f"ID: {reminder_id}",
        parse_mode="Markdown"
    )
    update_stats(user_id, "reminders")

async def process_natural_reminder_tomorrow(update, reminder_text, time_str):
    try:
        hour, minute = map(int, time_str.split(':'))
        remind_time = datetime.now() + timedelta(days=1)
        remind_time = remind_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
        user_id = update.effective_user.id
        reminder_id = add_reminder(user_id, reminder_text, remind_time)
        time_str_full = remind_time.strftime("%d.%m.%Y в %H:%M")
        await update.message.reply_text(
            f"✅ **Напоминание создано!**\n\n"
            f"📝 {reminder_text}\n"
            f"⏰ {time_str_full}\n\n"
            f"ID: {reminder_id}",
            parse_mode="Markdown"
        )
    except:
        await update.message.reply_text("❌ Неправильный формат времени")

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

async def cmd_quiz_answer(update, context):
    user_id = update.effective_user.id
    if user_id not in user_quiz:
        return
    try:
        num = int(update.message.text)
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

user_number_game = {}

async def cmd_guess(update, context):
    user_id = update.effective_user.id
    number = random.randint(1, 100)
    user_number_game[user_id] = {"number": number, "attempts": 0}
    await update.message.reply_text("🔢 Я загадал число от 1 до 100. Попробуй угадать! Напиши число.")
    update_stats(user_id, "games")

async def cmd_guess_answer(update, context):
    user_id = update.effective_user.id
    if user_id not in user_number_game:
        return
    try:
        guess = int(update.message.text)
        game = user_number_game[user_id]
        game["attempts"] += 1
        number = game["number"]
        if guess < number:
            await update.message.reply_text("📈 **Больше!** Попробуй ещё.")
        elif guess > number:
            await update.message.reply_text("📉 **Меньше!** Попробуй ещё.")
        else:
            await update.message.reply_text(f"🎉 **Поздравляю!** Ты угадал число {number} за {game['attempts']} попыток!")
            del user_number_game[user_id]
    except ValueError:
        await update.message.reply_text("Введи число!")

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
waiting_for_image = {}
waiting_for_city = {}
waiting_for_translate = {}
waiting_for_reminder = {}

async def start(update, context):
    user_id = update.effective_user.id
    waiting_for_image[user_id] = False
    waiting_for_city[user_id] = False
    waiting_for_translate[user_id] = False
    waiting_for_reminder[user_id] = False
    save_memory(user_id, "user", "/start")
    await update.message.reply_text(
        "🤍 **Привет! Я марGO — твой умный помощник!**\n\n"
        "🎨 **Картинка** — нажми кнопку и опиши\n"
        "🌤️ **Погода** — нажми кнопку и напиши город (или «погода в Москве»)\n"
        "🌍 **Переводчик** — нажми кнопку и напиши текст\n"
        "⏰ **Напомнить** — нажми кнопку и напиши «в 15:30» или «через 10 минут»\n"
        "📰 **Новости** — нажми кнопку и выбери страну\n"
        "🎮 **Игры** — /dice, /coin, /quiz, /guess\n"
        "💬 **Цитата** — мудрая фраза\n"
        "📊 **Профиль** — твоя статистика\n\n"
        "❌ **Отмена** — выйти из режима картинки\n\n"
        "Просто задавай вопросы — я отвечу!",
        parse_mode="Markdown",
        reply_markup=get_keyboard()
    )

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    update_stats(user_id, "messages")

    if user_id not in waiting_for_image:
        waiting_for_image[user_id] = False
    if user_id not in waiting_for_city:
        waiting_for_city[user_id] = False
    if user_id not in waiting_for_translate:
        waiting_for_translate[user_id] = False
    if user_id not in waiting_for_reminder:
        waiting_for_reminder[user_id] = False

    # ===== ОТМЕНА =====
    if text.lower() == "отмена":
        waiting_for_image[user_id] = False
        waiting_for_city[user_id] = False
        waiting_for_translate[user_id] = False
        waiting_for_reminder[user_id] = False
        await update.message.reply_text("✅ Режим отменён.", reply_markup=get_keyboard())
        return

    # ===== РЕЖИМ НАПОМИНАНИЯ =====
    if waiting_for_reminder.get(user_id, False):
        await cmd_remind(update, context)
        waiting_for_reminder[user_id] = False
        return

    # ===== РЕЖИМ ПЕРЕВОДА =====
    if waiting_for_translate.get(user_id, False):
        result = await translate_text(text, 'ru')
        if result:
            await update.message.reply_text(f"🌍 Перевод: {result}")
        else:
            await update.message.reply_text("❌ Не удалось перевести. Попробуй позже.")
        waiting_for_translate[user_id] = False
        return

    # ===== НОВОСТИ =====
    if text == "📰 Новости":
        await update.message.reply_text("📰 **Выбери страну:**", reply_markup=get_news_keyboard())
        return

    if text in ["🌍 Главные", "🇷🇺 Россия", "🇺🇸 США", "🇬🇧 Великобритания", "🇫🇷 Франция", "🇩🇪 Германия", "🇯🇵 Япония", "🇨🇳 Китай"]:
        country_map = {
            "🌍 Главные": "us",
            "🇷🇺 Россия": "ru",
            "🇺🇸 США": "us",
            "🇬🇧 Великобритания": "uk",
            "🇫🇷 Франция": "fr",
            "🇩🇪 Германия": "de",
            "🇯🇵 Япония": "jp",
            "🇨🇳 Китай": "cn"
        }
        await cmd_news(update, context, country_map[text])
        await update.message.reply_text("Главное меню:", reply_markup=get_keyboard())
        return

    if text == "🔙 Назад":
        await update.message.reply_text("Главное меню:", reply_markup=get_keyboard())
        return

    # ===== РЕЖИМ КАРТИНКИ =====
    if waiting_for_image.get(user_id, False):
        await update.message.reply_text("🎨 Рисую...")
        img = await generate_image(text)
        await update.message.reply_photo(img, caption=text)
        waiting_for_image[user_id] = False
        await update.message.reply_text("Меню:", reply_markup=get_keyboard())
        return

    # ===== РЕЖИМ ПОГОДЫ =====
    if waiting_for_city.get(user_id, False):
        weather = await get_weather(text)
        await update.message.reply_text(weather)
        waiting_for_city[user_id] = False
        return

    # ===== КНОПКИ =====
    if text == "🎨 Картинка":
        await update.message.reply_text("🖌️ Опиши что нарисовать\n❌ «отмена» — выйти", reply_markup=get_image_keyboard())
        waiting_for_image[user_id] = True
        return
    if text == "🌤️ Погода":
        await update.message.reply_text("🏙️ Напиши город (например: «Москва» или «погода в Москве»)")
        waiting_for_city[user_id] = True
        return
    if text == "🌍 Переводчик":
        await update.message.reply_text("🌍 Напиши текст для перевода на русский")
        waiting_for_translate[user_id] = True
        return
    if text == "⏰ Напомнить":
        await update.message.reply_text("⏰ Напиши в формате: `в 15:30` или `через 10 минут`", parse_mode="Markdown")
        waiting_for_reminder[user_id] = True
        return
    if text == "🎮 Игры":
        await update.message.reply_text("🎮 /dice — кубик\n/coin — монетка\n/quiz — викторина\n/guess — угадай число")
        return
    if text == "💬 Цитата":
        await cmd_quote(update, context)
        return
    if text == "📊 Профиль":
        await cmd_stats(update, context)
        return
    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Команды:**\n"
            "🎨 Картинка\n🌤️ Погода\n🌍 Переводчик\n⏰ Напомнить\n📰 Новости\n/dice, /coin, /quiz, /guess\n/my_reminders\n/del_remind 1\n\n❌ «отмена»",
            parse_mode="Markdown"
        )
        return

    # ===== ПОГОДА (ЛЮБОЙ ФОРМАТ) =====
    if "погода" in text.lower():
        city_text = text.lower().replace("погода", "").strip()
        city_text = re.sub(r'^(в|во|у|на)\s+', '', city_text)
        if not city_text or city_text in ["в", "во", "у", "на"]:
            await update.message.reply_text("🏙️ Напиши город. Например: «погода в Москве»")
            return
        weather = await get_weather(city_text)
        await update.message.reply_text(weather)
        return

    # ===== БЫСТРЫЕ КОМАНДЫ =====
    if text.lower().startswith("нарисуй"):
        prompt = text[7:].strip()
        if prompt:
            await update.message.reply_text("🎨 Рисую...")
            img = await generate_image(prompt)
            await update.message.reply_photo(img, caption=prompt)
            update_stats(user_id, "images")
        return

    # ===== ЕСТЕСТВЕННЫЕ НАПОМИНАНИЯ =====
    remind_match = re.search(r'напомни мне\s+(.+?)\s+в\s+(\d{1,2}:\d{2})', text.lower())
    if remind_match:
        await process_natural_reminder(update, remind_match.group(1), remind_match.group(2))
        return

    remind_minutes_match = re.search(r'напомни\s+(.+?)\s+через\s+(\d+)\s*(?:минут|минуты|минуту|час|часа|часов)', text.lower())
    if remind_minutes_match:
        minutes = int(remind_minutes_match.group(2))
        await process_natural_reminder_minutes(update, remind_minutes_match.group(1), minutes)
        return

    tomorrow_match = re.search(r'напомнить\s+(.+?)\s+завтра\s+в\s+(\d{1,2}:\d{2})', text.lower())
    if tomorrow_match:
        await process_natural_reminder_tomorrow(update, tomorrow_match.group(1), tomorrow_match.group(2))
        return

    # ===== ОБЫЧНЫЙ ВОПРОС =====
    await update.message.reply_text("💭 Думаю...")
    save_memory(user_id, "user", text)
    answer = await ask_groq_with_memory(user_id, text)
    save_memory(user_id, "assistant", answer)
    await update.message.reply_text(answer)

# ========== ЗАПУСК ==========
global application, loop

def main():
    global application, loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    threading.Thread(target=run_scheduler, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    application = app
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("my_reminders", cmd_my_reminders))
    app.add_handler(CommandHandler("del_remind", cmd_del_remind))
    app.add_handler(CommandHandler("dice", cmd_dice))
    app.add_handler(CommandHandler("coin", cmd_coin))
    app.add_handler(CommandHandler("quiz", cmd_quiz))
    app.add_handler(CommandHandler("guess", cmd_guess))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(MessageHandler(filters.Regex(r'^[1-4]$'), cmd_quiz_answer))
    app.add_handler(MessageHandler(filters.Regex(r'^\d+$'), cmd_guess_answer))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ марGO со всеми функциями запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
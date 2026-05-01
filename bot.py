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
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from pydub import AudioSegment
import xml.etree.ElementTree as ET

# ========== НОВОСТИ ==========
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
    if country == 'ru':
        sources = ['https://meduza.io/rss/all', 'https://ria.ru/export/rss2/index.xml']
        for src in sources:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(src, timeout=10) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            root = ET.fromstring(text)
                            items = root.findall('.//item')
                            news = []
                            for item in items[:5]:
                                title = item.find('title').text
                                link = item.find('link').text
                                news.append(f"[{title[:80]}]({link})")
                            if news:
                                return news
            except:
                continue
        return None
    
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

# ========== ВЕБ-СЕРВЕР И АВТОПИНГ ==========
web_app = Flask(__name__)

@web_app.route('/')
def health():
    return "Бот марGO работает!"

def run_web():
    web_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

def keep_alive():
    url = f"https://{os.environ.get('RENDER_EXTERNAL_URL', 'localhost')}"
    while True:
        try:
            requests.get(url, timeout=10)
            print("✅ Пинг отправлен")
        except:
            pass
        time.sleep(240)

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
OPENWEATHER_KEY = os.environ.get("OPENWEATHER_KEY")
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID")

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
    # Оставляем только последние 30 сообщений
    cursor.execute("DELETE FROM memory WHERE user_id = ? AND created_at < (SELECT created_at FROM memory WHERE user_id = ? ORDER BY created_at DESC LIMIT 1 OFFSET 30)", (user_id, user_id))
    conn.commit()

def get_memory(user_id):
    cursor.execute("SELECT role, content FROM memory WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (user_id,))
    rows = cursor.fetchall()
    return list(reversed(rows))

# ========== КЛАВИАТУРА ==========
def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("🌍 Переводчик"), KeyboardButton("⏰ Напомнить")],
        [KeyboardButton("📰 Новости"), KeyboardButton("🎮 Игры")],
        [KeyboardButton("📊 Профиль"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ========== GROQ (УМНЫЙ) ==========
async def ask_groq(prompt):
    if not GROQ_KEY:
        return "🔌 Groq не настроен"
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1000,
        "temperature": 0.8
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, json=payload, timeout=20) as r:
                if r.status == 200:
                    data = await r.json()
                    return data['choices'][0]['message']['content']
                return f"❌ Ошибка: {r.status}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

async def ask_groq_with_memory(user_id, prompt):
    history = get_memory(user_id)
    context = ""
    for role, content in history:
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
    return f"https://image.pollinations.ai/prompt/{enhanced.replace(' ', '%20')}?width=1024&height=1024&nologo=true&seed={seed}"

# ========== ПОГОДА ==========
async def get_weather(city):
    if not OPENWEATHER_KEY:
        return "🔌 Погода не настроена"
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

# ========== НОВОСТИ ==========
async def cmd_news(update, context, country=None):
    if country is None:
        text = update.message.text.replace('/news', '').strip().lower()
        countries_map = {
            'россия': 'ru', 'russia': 'ru',
            'сша': 'us', 'usa': 'us', 'америка': 'us',
            'великобритания': 'uk', 'britain': 'uk',
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
        await update.message.reply_text("❌ Не удалось загрузить новости.")

# ========== ПЕРЕВОДЧИК ==========
async def translate(text, target='ru'):
    url = "https://translate.googleapis.com/translate_a/single"
    params = {'client': 'gtx', 'sl': 'auto', 'tl': target, 'dt': 't', 'q': text}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=10) as r:
                if r.status == 200:
                    data = await r.json()
                    return data[0][0][0]
    except:
        pass
    return None

# ========== НАПОМИНАНИЯ ==========
def parse_time(text):
    now = datetime.now()
    match = re.search(r'через\s+(\d+)\s*(минут|минуты|минуту|час|часа|часов)', text.lower())
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
    return None

# ========== ПЛАНИРОВЩИК ==========
def check_reminders():
    cursor.execute("SELECT id, user_id, text FROM reminders WHERE remind_at <= ?", (datetime.now(),))
    reminders = cursor.fetchall()
    for rid, uid, text in reminders:
        try:
            asyncio.run_coroutine_threadsafe(
                application.bot.send_message(chat_id=uid, text=f"⏰ **Напоминание!**\n\n{text}", parse_mode="Markdown"),
                loop
            )
        except:
            pass
        cursor.execute("DELETE FROM reminders WHERE id = ?", (rid,))
    conn.commit()

def run_scheduler():
    while True:
        check_reminders()
        time.sleep(30)

# ========== КОМАНДЫ ==========
async def start(update, context):
    user_id = update.effective_user.id
    user = get_user(user_id)
    name = update.effective_user.first_name or "друг"
    await update.message.reply_text(
        f"🤍 **Привет, {name}! Я марGO — твой супер-помощник!**\n\n"
        f"🎨 **Картинка** — нажми кнопку\n"
        f"🌤️ **Погода** — нажми кнопку\n"
        f"🌍 **Переводчик** — нажми кнопку\n"
        f"⏰ **Напомнить** — «напомни мне купить хлеб в 15:30»\n"
        f"📰 **Новости** — нажми кнопку\n"
        f"🎮 **Игры** — /dice, /coin, /quiz\n"
        f"📊 **Профиль** — твоя статистика\n\n"
        f"Просто задавай вопросы — я отвечу!",
        parse_mode="Markdown",
        reply_markup=get_keyboard()
    )

async def cmd_profile(update, context):
    user_id = update.effective_user.id
    user = get_user(user_id)
    await update.message.reply_text(
        f"📊 **Твой профиль:**\n\n"
        f"💬 Сообщений: {user[2]}\n"
        f"🎨 Картинок: {user[3]}\n"
        f"🎮 Игр: {user[4]}\n"
        f"⏰ Напоминаний: {user[5]}\n"
        f"👑 Премиум: {'✅' if user[6] else '❌'}\n"
        f"📅 Впервые: {user[7][:10] if user[7] else 'сегодня'}",
        parse_mode="Markdown"
    )

async def cmd_remind(update, context):
    user_id = update.effective_user.id
    text = update.message.text.replace('/remind', '').strip()
    if not text:
        await update.message.reply_text("⏰ Напиши: `/remind купить хлеб в 15:30`", parse_mode="Markdown")
        return
    remind_time = parse_time(text)
    if not remind_time:
        await update.message.reply_text("❌ Не понял время. Формат: `в 15:30` или `через 10 минут`", parse_mode="Markdown")
        return
    reminder_text = re.sub(r'через \d+ минут|в \d{1,2}:\d{2}', '', text).strip()
    if not reminder_text:
        reminder_text = "Напоминание"
    cursor.execute("INSERT INTO reminders (user_id, text, remind_at, created_at) VALUES (?, ?, ?, ?)",
                   (user_id, reminder_text, remind_time, datetime.now()))
    conn.commit()
    update_stats(user_id, "reminders")
    await update.message.reply_text(f"✅ Напомню в {remind_time.strftime('%H:%M')}: {reminder_text}")

async def cmd_dice(update, context):
    user_id = update.effective_user.id
    value = random.randint(1, 6)
    dice_faces = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
    await update.message.reply_text(f"🎲 {dice_faces[value-1]} Выпало: **{value}**", parse_mode="Markdown")
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

# ========== ОБРАБОТЧИК КАРТИНОК С НОВОСТЯМИ ==========
waiting_for = {}

async def handle_photo(update, context):
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    file = await photo.get_file()
    path = f"temp_{user_id}.jpg"
    await file.download_to_drive(path)
    await update.message.reply_text("📸 Распознаю текст...")
    # Упрощённое распознавание (без OCR для скорости)
    await update.message.reply_text("❌ Распознавание текста с фото временно отключено. Используй текстовый ввод.")
    os.remove(path)

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    update_stats(user_id, "messages")

    if waiting_for.get(user_id) == "image":
        await update.message.reply_text("🎨 Рисую...")
        img = await generate_image(text)
        await update.message.reply_photo(img, caption=text)
        update_stats(user_id, "images")
        waiting_for[user_id] = None
        return

    if waiting_for.get(user_id) == "city":
        weather = await get_weather(text)
        await update.message.reply_text(weather)
        waiting_for[user_id] = None
        return

    if waiting_for.get(user_id) == "translate":
        result = await translate(text)
        if result:
            await update.message.reply_text(f"🌍 Перевод: {result}")
        else:
            await update.message.reply_text("❌ Ошибка перевода")
        waiting_for[user_id] = None
        return

    # Кнопки
    if text == "🎨 Картинка":
        await update.message.reply_text("🖌️ Опиши что нарисовать")
        waiting_for[user_id] = "image"
        return
    if text == "🌤️ Погода":
        await update.message.reply_text("🏙️ Напиши город")
        waiting_for[user_id] = "city"
        return
    if text == "🌍 Переводчик":
        await update.message.reply_text("🌍 Напиши текст для перевода на русский")
        waiting_for[user_id] = "translate"
        return
    if text == "⏰ Напомнить":
        await cmd_remind(update, context)
        return
    if text == "📰 Новости":
        keyboard = [
            [KeyboardButton("🌍 Главные"), KeyboardButton("🇷🇺 Россия")],
            [KeyboardButton("🇺🇸 США"), KeyboardButton("🇬🇧 Великобритания")],
            [KeyboardButton("🇫🇷 Франция"), KeyboardButton("🇩🇪 Германия")],
            [KeyboardButton("🇯🇵 Япония"), KeyboardButton("🇨🇳 Китай")],
            [KeyboardButton("🔙 Назад")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("📰 **Выбери страну:**", parse_mode="Markdown", reply_markup=reply_markup)
        return
    
    # Выбор страны для новостей
    if text == "🌍 Главные":
        await cmd_news(update, context, 'us')
        await update.message.reply_text("Главное меню:", reply_markup=get_keyboard())
        return
    if text == "🇷🇺 Россия":
        await cmd_news(update, context, 'ru')
        await update.message.reply_text("Главное меню:", reply_markup=get_keyboard())
        return
    if text == "🇺🇸 США":
        await cmd_news(update, context, 'us')
        await update.message.reply_text("Главное меню:", reply_markup=get_keyboard())
        return
    if text == "🇬🇧 Великобритания":
        await cmd_news(update, context, 'uk')
        await update.message.reply_text("Главное меню:", reply_markup=get_keyboard())
        return
    if text == "🇫🇷 Франция":
        await cmd_news(update, context, 'fr')
        await update.message.reply_text("Главное меню:", reply_markup=get_keyboard())
        return
    if text == "🇩🇪 Германия":
        await cmd_news(update, context, 'de')
        await update.message.reply_text("Главное меню:", reply_markup=get_keyboard())
        return
    if text == "🇯🇵 Япония":
        await cmd_news(update, context, 'jp')
        await update.message.reply_text("Главное меню:", reply_markup=get_keyboard())
        return
    if text == "🇨🇳 Китай":
        await cmd_news(update, context, 'cn')
        await update.message.reply_text("Главное меню:", reply_markup=get_keyboard())
        return
    if text == "🔙 Назад":
        await update.message.reply_text("Главное меню:", reply_markup=get_keyboard())
        return

    if text == "🎮 Игры":
        await update.message.reply_text("🎮 `/dice` — кубик\n/coin — монетка\n/quiz — викторина", parse_mode="Markdown")
        return
    if text == "📊 Профиль":
        await cmd_profile(update, context)
        return
    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Команды:**\n"
            "🎨 Картинка\n"
            "🌤️ Погода\n"
            "🌍 Перевод\n"
            "⏰ Напомнить\n"
            "📰 Новости\n"
            "/dice, /coin, /quiz\n"
            "📊 Профиль",
            parse_mode="Markdown"
        )
        return

    # Быстрые команды
    if text.lower().startswith("нарисуй"):
        prompt = text[7:].strip()
        if prompt:
            await update.message.reply_text("🎨 Рисую...")
            img = await generate_image(prompt)
            await update.message.reply_photo(img, caption=prompt)
            update_stats(user_id, "images")
        return
    if text.lower().startswith("погода в"):
        city = text[8:].strip()
        weather = await get_weather(city)
        await update.message.reply_text(weather)
        return

    # Умный ответ с памятью
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
    
    # Запускаем веб-сервер для автопинга
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    
    # Запускаем планировщик напоминаний
    threading.Thread(target=run_scheduler, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    application = app
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("dice", cmd_dice))
    app.add_handler(CommandHandler("coin", cmd_coin))
    app.add_handler(CommandHandler("quiz", cmd_quiz))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(MessageHandler(filters.Regex(r'^[1-4]$'), cmd_quiz_answer))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ марGO с новостями и автопингом запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
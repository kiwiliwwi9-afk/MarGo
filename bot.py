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

# ========== RSS ДЛЯ НОВОСТЕЙ ПО СТРАНАМ ==========
NEWS_RSS = {
    'ru': 'https://lenta.ru/rss',
    'us': 'https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml',
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
    except Exception as e:
        print(f"Ошибка RSS {country}: {e}")
        return None

# ========== ВЕБ-СЕРВЕР ==========
web_app = Flask(__name__)

@web_app.route('/')
def health():
    return "Бот марGO работает!"

def run_web():
    web_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

# ========== АВТОПИНГ ==========
def keep_alive():
    url = f"https://{os.environ.get('RENDER_EXTERNAL_URL', 'localhost')}"
    while True:
        try:
            requests.get(url, timeout=10)
            print("✅ Пинг отправлен")
        except:
            pass
        time.sleep(240)

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('margo.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        history TEXT,
        stats TEXT,
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
conn.commit()

def get_user_stats(user_id):
    cursor.execute("SELECT stats FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row and row[0]:
        return json.loads(row[0])
    return {"messages": 0, "reminders": 0, "games": 0, "images": 0}

def update_user_stats(user_id, key):
    stats = get_user_stats(user_id)
    stats[key] = stats.get(key, 0) + 1
    cursor.execute("UPDATE users SET stats = ? WHERE user_id = ?", (json.dumps(stats), user_id))
    conn.commit()

def get_user_history(user_id):
    cursor.execute("SELECT history FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row and row[0]:
        return json.loads(row[0])
    return []

def save_user_history(user_id, history):
    if len(history) > 20:
        history = history[-20:]
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, history, stats, last_active)
        VALUES (?, ?, ?, ?)
    ''', (user_id, json.dumps(history), json.dumps(get_user_stats(user_id)), datetime.now()))
    conn.commit()

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

def get_due_reminders():
    cursor.execute("SELECT id, user_id, text FROM reminders WHERE remind_at <= ?", (datetime.now(),))
    return cursor.fetchall()

def delete_reminder_by_id(reminder_id):
    cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()

def parse_reminder_time(text):
    text = text.lower()
    now = datetime.now()
    
    match = re.search(r'через\s+(\d+)\s*(минут|минуты|минуту|час|часов|часа)', text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if 'час' in unit:
            return now + timedelta(hours=amount)
        else:
            return now + timedelta(minutes=amount)
    
    match = re.search(r'в\s+(\d{1,2}):(\d{2})', text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        remind_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_time <= now:
            remind_time += timedelta(days=1)
        return remind_time
    
    match = re.search(r'завтра\s+в\s+(\d{1,2}):(\d{2})', text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        remind_time = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return remind_time
    
    return None

# ========== ПЛАНИРОВЩИК ==========
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

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
OPENWEATHER_KEY = os.environ.get("OPENWEATHER_KEY")
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID")

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")

logging.basicConfig(level=logging.INFO)

global application
global loop

def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("🌍 Переводчик"), KeyboardButton("⏰ Напомнить")],
        [KeyboardButton("📰 Новости"), KeyboardButton("🎮 Игры")],
        [KeyboardButton("📊 Статистика"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

async def ask_groq(prompt):
    if not GROQ_KEY:
        return "🔌 Groq не настроен"
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 600,
        "temperature": 0.7
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

async def ask_groq_with_memory(prompt, history):
    context = ""
    if history:
        recent = history[-4:]
        for msg in recent:
            role = "Пользователь" if msg['role'] == 'user' else "Ты"
            context += f"{role}: {msg['content']}\n"
    full_prompt = f"""Отвечай ТОЛЬКО на русском языке. Никаких других языков, только русский.

{context}
Пользователь: {prompt}
марGO:"""
    return await ask_groq(full_prompt)

# ========== КАРТИНКИ ==========
async def generate_image(prompt):
    enhanced = f"masterpiece, best quality, {prompt}"
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

# ========== ПЕРЕВОДЧИК ==========
async def translate_text(text, target_lang='ru'):
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        'client': 'gtx',
        'sl': 'auto',
        'tl': target_lang,
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

async def cmd_translate(update, context):
    text = update.message.text.replace('/translate', '').strip()
    if not text:
        await update.message.reply_text("🌍 **Переводчик**\n\n/t translate текст — переведу на русский\n/t en текст — на английский", parse_mode="Markdown")
        return
    
    parts = text.split(' ', 1)
    if len(parts) == 2 and len(parts[0]) == 2:
        target_lang = parts[0]
        text_to_translate = parts[1]
    else:
        target_lang = 'ru'
        text_to_translate = text
    
    result = await translate_text(text_to_translate, target_lang)
    if result:
        await update.message.reply_text(f"🌍 **Перевод:**\n{result}", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Не удалось перевести")

# ========== НОВОСТИ ПО СТРАНАМ ==========
async def cmd_news(update, context):
    text = update.message.text.replace('/news', '').strip().lower()
    
    countries = {
        'ru': 'россия', 'russia': 'россия',
        'us': 'сша', 'usa': 'сша', 'america': 'сша',
        'uk': 'великобритания', 'britain': 'великобритания',
        'fr': 'франция', 'france': 'франция',
        'de': 'германия', 'germany': 'германия',
        'jp': 'япония', 'japan': 'япония',
        'cn': 'китай', 'china': 'китай',
    }
    
    country_code = 'us'
    for code, name in countries.items():
        if name in text:
            country_code = code
            break
    
    news = await fetch_news(country_code)
    if news:
        country_name = countries.get(country_code, 'Мир')
        result = f"📰 **Новости {country_name.capitalize()}:**\n\n"
        for i, item in enumerate(news, 1):
            result += f"{i}. {item}\n"
        await update.message.reply_text(result, parse_mode="Markdown", disable_web_page_preview=True)
    else:
        await update.message.reply_text("❌ Не удалось загрузить новости")

# ========== СТАТИСТИКА ==========
async def cmd_stats(update, context):
    user_id = update.effective_user.id
    stats = get_user_stats(user_id)
    reminders = len(get_active_reminders(user_id))
    
    await update.message.reply_text(
        f"📊 **Твоя статистика:**\n\n"
        f"💬 Сообщений: {stats.get('messages', 0)}\n"
        f"⏰ Напоминаний: {reminders}\n"
        f"🎮 Игр: {stats.get('games', 0)}\n"
        f"🎨 Картинок: {stats.get('images', 0)}",
        parse_mode="Markdown"
    )

# ========== ИГРЫ ==========
async def cmd_dice(update, context):
    user_id = update.effective_user.id
    value = random.randint(1, 6)
    dice_faces = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
    await update.message.reply_text(f"🎲 {dice_faces[value-1]} **{value}**", parse_mode="Markdown")
    update_user_stats(user_id, "games")

async def cmd_coin(update, context):
    user_id = update.effective_user.id
    result = random.choice(["Орёл", "Решка"])
    await update.message.reply_text(f"🪙 **{result}**", parse_mode="Markdown")
    update_user_stats(user_id, "games")

quiz_questions = [
    {"question": "Сколько планет в Солнечной системе?", "options": ["7", "8", "9", "10"], "answer": "8"},
    {"question": "Кто создал Telegram?", "options": ["Илон Маск", "Павел Дуров", "Марк Цукерберг", "Билл Гейтс"], "answer": "Павел Дуров"},
    {"question": "Столица Франции?", "options": ["Лондон", "Берлин", "Париж", "Мадрид"], "answer": "Париж"},
]

user_quiz = {}

async def cmd_quiz(update, context):
    user_id = update.effective_user.id
    q = random.choice(quiz_questions)
    user_quiz[user_id] = {"question": q["question"], "answer": q["answer"], "options": q["options"]}
    options = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(q["options"])])
    await update.message.reply_text(f"❓ {q['question']}\n\n{options}\n\n_Ответь номером_", parse_mode="Markdown")
    update_user_stats(user_id, "games")

async def cmd_quiz_answer(update, context):
    user_id = update.effective_user.id
    if user_id not in user_quiz:
        return
    try:
        answer_num = int(update.message.text)
        q = user_quiz[user_id]
        options = q["options"]
        correct = q["answer"]
        if 1 <= answer_num <= len(options):
            if options[answer_num - 1] == correct:
                await update.message.reply_text("✅ Правильно!", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"❌ Неправильно! Ответ: {correct}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"Введи число от 1 до {len(options)}")
    except ValueError:
        await update.message.reply_text("Напиши номер цифрой")
    del user_quiz[user_id]

user_number_game = {}

async def cmd_guess(update, context):
    user_id = update.effective_user.id
    number = random.randint(1, 100)
    user_number_game[user_id] = {"number": number, "attempts": 0}
    await update.message.reply_text("🔢 Я загадал число от 1 до 100. Угадай!")
    update_user_stats(user_id, "games")

async def cmd_guess_answer(update, context):
    user_id = update.effective_user.id
    if user_id not in user_number_game:
        return
    try:
        guess = int(update.message.text)
        game = user_number_game[user_id]
        game["attempts"] += 1
        if guess < game["number"]:
            await update.message.reply_text("📈 Больше!")
        elif guess > game["number"]:
            await update.message.reply_text("📉 Меньше!")
        else:
            await update.message.reply_text(f"🎉 Угадал! Число {game['number']} за {game['attempts']} попыток!")
            del user_number_game[user_id]
    except ValueError:
        await update.message.reply_text("Введи число!")

# ========== НАПОМИНАНИЯ ==========
async def cmd_remind(update, context):
    user_id = update.effective_user.id
    text = update.message.text.replace('/remind', '').strip()
    if not text:
        await update.message.reply_text("⏰ /remind текст через 10 минут\n/remind текст в 15:30\n/remind текст завтра в 9:00", parse_mode="Markdown")
        return
    
    remind_time = parse_reminder_time(text)
    if not remind_time:
        await update.message.reply_text("❌ Не понял время", parse_mode="Markdown")
        return
    
    reminder_text = re.sub(r'через \d+ минут|в \d{1,2}:\d{2}|завтра в \d{1,2}:\d{2}', '', text).strip()
    if not reminder_text:
        reminder_text = "Напоминание"
    
    reminder_id = add_reminder(user_id, reminder_text, remind_time)
    await update.message.reply_text(f"✅ Напоминание {reminder_id}: {reminder_text}\n⏰ {remind_time.strftime('%d.%m.%Y %H:%M')}", parse_mode="Markdown")
    update_user_stats(user_id, "reminders")

async def cmd_my_reminders(update, context):
    user_id = update.effective_user.id
    reminders = get_active_reminders(user_id)
    if not reminders:
        await update.message.reply_text("📭 Нет напоминаний", parse_mode="Markdown")
        return
    result = "⏰ **Твои напоминания:**\n\n"
    for rid, text, remind_at in reminders:
        result += f"**{rid}.** {text}\n   📅 {remind_at.strftime('%d.%m.%Y %H:%M')}\n\n"
    result += "🗑️ /del_remind [ID]"
    await update.message.reply_text(result, parse_mode="Markdown")

async def cmd_del_remind(update, context):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("❌ /del_remind 1", parse_mode="Markdown")
        return
    try:
        reminder_id = int(context.args[0])
        if delete_reminder(reminder_id, user_id):
            await update.message.reply_text(f"✅ Напоминание {reminder_id} удалено!", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Не найдено", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом", parse_mode="Markdown")

async def process_natural_reminder(update, reminder_text, time_str):
    hour, minute = map(int, time_str.split(':'))
    now = datetime.now()
    remind_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if remind_time <= now:
        remind_time += timedelta(days=1)
    user_id = update.effective_user.id
    reminder_id = add_reminder(user_id, reminder_text, remind_time)
    await update.message.reply_text(f"✅ Напоминание {reminder_id}: {reminder_text}\n⏰ {remind_time.strftime('%d.%m.%Y %H:%M')}", parse_mode="Markdown")

async def process_natural_reminder_minutes(update, reminder_text, minutes):
    user_id = update.effective_user.id
    remind_time = datetime.now() + timedelta(minutes=minutes)
    reminder_id = add_reminder(user_id, reminder_text, remind_time)
    await update.message.reply_text(f"✅ Напоминание {reminder_id}: {reminder_text}\n⏰ Через {minutes} мин", parse_mode="Markdown")

async def process_natural_reminder_tomorrow(update, reminder_text, time_str):
    hour, minute = map(int, time_str.split(':'))
    remind_time = datetime.now() + timedelta(days=1)
    remind_time = remind_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
    user_id = update.effective_user.id
    reminder_id = add_reminder(user_id, reminder_text, remind_time)
    await update.message.reply_text(f"✅ Напоминание {reminder_id}: {reminder_text}\n⏰ {remind_time.strftime('%d.%m.%Y %H:%M')}", parse_mode="Markdown")

# ========== РАСПОЗНАВАНИЕ ГОЛОСА ==========
async def recognize_speech_yandex(file_path):
    if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        return None
    try:
        audio = AudioSegment.from_ogg(file_path)
        wav_path = file_path.replace('.ogg', '.wav')
        audio.export(wav_path, format='wav', parameters=["-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1"])
        url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
        headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
        with open(wav_path, 'rb') as f:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, headers=headers, data=f, timeout=15) as r:
                    os.remove(wav_path)
                    if r.status == 200:
                        data = await r.json()
                        return data.get('result')
        return None
    except:
        return None

async def handle_voice(update, context):
    user_id = update.effective_user.id
    voice = update.message.voice
    file = await voice.get_file()
    file_path = f"temp_voice_{user_id}.ogg"
    await file.download_to_drive(file_path)
    await update.message.reply_text("🎤 Распознаю голос...")
    recognized_text = await recognize_speech_yandex(file_path)
    if recognized_text:
        await update.message.reply_text(f"📝 {recognized_text[:500]}")
        answer = await ask_groq(recognized_text)
        await update.message.reply_text(answer)
    else:
        await update.message.reply_text("❌ Не распознано")
    os.remove(file_path)

# ========== OCR ==========
async def recognize_text_from_photo(file_path):
    url = "https://api.ocr.space/parse/image"
    with open(file_path, 'rb') as f:
        files = {'file': f}
        data = {'language': 'rus', 'isOverlayRequired': False, 'scale': True, 'OCREngine': 2, 'detectOrientation': True}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, data=data, files=files, timeout=30) as r:
                    if r.status == 200:
                        result = await r.json()
                        if result.get('ParsedResults') and len(result['ParsedResults']) > 0:
                            text = result['ParsedResults'][0]['ParsedText']
                            if text and len(text.strip()) > 3:
                                return text.strip()
        except:
            pass
        return None

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
waiting_for_image = {}
waiting_for_city = {}

async def start(update, context):
    user_id = update.effective_user.id
    waiting_for_image[user_id] = False
    waiting_for_city[user_id] = False
    save_user_history(user_id, [])
    if not get_user_stats(user_id):
        cursor.execute("INSERT OR REPLACE INTO users (user_id, stats, last_active) VALUES (?, ?, ?)", 
                       (user_id, json.dumps({"messages": 0, "reminders": 0, "games": 0, "images": 0}), datetime.now()))
        conn.commit()
    await update.message.reply_text(
        "🤍 **Привет! Я марGO**\n\n"
        "🎨 Картинка — нажми кнопку\n"
        "🌤️ Погода — нажми кнопку\n"
        "🌍 Переводчик — нажми кнопку\n"
        "⏰ Напомнить — «напомни мне...»\n"
        "📰 Новости — /news или /news россия\n"
        "🎮 Игры — /dice, /coin, /quiz, /guess\n"
        "📊 Статистика — /stats\n"
        "📸 Фото с текстом — отправь фото\n"
        "🎤 Голос — отправь голосовое\n\n"
        "📋 /my_reminders — список напоминаний\n"
        "🗑️ /del_remind 1 — удалить\n\n"
        "Просто задавай вопросы!",
        parse_mode="Markdown",
        reply_markup=get_keyboard()
    )

async def handle_photo(update, context):
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_path = f"temp_{user_id}.jpg"
    await file.download_to_drive(file_path)
    await update.message.reply_text("📸 Распознаю текст...")
    recognized_text = await recognize_text_from_photo(file_path)
    if recognized_text:
        await update.message.reply_text(f"📄 {recognized_text[:500]}")
        answer = await ask_groq(recognized_text)
        await update.message.reply_text(answer)
    else:
        await update.message.reply_text("❌ Не распознано")
    os.remove(file_path)

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    history = get_user_history(user_id)
    update_user_stats(user_id, "messages")

    if user_id not in waiting_for_image:
        waiting_for_image[user_id] = False
    if user_id not in waiting_for_city:
        waiting_for_city[user_id] = False

    if waiting_for_image.get(user_id, False):
        img = await generate_image(text)
        await update.message.reply_photo(img, caption=text)
        waiting_for_image[user_id] = False
        update_user_stats(user_id, "images")
        return

    if waiting_for_city.get(user_id, False):
        weather = await get_weather(text)
        await update.message.reply_text(weather)
        waiting_for_city[user_id] = False
        return

    # Естественные команды
    if re.search(r'(мои|список|покажи)\s+напоминани[яй]', text.lower()):
        await cmd_my_reminders(update, context)
        return
    if re.search(r'(удали|удалить)\s+напоминани[ее]\s+(\d+)', text.lower()):
        match = re.search(r'(\d+)', text)
        if match:
            reminder_id = int(match.group(1))
            if delete_reminder(reminder_id, user_id):
                await update.message.reply_text(f"✅ Напоминание {reminder_id} удалено!")
            else:
                await update.message.reply_text(f"❌ Не найдено")
        return
    if re.search(r'(моя|мои|покажи)\s+статистик[ау]', text.lower()):
        await cmd_stats(update, context)
        return
    if text.lower().startswith("переведи") or text.lower().startswith("перевод"):
        text_to_translate = text[text.find(' ')+1:].strip()
        if text_to_translate:
            result = await translate_text(text_to_translate, 'ru')
            if result:
                await update.message.reply_text(f"🌍 **Перевод:**\n{result}", parse_mode="Markdown")
        return

    # Естественные напоминания
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

    # Кнопки
    if text == "🎨 Картинка":
        await update.message.reply_text("🖌️ Опиши что нарисовать")
        waiting_for_image[user_id] = True
        return
    if text == "🌤️ Погода":
        await update.message.reply_text("🏙️ Напиши город")
        waiting_for_city[user_id] = True
        return
    if text == "🌍 Переводчик":
        await update.message.reply_text("🌍 Напиши текст для перевода")
        return
    if text == "⏰ Напомнить":
        await update.message.reply_text("⏰ «напомни мне купить хлеб в 15:30»")
        return
    if text == "📰 Новости":
        await update.message.reply_text("📰 /news россия\n/news сша\n/news франция\n/news германия\n/news япония")
        return
    if text == "🎮 Игры":
        await update.message.reply_text("🎮 /dice\n/coin\n/quiz\n/guess")
        return
    if text == "📊 Статистика":
        await cmd_stats(update, context)
        return
    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Команды:**\n"
            "🎨 Картинка — кнопка\n"
            "🌤️ Погода — кнопка\n"
            "🌍 Переводчик — кнопка\n"
            "📰 Новости — /news\n"
            "🎮 Игры — /dice, /coin, /quiz, /guess\n"
            "📊 Статистика — /stats\n"
            "⏰ Напомнить — «напомни мне...»\n"
            "📋 /my_reminders\n"
            "🗑️ /del_remind 1\n"
            "📸 Фото — отправь фото\n"
            "🎤 Голос — отправь голосовое",
            parse_mode="Markdown"
        )
        return

    # Быстрые команды
    if text.lower().startswith("нарисуй"):
        prompt = text[7:].strip()
        if prompt:
            img = await generate_image(prompt)
            await update.message.reply_photo(img, caption=prompt)
            update_user_stats(user_id, "images")
        return
    if text.lower().startswith("погода в"):
        city = text[8:].strip()
        weather = await get_weather(city)
        await update.message.reply_text(weather)
        return

    # Обычный вопрос
    await update.message.reply_text("💭 Думаю...")
    history.append({"role": "user", "content": text})
    answer = await ask_groq_with_memory(text, history)
    history.append({"role": "assistant", "content": answer})
    save_user_history(user_id, history)
    await update.message.reply_text(answer)

# ========== ЗАПУСК ==========
def main():
    global application, loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    application = app
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("my_reminders", cmd_my_reminders))
    app.add_handler(CommandHandler("del_remind", cmd_del_remind))
    app.add_handler(CommandHandler("translate", cmd_translate))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("dice", cmd_dice))
    app.add_handler(CommandHandler("coin", cmd_coin))
    app.add_handler(CommandHandler("quiz", cmd_quiz))
    app.add_handler(CommandHandler("guess", cmd_guess))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Regex(r'^[1-4]$'), cmd_quiz_answer))
    app.add_handler(MessageHandler(filters.Regex(r'^\d+$'), cmd_guess_answer))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ марGO запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
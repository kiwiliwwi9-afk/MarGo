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

# ========== РАБОТА С БАЗОЙ ==========
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
        INSERT OR REPLACE INTO users (user_id, history, last_active)
        VALUES (?, ?, ?)
    ''', (user_id, json.dumps(history), datetime.now()))
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

# ========== ПАРСИНГ ВРЕМЕНИ ==========
def parse_reminder_time(text):
    text = text.lower()
    now = datetime.now()
    
    # через X минут/часов
    match = re.search(r'через\s+(\d+)\s*(минут|минуты|минуту|час|часов|часа)', text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if 'час' in unit:
            return now + timedelta(hours=amount)
        else:
            return now + timedelta(minutes=amount)
    
    # в 15:30
    match = re.search(r'в\s+(\d{1,2}):(\d{2})', text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        remind_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_time <= now:
            remind_time += timedelta(days=1)
        return remind_time
    
    # завтра в 9:00
    match = re.search(r'завтра\s+в\s+(\d{1,2}):(\d{2})', text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        remind_time = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return remind_time
    
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
            print(f"✅ Напоминание {reminder_id} отправлено пользователю {user_id}")
        except Exception as e:
            print(f"❌ Ошибка отправки напоминания {reminder_id}: {e}")
        delete_reminder_by_id(reminder_id)

def run_scheduler():
    global reminder_running
    if reminder_running:
        return
    reminder_running = True
    while True:
        check_and_send_reminders()
        time.sleep(30)

# ========== GROQ ==========
TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
OPENWEATHER_KEY = os.environ.get("OPENWEATHER_KEY")

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")

logging.basicConfig(level=logging.INFO)

global application
global loop

def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("😂 Мем"), KeyboardButton("⏰ Напомнить")],
        [KeyboardButton("📋 Мои напоминания"), KeyboardButton("❓ Помощь")]
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

# ========== МЕМЫ ==========
def get_meme():
    memes = [
        "🐱 Кот: 'Я вас не слышу'",
        "😂 Программист: 'Переустановлю завтра'",
        "🤖 Нейросеть: '2+2=5'"
    ]
    return random.choice(memes)

# ========== НАПОМИНАНИЯ ==========
async def cmd_remind(update, context):
    user_id = update.effective_user.id
    text = update.message.text.replace('/remind', '').strip()
    
    if not text:
        await update.message.reply_text(
            "⏰ **Как создать напоминание:**\n\n"
            "• `/remind позвонить маме через 10 минут`\n"
            "• `/remind купить хлеб в 15:30`\n"
            "• `/remind сдать проект завтра в 9:00`\n\n"
            "Или просто напиши: «напомни мне купить хлеб в 15:30»",
            parse_mode="Markdown"
        )
        return
    
    remind_time = parse_reminder_time(text)
    if not remind_time:
        await update.message.reply_text(
            "❌ Не понял время. Используй формат:\n"
            "• `через 10 минут`\n"
            "• `в 15:30`\n"
            "• `завтра в 9:00`",
            parse_mode="Markdown"
        )
        return
    
    reminder_text = text
    for phrase in ['через \d+ минут', 'через \d+ минуты', 'через \d+ час', 'в \d{1,2}:\d{2}', 'завтра в \d{1,2}:\d{2}']:
        reminder_text = re.sub(phrase, '', reminder_text).strip()
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

async def cmd_my_reminders(update, context):
    user_id = update.effective_user.id
    reminders = get_active_reminders(user_id)
    
    if not reminders:
        await update.message.reply_text("📭 У тебя нет активных напоминаний.", parse_mode="Markdown")
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
        await update.message.reply_text("❌ Укажи ID напоминания: `/del_remind 1`", parse_mode="Markdown")
        return
    try:
        reminder_id = int(context.args[0])
        if delete_reminder(reminder_id, user_id):
            await update.message.reply_text(f"✅ Напоминание {reminder_id} удалено!", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Напоминание {reminder_id} не найдено", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом", parse_mode="Markdown")

# ========== ЕСТЕСТВЕННЫЕ НАПОМИНАНИЯ ==========
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
        await update.message.reply_text("❌ Ошибка: неправильный формат времени. Используй например: «в 15:30»")

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
        await update.message.reply_text("❌ Ошибка: неправильный формат времени")

# ========== OCR ==========
async def recognize_text_from_photo(file_path):
    url = "https://api.ocr.space/parse/image"
    with open(file_path, 'rb') as f:
        files = {'file': f}
        data = {
            'language': 'rus',
            'isOverlayRequired': False,
            'scale': True,
            'OCREngine': 2,
            'detectOrientation': True
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, data=data, files=files, timeout=30) as r:
                    if r.status == 200:
                        result = await r.json()
                        if result.get('IsErroredOnProcessing'):
                            return None
                        if result.get('ParsedResults') and len(result['ParsedResults']) > 0:
                            text = result['ParsedResults'][0]['ParsedText']
                            if text and len(text.strip()) > 3:
                                return text.strip()
                    return None
        except Exception as e:
            print(f"OCR ошибка: {e}")
            return None

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
waiting_for_image = {}
waiting_for_city = {}

async def start(update, context):
    user_id = update.effective_user.id
    waiting_for_image[user_id] = False
    waiting_for_city[user_id] = False
    save_user_history(user_id, [])

    await update.message.reply_text(
        "🤍 **Привет! Я марGO — твой умный помощник!**\n\n"
        "🎨 **Картинка** — нажми кнопку и опиши\n"
        "🌤️ **Погода** — нажми кнопку и напиши город\n"
        "😂 **Мем** — случайная шутка\n"
        "⏰ **Напомнить** — просто напиши: «напомни мне купить хлеб в 15:30»\n\n"
        "📋 **Мои напоминания** — посмотреть все\n"
        "🗑️ **Удалить** — `/del_remind 1`\n\n"
        "📸 Отправь фото с текстом — я прочитаю\n"
        "💬 Или просто задай вопрос!",
        parse_mode="Markdown",
        reply_markup=get_keyboard()
    )

async def handle_photo(update, context):
    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_path = f"temp_{user_id}.jpg"
    await file.download_to_drive(file_path)
    
    await update.message.reply_text("📸 Распознаю текст с фото...")
    recognized_text = await recognize_text_from_photo(file_path)
    
    if recognized_text and recognized_text.strip():
        await update.message.reply_text(f"📄 **Распознанный текст:**\n{recognized_text[:1000]}", parse_mode="Markdown")
        await update.message.reply_text("💭 Думаю над ответом...")
        answer = await ask_groq(f"Вот текст с фото. Ответь на вопрос или реши задачу: {recognized_text}")
        await update.message.reply_text(answer)
        history = get_user_history(user_id)
        history.append({"role": "user", "content": f"[Фото] {recognized_text[:200]}"})
        history.append({"role": "assistant", "content": answer})
        save_user_history(user_id, history)
    else:
        await update.message.reply_text("❌ Не удалось распознать текст на фото.")
    os.remove(file_path)

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    history = get_user_history(user_id)

    if user_id not in waiting_for_image:
        waiting_for_image[user_id] = False
    if user_id not in waiting_for_city:
        waiting_for_city[user_id] = False

    if waiting_for_image.get(user_id, False):
        await update.message.reply_text("🎨 Рисую...")
        img = await generate_image(text)
        await update.message.reply_photo(img, caption=text)
        waiting_for_image[user_id] = False
        return

    if waiting_for_city.get(user_id, False):
        weather = await get_weather(text)
        await update.message.reply_text(weather)
        waiting_for_city[user_id] = False
        return

    # ===== ЕСТЕСТВЕННЫЕ НАПОМИНАНИЯ =====
    # напомни мне ... в 15:30
    remind_match = re.search(r'напомни мне\s+(.+?)\s+в\s+(\d{1,2}:\d{2})', text.lower())
    if remind_match:
        await process_natural_reminder(update, remind_match.group(1), remind_match.group(2))
        return
    
    # напомни ... через X минут
    remind_minutes_match = re.search(r'напомни\s+(.+?)\s+через\s+(\d+)\s*(?:минут|минуты|минуту|час|часа|часов)', text.lower())
    if remind_minutes_match:
        minutes = int(remind_minutes_match.group(2))
        unit = remind_minutes_match.group(3) if len(remind_minutes_match.groups()) > 2 else ''
        if 'час' in unit:
            minutes = minutes * 60
        await process_natural_reminder_minutes(update, remind_minutes_match.group(1), minutes)
        return
    
    # создай напоминание ... в 15:30
    create_match = re.search(r'создай напоминание\s+(.+?)\s+в\s+(\d{1,2}:\d{2})', text.lower())
    if create_match:
        await process_natural_reminder(update, create_match.group(1), create_match.group(2))
        return
    
    # напомнить ... завтра в 9:00
    tomorrow_match = re.search(r'напомнить\s+(.+?)\s+завтра\s+в\s+(\d{1,2}:\d{2})', text.lower())
    if tomorrow_match:
        await process_natural_reminder_tomorrow(update, tomorrow_match.group(1), tomorrow_match.group(2))
        return
    
    # запомни ... в 14:00
    remember_match = re.search(r'запомни\s+(.+?)\s+в\s+(\d{1,2}:\d{2})', text.lower())
    if remember_match:
        await process_natural_reminder(update, remember_match.group(1), remember_match.group(2))
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
    if text == "😂 Мем":
        await update.message.reply_text(get_meme())
        return
    if text == "⏰ Напомнить":
        await update.message.reply_text(
            "⏰ **Напиши в любом формате:**\n"
            "• «напомни мне купить хлеб в 15:30»\n"
            "• «напомни позвонить маме через 10 минут»\n"
            "• «напомнить сдать проект завтра в 9:00»",
            parse_mode="Markdown"
        )
        return
    if text == "📋 Мои напоминания":
        await cmd_my_reminders(update, context)
        return
    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Что умеет марGO:**\n\n"
            "🎨 **Картинка** — нажми кнопку и опиши\n"
            "🌤️ **Погода** — нажми кнопку и напиши город\n"
            "😂 **Мем** — случайная шутка\n"
            "⏰ **Напомнить** — просто напиши: «напомни мне...»\n\n"
            "📋 `/my_reminders` — посмотреть все напоминания\n"
            "🗑️ `/del_remind 1` — удалить\n\n"
            "📸 Отправь фото с текстом — я прочитаю\n"
            "💬 Или просто задай вопрос!",
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
        return
    if text.lower().startswith("погода в"):
        city = text[8:].strip()
        weather = await get_weather(city)
        await update.message.reply_text(weather)
        return
    if text.lower() in ["шутка", "расскажи шутку", "анекдот"]:
        await update.message.reply_text(get_meme())
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
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ марGO с естественными напоминаниями запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
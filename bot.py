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
    CREATE TABLE IF NOT EXISTS schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        day TEXT,
        lessons TEXT,
        UNIQUE(user_id, day)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS homework (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        subject TEXT,
        task TEXT,
        deadline TEXT,
        created_at TIMESTAMP
    )
''')
conn.commit()

# ========== ДНИ НЕДЕЛИ ==========
DAYS_RU = {
    'пн': 'понедельник', 'понедельник': 'понедельник',
    'вт': 'вторник', 'вторник': 'вторник',
    'ср': 'среда', 'среда': 'среда',
    'чт': 'четверг', 'четверг': 'четверг',
    'пт': 'пятница', 'пятница': 'пятница',
    'сб': 'суббота', 'суббота': 'суббота',
    'вс': 'воскресенье', 'воскресенье': 'воскресенье',
    'сегодня': 'сегодня', 'завтра': 'завтра'
}

WEEKDAYS = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']

def get_weekday_name(day):
    if day == 'сегодня':
        return WEEKDAYS[datetime.now().weekday()]
    elif day == 'завтра':
        return WEEKDAYS[(datetime.now() + timedelta(days=1)).weekday()]
    return DAYS_RU.get(day.lower(), None)

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

def get_schedule(user_id, day=None):
    if day:
        cursor.execute("SELECT lessons FROM schedule WHERE user_id = ? AND day = ?", (user_id, day))
        row = cursor.fetchone()
        return row[0] if row else None
    else:
        cursor.execute("""
            SELECT day, lessons FROM schedule WHERE user_id = ? 
            ORDER BY CASE day
                WHEN 'понедельник' THEN 1
                WHEN 'вторник' THEN 2
                WHEN 'среда' THEN 3
                WHEN 'четверг' THEN 4
                WHEN 'пятница' THEN 5
                WHEN 'суббота' THEN 6
                WHEN 'воскресенье' THEN 7
            END
        """, (user_id,))
        return cursor.fetchall()

def save_schedule(user_id, day, lessons):
    cursor.execute('''
        INSERT OR REPLACE INTO schedule (user_id, day, lessons)
        VALUES (?, ?, ?)
    ''', (user_id, day, lessons))
    conn.commit()

def delete_schedule_day(user_id, day):
    cursor.execute("DELETE FROM schedule WHERE user_id = ? AND day = ?", (user_id, day))
    conn.commit()

def clear_schedule(user_id):
    cursor.execute("DELETE FROM schedule WHERE user_id = ?", (user_id,))
    conn.commit()

def get_homework(user_id, subject=None):
    if subject:
        cursor.execute("SELECT subject, task, deadline FROM homework WHERE user_id = ? AND subject LIKE ? ORDER BY created_at DESC", (user_id, f'%{subject}%'))
    else:
        cursor.execute("SELECT subject, task, deadline FROM homework WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    return cursor.fetchall()

def add_homework(user_id, subject, task, deadline=None):
    cursor.execute('''
        INSERT INTO homework (user_id, subject, task, deadline, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, subject, task, deadline, datetime.now()))
    conn.commit()

# ========== GROQ ==========
TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
OPENWEATHER_KEY = os.environ.get("OPENWEATHER_KEY")

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")

logging.basicConfig(level=logging.INFO)

def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("😂 Мем"), KeyboardButton("📅 Расписание")],
        [KeyboardButton("📚 ДЗ"), KeyboardButton("❓ Помощь")]
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

# ========== ОБРАБОТКА ВВОДА РАСПИСАНИЯ ==========
async def process_schedule_input(update, context, text):
    user_id = update.effective_user.id
    lines = text.strip().split('\n')
    saved = 0
    
    for line in lines:
        if ':' not in line:
            continue
        day, lessons = line.split(':', 1)
        day = day.strip().lower()
        lessons = lessons.strip()
        target_day = get_weekday_name(day)
        if target_day:
            save_schedule(user_id, target_day, lessons)
            saved += 1
    
    if saved > 0:
        await update.message.reply_text(f"✅ Сохранено расписание на {saved} дней!")
        context.user_data['waiting_for_schedule'] = False
    else:
        await update.message.reply_text(
            "❌ Не удалось распознать формат.\n"
            "Используй:\n`понедельник: математика, русский`\n`вторник: литература, история`",
            parse_mode="Markdown"
        )

# ========== КОМАНДЫ РАСПИСАНИЯ ==========
async def cmd_schedule(update, context):
    user_id = update.effective_user.id
    
    if context.user_data.get('waiting_for_schedule'):
        text = update.message.text
        await process_schedule_input(update, context, text)
        return
    
    schedule = get_schedule(user_id)
    
    if not schedule:
        await update.message.reply_text(
            "📭 **У тебя пока нет расписания.**\n\n"
            "Отправь расписание в формате:\n"
            "`понедельник: математика, русский, физика`\n"
            "`вторник: литература, история, английский`\n\n"
            "Каждый день с новой строки.\n"
            "Или используй `/set_schedule день: уроки` для одного дня.",
            parse_mode="Markdown"
        )
        context.user_data['waiting_for_schedule'] = True
        return
    
    result = "📅 **Твоё расписание на неделю:**\n\n"
    for day, lessons in schedule:
        result += f"**{day.capitalize()}:** {lessons}\n\n"
    await update.message.reply_text(result, parse_mode="Markdown")

async def cmd_set_schedule(update, context):
    user_id = update.effective_user.id
    text = update.message.text.replace('/set_schedule', '').strip()
    
    if ':' not in text:
        await update.message.reply_text(
            "📝 Формат: `/set_schedule понедельник: математика, русский, физика`",
            parse_mode="Markdown"
        )
        return
    
    day, lessons = text.split(':', 1)
    day = day.strip().lower()
    lessons = lessons.strip()
    target_day = get_weekday_name(day)
    
    if not target_day:
        await update.message.reply_text("❌ Неправильный день")
        return
    
    save_schedule(user_id, target_day, lessons)
    await update.message.reply_text(f"✅ Расписание на **{target_day.capitalize()}** сохранено!", parse_mode="Markdown")

async def cmd_edit_schedule(update, context):
    user_id = update.effective_user.id
    text = update.message.text.replace('/edit_schedule', '').strip()
    
    if not text:
        await update.message.reply_text(
            "📝 **Редактирование расписания:**\n\n"
            "`/edit_schedule понедельник: математика, русский` — изменить день\n"
            "`/edit_schedule удалить понедельник` — удалить день\n"
            "`/edit_schedule очистить` — удалить всё расписание",
            parse_mode="Markdown"
        )
        return
    
    if ':' in text:
        day, lessons = text.split(':', 1)
        day = day.strip().lower()
        lessons = lessons.strip()
        target_day = get_weekday_name(day)
        if target_day:
            save_schedule(user_id, target_day, lessons)
            await update.message.reply_text(f"✅ Расписание на **{target_day.capitalize()}** обновлено!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Неправильный день")
    elif 'удалить' in text.lower():
        day = text.lower().replace('удалить', '').strip()
        target_day = get_weekday_name(day)
        if target_day:
            delete_schedule_day(user_id, target_day)
            await update.message.reply_text(f"✅ Расписание на **{target_day.capitalize()}** удалено!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Неправильный день")
    elif 'очистить' in text.lower():
        clear_schedule(user_id)
        await update.message.reply_text("✅ Всё расписание удалено!")

# ========== ДОМАШНЕЕ ЗАДАНИЕ ==========
async def cmd_homework(update, context):
    user_id = update.effective_user.id
    
    if context.args:
        subject = ' '.join(context.args).lower()
        hw_list = get_homework(user_id, subject)
        if hw_list:
            result = f"📚 **ДЗ по {subject.capitalize()}:**\n"
            for subj, task, deadline in hw_list[:5]:
                result += f"• {task}\n"
            await update.message.reply_text(result, parse_mode="Markdown")
        else:
            await update.message.reply_text(f"📭 Нет домашнего задания по {subject}")
        return
    
    hw_list = get_homework(user_id)
    if hw_list:
        result = "📚 **Твои домашние задания:**\n"
        for subj, task, deadline in hw_list[:10]:
            result += f"• **{subj}**: {task}\n"
        await update.message.reply_text(result, parse_mode="Markdown")
    else:
        await update.message.reply_text("📭 У тебя пока нет домашнего задания. Добавь через `/hw_add математика: решить №5`", parse_mode="Markdown")

async def cmd_homework_add(update, context):
    user_id = update.effective_user.id
    text = update.message.text.replace('/hw_add', '').strip()
    
    if ':' not in text:
        await update.message.reply_text("❌ Формат: `/hw_add математика: решить №5 стр 12`", parse_mode="Markdown")
        return
    
    subject, task = text.split(':', 1)
    subject = subject.strip()
    task = task.strip()
    
    deadline = None
    deadline_match = re.search(r'\[(.*?)\]', task)
    if deadline_match:
        deadline = deadline_match.group(1)
        task = task.replace(f'[{deadline}]', '').strip()
    
    add_homework(user_id, subject, task, deadline)
    
    response = f"✅ Добавлено ДЗ по **{subject}**: {task}"
    if deadline:
        response += f"\n📅 Дедлайн: {deadline}"
    await update.message.reply_text(response, parse_mode="Markdown")

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
    context.user_data['waiting_for_schedule'] = False
    save_user_history(user_id, [])

    await update.message.reply_text(
        "🤍 **Привет! Я марGO — твой умный помощник!**\n\n"
        "🎨 **Картинка** — нажми кнопку и опиши\n"
        "🌤️ **Погода** — нажми кнопку и напиши город\n"
        "😂 **Мем** — случайная шутка\n"
        "📅 **Расписание** — нажми кнопку, добавь или посмотри\n"
        "📚 **ДЗ** — нажми кнопку, добавь или посмотри\n\n"
        "**Команды:**\n"
        "• `/set_schedule понедельник: математика, русский`\n"
        "• `/edit_schedule` — редактировать расписание\n"
        "• `/hw_add математика: решить №5`\n"
        "• `/hw` — показать ДЗ\n\n"
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
        await update.message.reply_text("❌ Не удалось распознать текст на фото. Попробуй сделать фото чётче.")
    
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
    if text == "📅 Расписание":
        await cmd_schedule(update, context)
        return
    if text == "📚 ДЗ":
        await cmd_homework(update, context)
        return
    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Команды:**\n\n"
            "📅 **Расписание:**\n"
            "• /schedule — показать всё расписание\n"
            "• /set_schedule день: уроки — добавить день\n"
            "• /edit_schedule — редактировать\n\n"
            "📚 **ДЗ:**\n"
            "• /hw_add предмет: задача — добавить\n"
            "• /hw — показать всё ДЗ\n\n"
            "🎨 **Картинка:** нажми кнопку или «нарисуй кота»\n"
            "🌤️ **Погода:** нажми кнопку или «погода в Москве»\n"
            "😂 **Мем:** «расскажи шутку»\n"
            "📸 **Фото:** отправь фото с текстом\n"
            "💬 **Вопрос:** просто напиши",
            parse_mode="Markdown"
        )
        return

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

    await update.message.reply_text("💭 Думаю...")
    history.append({"role": "user", "content": text})
    answer = await ask_groq_with_memory(text, history)
    history.append({"role": "assistant", "content": answer})
    save_user_history(user_id, history)
    await update.message.reply_text(answer)

# ========== ЗАПУСК ==========
def main():
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("set_schedule", cmd_set_schedule))
    app.add_handler(CommandHandler("edit_schedule", cmd_edit_schedule))
    app.add_handler(CommandHandler("hw", cmd_homework))
    app.add_handler(CommandHandler("hw_add", cmd_homework_add))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ марGO с расписанием и ДЗ запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
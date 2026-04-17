import os
import asyncio
import aiohttp
import random
import threading
import time
import sqlite3
import json
from datetime import datetime
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")

if not TOKEN or not GROQ_KEY:
    raise ValueError("BOT_TOKEN и GROQ_KEY должны быть заданы")

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('margo.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        facts TEXT,
        history TEXT,
        last_active TIMESTAMP
    )
''')
conn.commit()

def get_user_data(user_id):
    cursor.execute("SELECT name, facts, history FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        name = row[0]
        facts = json.loads(row[1]) if row[1] else {}
        history = json.loads(row[2]) if row[2] else []
        return name, facts, history
    return None, {}, []

def save_user_data(user_id, name, facts, history):
    if len(history) > 20:
        history = history[-20:]
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, name, facts, history, last_active)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, name, json.dumps(facts), json.dumps(history), datetime.now()))
    conn.commit()

# ========== ВЕБ-СЕРВЕР ДЛЯ RENDER ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "Бот марGO работает 24/7!"

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

# ========== АВТОПИНГ ==========
def keep_alive():
    url = f"https://{os.environ.get('RENDER_EXTERNAL_URL', 'localhost')}"
    while True:
        try:
            import requests
            requests.get(url, timeout=10)
        except:
            pass
        time.sleep(240)

# ========== КЛАВИАТУРА ==========
def get_keyboard():
    buttons = [[KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")], [KeyboardButton("❓ Помощь")]]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ========== ФУНКЦИИ ==========
async def ask_groq(prompt):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 800,
        "temperature": 0.8
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, json=payload, timeout=30) as r:
                if r.status == 200:
                    data = await r.json()
                    return data['choices'][0]['message']['content']
                return f"❌ Ошибка: {r.status}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

async def get_weather(city):
    url = f"https://wttr.in/{city}?format=%C+%t&lang=ru"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status == 200:
                    return f"🌤️ {city.capitalize()}: {await r.text()}"
                return f"❌ Город не найден"
    except:
        return "❌ Ошибка погоды"

async def generate_image(prompt, user_id):
    salt = random.randint(1, 999999)
    return f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}?width=1024&height=1024&nologo=true&seed={salt}"

def detect_style(text):
    t = text.lower()
    if "официально" in t:
        return "official"
    if "для детей" in t:
        return "child"
    if "коротко" in t:
        return "short"
    return "normal"

def clean_text(text):
    t = text
    for word in ["официально", "серьёзно", "деловой", "для детей", "детям", "ребёнку", "коротко", "кратко"]:
        t = t.replace(word, "")
    return t.strip()

# ========== БОТ ==========
waiting_for_image = {}
waiting_for_weather = {}

async def start(update, context):
    user_id = update.effective_user.id
    name, facts, history = get_user_data(user_id)
    waiting_for_image[user_id] = False
    waiting_for_weather[user_id] = False
    
    if name:
        await update.message.reply_text(f"🤍 С возвращением, {name}!", reply_markup=get_keyboard())
    else:
        await update.message.reply_text(
            "🤍 Привет! Я **марGO**.\n\n"
            "🎨 **Картинка** — нажми кнопку и опиши\n"
            "🌤️ **Погода** — нажми кнопку и напиши город\n\n"
            "Как тебя зовут?",
            parse_mode="Markdown"
        )
        context.user_data['waiting_for_name'] = True

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    ud = context.user_data
    name, facts, history = get_user_data(user_id)

    if user_id not in waiting_for_image:
        waiting_for_image[user_id] = False
    if user_id not in waiting_for_weather:
        waiting_for_weather[user_id] = False

    if ud.get('waiting_for_name'):
        new_name = text.strip()
        save_user_data(user_id, new_name, facts, history)
        await update.message.reply_text(f"🤍 Приятно познакомиться, **{new_name}**!", parse_mode="Markdown", reply_markup=get_keyboard())
        ud['waiting_for_name'] = False
        return

    if text == "🌤️ Погода":
        await update.message.reply_text("🏙️ Напиши название города")
        waiting_for_weather[user_id] = True
        return

    if waiting_for_weather.get(user_id, False):
        await update.message.reply_text("🔍 Смотрю погоду...")
        weather = await get_weather(text)
        await update.message.reply_text(weather)
        waiting_for_weather[user_id] = False
        return

    if text == "🎨 Картинка":
        await update.message.reply_text("🖌️ Опиши, что нарисовать (отмена — выйти)")
        waiting_for_image[user_id] = True
        return

    if text == "❓ Помощь":
        await update.message.reply_text("📋 Кнопки: Картинка, Погода\n\n🧠 Я помню наш диалог и не повторяюсь!")
        return

    if text.lower() == "отмена":
        waiting_for_image[user_id] = False
        waiting_for_weather[user_id] = False
        await update.message.reply_text("✅ Отменено.")
        return

    if waiting_for_image.get(user_id, False):
        await update.message.reply_text("🎨 Рисую картинку...")
        img = await generate_image(text, user_id)
        await update.message.reply_photo(img, caption=f"🎨 {text}")
        waiting_for_image[user_id] = False
        return

    # ===== ОСНОВНОЙ ДИАЛОГ С ПАМЯТЬЮ (БЕЗ ЗАНУДСТВА) =====
    await update.message.reply_text("💭 Думаю...")

    history.append({"role": "user", "content": text})

    # Формируем промпт с историей
    memory_prompt = ""
    if history:
        memory_prompt = "Вот история диалога:\n"
        for msg in history[-10:]:
            memory_prompt += f"{msg['role']}: {msg['content']}\n"
        memory_prompt += f"\nПользователь: {text}\n"
        memory_prompt += "Ответь естественно, продолжай разговор. НЕ ИСПОЛЬЗУЙ фразы типа 'в предыдущем разговоре', 'учитывая историю', 'как мы обсуждали'. Просто отвечай как обычный человек."
    else:
        memory_prompt = text

    answer = await ask_groq(memory_prompt)

    history.append({"role": "assistant", "content": answer})
    save_user_data(user_id, name if name else "друг", facts, history)

    await update.message.reply_text(answer)

def run_bot():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    
    bot_app = Application.builder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ марGO запущена!")
    bot_app.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    threading.Thread(target=keep_alive).start()
    run_bot()

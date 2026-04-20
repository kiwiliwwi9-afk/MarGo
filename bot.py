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
from datetime import datetime
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
conn.commit()

def get_history(user_id):
    cursor.execute("SELECT history FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row and row[0]:
        return json.loads(row[0])
    return []

def save_history(user_id, history):
    if len(history) > 20:
        history = history[-20:]
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, history, last_active)
        VALUES (?, ?, ?)
    ''', (user_id, json.dumps(history), datetime.now()))
    conn.commit()

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("BOT_TOKEN")
GIGACHAT_CLIENT_ID = os.environ.get("GIGACHAT_CLIENT_ID")
GIGACHAT_SECRET = os.environ.get("GIGACHAT_SECRET")
OPENWEATHER_KEY = os.environ.get("OPENWEATHER_KEY")

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")

logging.basicConfig(level=logging.INFO)

def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("😂 Мем"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ========== GIGACHAT ==========
gigachat_token = None
token_expiry = 0

async def get_gigachat_token():
    global gigachat_token, token_expiry
    if gigachat_token and time.time() < token_expiry:
        return gigachat_token
    
    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    data = {"scope": "GIGACHAT_API_PERS", "client_id": GIGACHAT_CLIENT_ID, "client_secret": GIGACHAT_SECRET}
    
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=headers, data=data) as r:
            if r.status == 200:
                data = await r.json()
                gigachat_token = data['access_token']
                token_expiry = time.time() + 3600
                return gigachat_token
            return None

async def ask_gigachat(prompt):
    token = await get_gigachat_token()
    if not token:
        return "❌ Ошибка подключения, попробуй позже"
    
    url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "model": "GigaChat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 600
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, json=payload, timeout=30) as r:
                if r.status == 200:
                    data = await r.json()
                    return data['choices'][0]['message']['content']
                return "❌ Ошибка, попробуй ещё раз"
    except Exception as e:
        return "❌ Ошибка, попробуй позже"

async def ask_gigachat_with_memory(prompt, history):
    context = ""
    if history:
        context = "Вот что мы уже обсуждали:\n"
        for msg in history[-8:]:
            role = "Пользователь" if msg['role'] == 'user' else "марGO"
            context += f"{role}: {msg['content']}\n"
        context += "\n"
    
    full_prompt = f"{context}Пользователь: {prompt}\nОтветь как дружелюбный помощник, по имени марGO, кратко и по делу."
    return await ask_gigachat(full_prompt)

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
                    description = data['weather'][0]['description']
                    return f"🌤️ {city.capitalize()}: {description}, {temp}°C"
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

# ========== ОБРАБОТЧИКИ ==========
waiting_for_image = {}
waiting_for_city = {}

async def start(update, context):
    user_id = update.effective_user.id
    waiting_for_image[user_id] = False
    waiting_for_city[user_id] = False
    save_history(user_id, [])
    await update.message.reply_text(
        "🤍 Привет! Я марGO — твой помощник.\n\n"
        "🎨 Картинка — нажми кнопку\n"
        "🌤️ Погода — нажми кнопку\n"
        "😂 Мем — случайная шутка\n\n"
        "«нарисуй кота»\n"
        "«погода в Москве»",
        reply_markup=get_keyboard()
    )

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    history = get_history(user_id)

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
    
    if text == "❓ Помощь":
        await update.message.reply_text("Кнопки: Картинка, Погода, Мем")
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

    if text.lower() in ["шутка", "расскажи шутку"]:
        await update.message.reply_text(get_meme())
        return

    await update.message.reply_text("💭 Думаю...")
    history.append({"role": "user", "content": text})
    answer = await ask_gigachat_with_memory(text, history)
    history.append({"role": "assistant", "content": answer})
    save_history(user_id, history)
    await update.message.reply_text(answer)

def main():
    threading.Thread(target=run_web, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ марGO запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()

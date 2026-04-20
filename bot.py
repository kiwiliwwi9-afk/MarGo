import os
import aiohttp
import asyncio
import random
import logging
import threading
import sqlite3
import json
from datetime import datetime
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== ВЕБ-СЕРВЕР ДЛЯ RENDER ==========
web_app = Flask(__name__)

@web_app.route('/')
def health():
    return "Бот марGO работает!"

def run_web():
    web_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

# ========== БАЗА ДАННЫХ ДЛЯ ПАМЯТИ ==========
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
GROQ_KEY = os.environ.get("GROQ_KEY")

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("😂 Мем"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ========== УМНЫЕ ОТВЕТЫ С ПАМЯТЬЮ (ЖИВЫЕ) ==========
async def ask_groq_with_memory(prompt, history):
    # Формируем контекст из истории (последние 10 сообщений)
    context = ""
    if history:
        context = "Вот что мы уже обсудили (не повторяйся, учитывай это):\n"
        for msg in history[-10:]:
            role = "Пользователь" if msg['role'] == 'user' else "марGO"
            context += f"{role}: {msg['content']}\n"
        context += "\n"
    
    full_prompt = f"""Ты — марGO, живой, эмоциональный и умный помощник. Ты общаешься как подруга/друг, а не как робот.

ПРАВИЛА:
1. Отвечай живо, с душой, как человек
2. Используй эмодзи уместно (🤍, 🎨, 🌍, 😂, ✨, 💬, 🎉)
3. Не будь сухим и официальным
4. Проявляй эмпатию, радуйся, удивляйся
5. Если пользователь грустит — поддержи
6. Шути, но не перебарщивай
7. Не повторяй то, что уже было сказано в истории
8. Отвечай как добрая подруга, которая всегда рядом

{context}Пользователь: {prompt}
марGO:"""
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": full_prompt}],
        "max_tokens": 600,
        "temperature": 0.9
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, json=payload, timeout=30) as r:
                if r.status == 200:
                    data = await r.json()
                    return data['choices'][0]['message']['content']
                return f"❌ Ошибка API: {r.status}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

async def ask_groq(prompt):
    return await ask_groq_with_memory(prompt, [])

# ========== КАРТИНКИ ==========
async def generate_image(prompt):
    enhanced = f"masterpiece, best quality, highly detailed, beautiful, {prompt}"
    seed = random.randint(1, 999999)
    return f"https://image.pollinations.ai/prompt/{enhanced.replace(' ', '%20')}?width=1024&height=1024&nologo=true&seed={seed}"

# ========== ПОГОДА ==========
async def get_weather(city):
    url = f"https://wttr.in/{city}?format=%C+%t&lang=ru&m"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status == 200:
                    return f"🌤️ {city.capitalize()}: {await r.text()}"
                return f"❌ Город не найден"
    except:
        return "❌ Ошибка погоды"

async def get_weather_forecast(city):
    url = f"https://wttr.in/{city}?format=%C+%t&lang=ru&m&0-7"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status == 200:
                    return f"📅 Прогноз на неделю для {city.capitalize()}:\n{await r.text()}"
                return f"❌ Город не найден"
    except:
        return "❌ Ошибка прогноза"

# ========== МЕМЫ ==========
def get_meme():
    memes = [
        "🐱 Кот: 'Я вас не слышу'",
        "😂 Программист утром: 'Знаю как исправить!' Вечером: 'Переустановлю завтра'",
        "🤖 Нейросеть: 'Я умная' Пользователь: '2+2?' Нейросеть: '5'",
        "💬 марGO: 'Я всё помню' Пользователь: 'Что я сказал?' марGO: '...'"
    ]
    return random.choice(memes)

# ========== ОБРАБОТЧИКИ ==========
waiting_for_image = {}
waiting_for_city = {}

async def start(update, context):
    user_id = update.effective_user.id
    waiting_for_image[user_id] = False
    waiting_for_city[user_id] = False
    
    # Очищаем историю при новом старте
    save_history(user_id, [])
    
    await update.message.reply_text(
        "🤍 **Привет! Я марGO — твой живой и умный помощник!**\n\n"
        "🧠 **Я запоминаю наш диалог и отвечаю как подруга!**\n\n"
        "🎨 **Картинка** — нажми кнопку\n"
        "🌤️ **Погода** — нажми кнопку\n"
        "😂 **Мем** — случайная шутка\n\n"
        "**Быстрые команды:**\n"
        "• «нарисуй кота»\n"
        "• «погода в Москве»\n"
        "• «расскажи шутку»\n\n"
        "Просто пиши, я всегда рядом 🤍",
        parse_mode="Markdown",
        reply_markup=get_keyboard()
    )

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    
    # Получаем историю пользователя
    history = get_history(user_id)

    if user_id not in waiting_for_image:
        waiting_for_image[user_id] = False
    if user_id not in waiting_for_city:
        waiting_for_city[user_id] = False

    # Режим ожидания картинки
    if waiting_for_image.get(user_id, False):
        await update.message.reply_text("🎨 Рисую...")
        img = await generate_image(text)
        await update.message.reply_photo(img, caption=text)
        waiting_for_image[user_id] = False
        return

    # Режим ожидания города
    if waiting_for_city.get(user_id, False):
        if "на неделю" in text.lower():
            city = text.lower().replace("на неделю", "").strip()
            weather = await get_weather_forecast(city)
        else:
            weather = await get_weather(text)
        await update.message.reply_text(weather)
        waiting_for_city[user_id] = False
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
    
    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Что умеет марGO:**\n\n"
            "• 🎨 **Картинка** — нажми кнопку\n"
            "• 🌤️ **Погода** — нажми кнопку\n"
            "• 😂 **Мем** — случайная шутка\n"
            "• 🧠 **Память** — я запоминаю наш диалог!\n\n"
            "**Быстрые команды:**\n"
            "• «нарисуй кота»\n"
            "• «погода в Москве»\n"
            "• «расскажи шутку»\n\n"
            "Просто пиши, я всегда отвечу живо и с душой 🤍",
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
        if "на неделю" in city.lower():
            city = city.replace("на неделю", "").strip()
            weather = await get_weather_forecast(city)
        else:
            weather = await get_weather(city)
        await update.message.reply_text(weather)
        return

    if text.lower() in ["шутка", "расскажи шутку", "анекдот"]:
        await update.message.reply_text(get_meme())
        return

    # Умный ответ с памятью (живой)
    await update.message.reply_text("💭 Думаю...")
    
    # Добавляем сообщение пользователя в историю
    history.append({"role": "user", "content": text})
    
    # Получаем ответ с учётом истории
    answer = await ask_groq_with_memory(text, history)
    
    # Добавляем ответ в историю
    history.append({"role": "assistant", "content": answer})
    
    # Сохраняем историю
    save_history(user_id, history)
    
    await update.message.reply_text(answer)

async def help_command(update, context):
    await update.message.reply_text(
        "/start — перезапустить бота\n"
        "/help — помощь\n\n"
        "Просто напиши вопрос, я отвечу как подруга 🤍"
    )

def main():
    # Запускаем веб-сервер
    threading.Thread(target=run_web).start()
    
    # Запускаем бота
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ марGO с живыми ответами запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
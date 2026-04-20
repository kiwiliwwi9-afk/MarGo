import os
import aiohttp
import asyncio
import random
import logging
import threading
import time
import requests
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

# ========== АВТОПИНГ (ЧТОБЫ НЕ ЗАСЫПАЛ) ==========
def keep_alive():
    url = f"https://{os.environ.get('RENDER_EXTERNAL_URL', 'localhost')}"
    while True:
        try:
            requests.get(url, timeout=10)
            print("✅ Пинг отправлен")
        except Exception as e:
            print(f"❌ Ошибка пинга: {e}")
        time.sleep(240)  # каждые 4 минуты

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
            async with s.post(url, headers=headers, json=payload, timeout=30) as r:
                if r.status == 200:
                    data = await r.json()
                    return data['choices'][0]['message']['content']
                return f"❌ Ошибка API: {r.status}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

async def generate_image(prompt):
    enhanced = f"masterpiece, best quality, highly detailed, beautiful, {prompt}"
    seed = random.randint(1, 999999)
    return f"https://image.pollinations.ai/prompt/{enhanced.replace(' ', '%20')}?width=1024&height=1024&nologo=true&seed={seed}"

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

def get_meme():
    memes = [
        "🐱 Кот: 'Я вас не слышу'",
        "😂 Программист утром: 'Знаю как исправить!' Вечером: 'Переустановлю завтра'",
        "🤖 Нейросеть: 'Я умная' Пользователь: '2+2?' Нейросеть: '5'"
    ]
    return random.choice(memes)

waiting_for_image = {}
waiting_for_city = {}

async def start(update, context):
    user_id = update.effective_user.id
    waiting_for_image[user_id] = False
    waiting_for_city[user_id] = False
    await update.message.reply_text(
        "🤍 **Привет! Я марGO — твой умный помощник!**\n\n"
        "🎨 **Картинка** — нажми кнопку\n"
        "🌤️ **Погода** — нажми кнопку\n"
        "😂 **Мем** — случайная шутка\n\n"
        "**Быстрые команды:**\n"
        "• «нарисуй кота»\n"
        "• «погода в Москве»\n"
        "• «расскажи шутку»",
        parse_mode="Markdown",
        reply_markup=get_keyboard()
    )

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text

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
        if "на неделю" in text.lower():
            city = text.lower().replace("на неделю", "").strip()
            weather = await get_weather_forecast(city)
        else:
            weather = await get_weather(text)
        await update.message.reply_text(weather)
        waiting_for_city[user_id] = False
        return

    if text == "🎨 Картинка":
        await update.message.reply_text("🖌️ Опиши что нарисовать")
        waiting_for_image[user_id] = True
    elif text == "🌤️ Погода":
        await update.message.reply_text("🏙️ Напиши город")
        waiting_for_city[user_id] = True
    elif text == "😂 Мем":
        await update.message.reply_text(get_meme())
    elif text == "❓ Помощь":
        await update.message.reply_text("Кнопки: Картинка, Погода, Мем")
    elif text.lower().startswith("нарисуй"):
        prompt = text[7:].strip()
        if prompt:
            await update.message.reply_text("🎨 Рисую...")
            img = await generate_image(prompt)
            await update.message.reply_photo(img, caption=prompt)
    elif text.lower().startswith("погода в"):
        city = text[8:].strip()
        if "на неделю" in city.lower():
            city = city.replace("на неделю", "").strip()
            weather = await get_weather_forecast(city)
        else:
            weather = await get_weather(city)
        await update.message.reply_text(weather)
    elif text.lower() in ["шутка", "расскажи шутку", "анекдот"]:
        await update.message.reply_text(get_meme())
    else:
        await update.message.reply_text("💭 Думаю...")
        answer = await ask_groq(text)
        await update.message.reply_text(answer)

async def help_command(update, context):
    await update.message.reply_text("/start — перезапустить")

def main():
    # Запускаем веб-сервер
    threading.Thread(target=run_web).start()
    # Запускаем автопинг
    threading.Thread(target=keep_alive).start()
    # Запускаем бота
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ марGO с автопингом запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
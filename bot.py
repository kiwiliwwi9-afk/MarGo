import os
import asyncio
import aiohttp
import random
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask
import threading

TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")
if not GROQ_KEY:
    raise ValueError("GROQ_KEY не задан")

# Веб-сервер для Render
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "Бот марGO работает!"

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

# ========== БОТ ==========
user_names = {}
waiting_for_image = {}
waiting_for_weather = {}
last_image_prompts = {}

def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

async def ask_groq(prompt, style="normal"):
    style_prompts = {
        "official": "Ты — официальный ассистент. Отвечай серьёзно, формально.",
        "child": "Ты — детский помощник. Объясняй простым языком для ребёнка.",
        "short": "Отвечай максимально коротко. 1-2 предложения.",
        "normal": "Ты — марGO, дружелюбный помощник. Отвечай естественно, с душой."
    }
    
    full_prompt = f"{style_prompts[style]} Пользователь спрашивает: {prompt}"
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": full_prompt}],
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
                    weather = await r.text()
                    return f"🌤️ {city.capitalize()}: {weather}"
                return f"❌ Город не найден"
    except:
        return "❌ Ошибка погоды"

async def generate_image(prompt, user_id):
    salt = random.randint(1, 999999)
    last = last_image_prompts.get(user_id, "")
    if prompt == last:
        prompt = f"{prompt} другой вариант"
    last_image_prompts[user_id] = prompt
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

async def start(update, context):
    user_id = update.effective_user.id
    waiting_for_image[user_id] = False
    waiting_for_weather[user_id] = False
    await update.message.reply_text(
        "🤍 Привет! Я **марGO**!\n\n"
        "🌤️ **Погода** — нажми кнопку и напиши город\n"
        "🎨 **Картинка** — нажми кнопку и опиши\n"
        "📝 **Стили** — добавь: официально, для детей, коротко\n\n"
        "Как тебя зовут?",
        parse_mode="Markdown"
    )
    context.user_data['waiting_for_name'] = True

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    ud = context.user_data

    if user_id not in waiting_for_image:
        waiting_for_image[user_id] = False
    if user_id not in waiting_for_weather:
        waiting_for_weather[user_id] = False

    if ud.get('waiting_for_name'):
        user_names[user_id] = text.strip()
        ud['waiting_for_name'] = False
        await update.message.reply_text(
            f"🤍 Приятно познакомиться, **{user_names[user_id]}**!",
            parse_mode="Markdown",
            reply_markup=get_keyboard()
        )
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
        await update.message.reply_text(
            "📋 **Что умеет марGO:**\n\n"
            "• 🌤️ Погода — кнопка + город\n"
            "• 🎨 Картинка — кнопка + описание\n"
            "• 📝 Стили — добавь: официально, для детей, коротко",
            parse_mode="Markdown"
        )
        return

    if text.lower() == "отмена":
        waiting_for_image[user_id] = False
        waiting_for_weather[user_id] = False
        await update.message.reply_text("✅ Режим отменён.")
        return

    if waiting_for_image.get(user_id, False):
        await update.message.reply_text("🎨 Рисую картинку...")
        img = await generate_image(text, user_id)
        await update.message.reply_photo(img, caption=f"🎨 {text}")
        waiting_for_image[user_id] = False
        return

    style = detect_style(text)
    clean = clean_text(text)
    if not clean:
        clean = text
    
    await update.message.reply_text("💭 Думаю...")
    answer = await ask_groq(clean, style)
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
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    # Запускаем бота
    run_bot()

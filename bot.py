import os
import asyncio
import aiohttp
import random
import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")

if not TOKEN or not GROQ_KEY:
    raise ValueError("BOT_TOKEN и GROQ_KEY должны быть заданы")

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== КЛАВИАТУРА ==========
def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("😂 Мем"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ========== ФУНКЦИЯ ДЛЯ GROQ (УМНЫЕ ОТВЕТЫ) ==========
async def ask_groq(prompt):
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

# ========== ГЕНЕРАЦИЯ КАРТИНОК (УЛУЧШЕННАЯ) ==========
async def generate_image(prompt):
    # Улучшенный API с качеством 1024x1024 и моделью flux
    seed = random.randint(1, 999999)
    url = f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}?width=1024&height=1024&nologo=true&seed={seed}&model=flux"
    return url

# ========== ПОГОДА ==========
async def get_weather(city):
    url = f"https://wttr.in/{city}?format=%C+%t+%w&lang=ru"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status == 200:
                    weather = await r.text()
                    return f"🌤️ {city.capitalize()}: {weather}"
                return f"❌ Город не найден"
    except:
        return "❌ Ошибка погоды"

async def get_weather_forecast(city):
    url = f"https://wttr.in/{city}?format=%C+%t&lang=ru&0-7"
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

def get_quote():
    quotes = [
        "💡 Код — это поэзия, которую понимает компьютер.",
        "🚀 Лучший способ предсказать будущее — создать его самому.",
        "🌍 GO World — твой мир. Твои правила.",
        "🤍 марGO всегда с тобой!"
    ]
    return random.choice(quotes)

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
user_states = {}
waiting_for_city = {}
waiting_for_image = {}

async def start(update, context):
    user_id = update.effective_user.id
    user_states[user_id] = {}
    await update.message.reply_text(
        "🤍 **Привет! Я марGO — твой умный помощник!**\n\n"
        "🎨 **Картинка** — нажми кнопку и напиши описание\n"
        "🌤️ **Погода** — нажми кнопку и напиши город\n"
        "😂 **Мем** — случайная шутка\n"
        "💬 **Общение** — просто напиши вопрос\n\n"
        "Например:\n"
        "• «нарисуй кота в космосе»\n"
        "• «погода в Москве»\n"
        "• «погода в Москве на неделю»\n"
        "• «расскажи шутку»\n"
        "• «что такое любовь?»",
        parse_mode="Markdown",
        reply_markup=get_keyboard()
    )

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    waiting_for_image[user_id] = waiting_for_image.get(user_id, False)
    waiting_for_city[user_id] = waiting_for_city.get(user_id, False)

    # Обработка картинки
    if waiting_for_image.get(user_id, False):
        await update.message.reply_text("🎨 Рисую картинку... (до 15 секунд)")
        img_url = await generate_image(text)
        await update.message.reply_photo(img_url, caption=f"🎨 {text}")
        waiting_for_image[user_id] = False
        return

    # Обработка погоды
    if waiting_for_city.get(user_id, False):
        if "на неделю" in text.lower():
            city = text.lower().replace("на неделю", "").strip()
            weather = await get_weather_forecast(city)
        else:
            weather = await get_weather(text)
        await update.message.reply_text(weather)
        waiting_for_city[user_id] = False
        return

    # Кнопка "Картинка"
    if text == "🎨 Картинка":
        await update.message.reply_text("🖌️ Опиши, что нарисовать. Например: «кот в космосе»")
        waiting_for_image[user_id] = True
        return

    # Кнопка "Погода"
    if text == "🌤️ Погода":
        await update.message.reply_text("🏙️ Напиши название города. Например: «Москва» или «Москва на неделю»")
        waiting_for_city[user_id] = True
        return

    # Кнопка "Мем"
    if text == "😂 Мем":
        await update.message.reply_text(get_meme())
        return

    # Кнопка "Помощь"
    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Что умеет марGO:**\n\n"
            "• 🎨 **Картинка** — нажми кнопку и опиши\n"
            "• 🌤️ **Погода** — нажми кнопку и напиши город\n"
            "• 😂 **Мем** — случайная шутка\n"
            "• 💬 **Общение** — просто напиши вопрос\n\n"
            "**Примеры:**\n"
            "• «нарисуй кота в космосе»\n"
            "• «погода в Москве на неделю»\n"
            "• «расскажи шутку»\n"
            "• «что такое искусственный интеллект?»",
            parse_mode="Markdown"
        )
        return

    # Обработка "нарисуй" без кнопки
    if text.lower().startswith("нарисуй"):
        prompt = text[7:].strip()
        if prompt:
            await update.message.reply_text("🎨 Рисую картинку... (до 15 секунд)")
            img_url = await generate_image(prompt)
            await update.message.reply_photo(img_url, caption=f"🎨 {prompt}")
        else:
            await update.message.reply_text("🖌️ Что нарисовать? Например: «нарисуй кота в космосе»")
        return

    # Обработка "погода в" без кнопки
    if text.lower().startswith("погода в"):
        city = text[8:].strip()
        if "на неделю" in city.lower():
            city = city.replace("на неделю", "").strip()
            weather = await get_weather_forecast(city)
        else:
            weather = await get_weather(city)
        await update.message.reply_text(weather)
        return

    # Обработка "расскажи шутку"
    if text.lower() in ["шутка", "расскажи шутку", "анекдот"]:
        await update.message.reply_text(get_meme())
        return

    # Умный ответ через Groq
    await update.message.reply_text("💭 Думаю...")
    answer = await ask_groq(text)
    await update.message.reply_text(answer)

async def help_command(update, context):
    await update.message.reply_text(
        "/start — перезапустить бота\n"
        "/help — помощь\n\n"
        "Или просто напиши вопрос!"
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ марGO запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()

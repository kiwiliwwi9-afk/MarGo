import os
import aiohttp
import random
import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== КЛАВИАТУРА ==========
def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("😂 Мем"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ========== УМНЫЕ ОТВЕТЫ (GROQ) ==========
async def ask_groq(prompt):
    if not GROQ_KEY:
        return "🤍 Ключ Groq не настроен"
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

# ========== КАРТИНКИ ==========
async def generate_image(prompt):
    enhanced = f"masterpiece, best quality, highly detailed, {prompt}"
    seed = random.randint(1, 999999)
    return f"https://image.pollinations.ai/prompt/{enhanced.replace(' ', '%20')}?width=1024&height=1024&nologo=true&seed={seed}"

# ========== ПОГОДА ==========
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

# ========== ОБРАБОТЧИКИ ==========
waiting_for_image = {}
waiting_for_city = {}

async def start(update, context):
    await update.message.reply_text(
        "🤍 **Привет! Я марGO — твой умный помощник!**\n\n"
        "🎨 **Картинка** — нажми кнопку и напиши описание\n"
        "🌤️ **Погода** — нажми кнопку и напиши город\n"
        "😂 **Мем** — случайная шутка\n"
        "💬 **Общение** — просто напиши вопрос\n\n"
        "**Быстрые команды:**\n"
        "• «нарисуй кота в космосе»\n"
        "• «погода в Москве»\n"
        "• «погода в Москве на неделю»",
        parse_mode="Markdown",
        reply_markup=get_keyboard()
    )

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text

    # Режим ожидания картинки
    if waiting_for_image.get(user_id, False):
        await update.message.reply_text("🎨 Рисую картинку... (до 15 секунд)")
        img = await generate_image(text)
        await update.message.reply_photo(img, caption=f"🎨 {text}")
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
        await update.message.reply_text("🖌️ Опиши, что нарисовать. Например: «кот в космосе»")
        waiting_for_image[user_id] = True
        return

    if text == "🌤️ Погода":
        await update.message.reply_text("🏙️ Напиши название города. Например: «Москва» или «Москва на неделю»")
        waiting_for_city[user_id] = True
        return

    if text == "😂 Мем":
        await update.message.reply_text(get_meme())
        return

    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Что умеет марGO:**\n\n"
            "• 🎨 **Картинка** — нажми кнопку и опиши\n"
            "• 🌤️ **Погода** — нажми кнопку и напиши город\n"
            "• 💬 **Общение** — просто напиши вопрос\n\n"
            "**Быстрые команды:**\n"
            "• «нарисуй кота»\n"
            "• «погода в Москве»\n"
            "• «погода в Москве на неделю»",
            parse_mode="Markdown"
        )
        return

    # Быстрые команды без кнопок
    if text.lower().startswith("нарисуй"):
        prompt = text[7:].strip()
        if prompt:
            await update.message.reply_text("🎨 Рисую картинку...")
            img = await generate_image(prompt)
            await update.message.reply_photo(img, caption=f"🎨 {prompt}")
        else:
            await update.message.reply_text("🖌️ Что нарисовать? Например: «нарисуй кота»")
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

    # Умный ответ через Groq
    await update.message.reply_text("💭 Думаю...")
    answer = await ask_groq(text)
    await update.message.reply_text(answer)

async def help_command(update, context):
    await update.message.reply_text("/start — перезапустить бота")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ марGO с картинками, погодой и Groq запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()

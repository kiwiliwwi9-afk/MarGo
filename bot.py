import aiohttp
import asyncio
import random
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== КЛЮЧИ ==========
TOKEN = "8737782674:AAGFDh3KdhFaVu3lp4QFm-2_cR-_Ne7hICY"
GROQ_KEY = "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"
# ==========================

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

# ========== УМНЫЕ ОТВЕТЫ (Groq) ==========
async def ask_groq(prompt, style="normal"):
    style_prompts = {
        "official": "Ты — официальный ассистент. Отвечай серьёзно, формально, деловым стилем. Без эмоций, без шуток.",
        "child": "Ты — детский помощник. Объясняй простым языком для ребёнка 8-10 лет. Будь добрым, понятным, используй примеры.",
        "short": "Отвечай максимально коротко. 1-2 предложения. Только суть, без воды.",
        "normal": "Ты — марGO, дружелюбный молодой помощник. Отвечай естественно, с душой, можешь шутить, но не перебарщивай."
    }
    
    full_prompt = f"{style_prompts[style]} Пользователь спрашивает: {prompt}"
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": full_prompt}],
        "max_tokens": 1000,
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

# ========== ПОГОДА ==========
async def get_weather(city):
    url = f"https://wttr.in/{city}?format=%C+%t+%w&lang=ru"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status == 200:
                    weather = await r.text()
                    return f"🌤️ {city.capitalize()}: {weather}"
                return f"❌ Город {city} не найден"
    except:
        return "❌ Ошибка. Проверь название города"

# ========== КАРТИНКИ ==========
async def generate_image(prompt, user_id):
    salt = random.randint(1, 999999)
    last = last_image_prompts.get(user_id, "")
    if prompt == last:
        prompt = f"{prompt} (другой вариант)"
    last_image_prompts[user_id] = prompt
    url = f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}?width=1024&height=1024&nologo=true&seed={salt}"
    return url

# ========== ОПРЕДЕЛЕНИЕ СТИЛЯ ==========
def detect_style(text):
    t = text.lower()
    if "официально" in t or "серьёзно" in t:
        return "official"
    if "для детей" in t or "ребёнку" in t:
        return "child"
    if "коротко" in t or "кратко" in t:
        return "short"
    return "normal"

def clean_text(text):
    t = text
    for word in ["официально", "серьёзно", "деловой", "для детей", "детям", "ребёнку", "коротко", "кратко"]:
        t = t.replace(word, "")
    return t.strip()

# ========== ОБРАБОТКА ==========
async def start(update, context):
    user_id = update.effective_user.id
    waiting_for_image[user_id] = False
    waiting_for_weather[user_id] = False
    await update.message.reply_text(
        "🤍 Привет! Я **марGO** на Groq.\n\n"
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
        await update.message.reply_text("🖌️ Опиши, что нарисовать\n(отмена — выйти)")
        waiting_for_image[user_id] = True
        return

    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Что умеет марGO:**\n\n"
            "• 🌤️ **Погода** — кнопка + город\n"
            "• 🎨 **Картинка** — кнопка + описание\n"
            "• 📝 **Стили** — добавь: официально, для детей, коротко\n\n"
            "Примеры:\n"
            "— «Официально напиши письмо»\n"
            "— «Для детей про космос»\n"
            "— «Коротко что такое ИИ»\n"
            "— «Напиши стих про весну»\n"
            "— «Расскажи шутку»",
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
        if img:
            await update.message.reply_photo(img, caption=f"🎨 {text}")
        else:
            await update.message.reply_text("❌ Не удалось нарисовать")
        waiting_for_image[user_id] = False
        return

    style = detect_style(text)
    clean = clean_text(text)
    if not clean:
        clean = text
    
    await update.message.reply_text("💭 Думаю...")
    name = user_names.get(user_id, "друг")
    answer = await ask_groq(clean, style)
    await update.message.reply_text(answer)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ марGO запущена!")
    app.run_polling()

if __name__ == "__main__":
    main()

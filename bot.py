import os
import aiohttp
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ==========
TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_KEY")

# ========== ПРОВЕРКА (ВИДНО В ЛОГАХ) ==========
print("=" * 50)
print(f"BOT_TOKEN загружен: {'✅ ДА' if TOKEN else '❌ НЕТ'}")
print(f"GROQ_KEY загружен: {'✅ ДА' if GROQ_KEY else '❌ НЕТ'}")
if TOKEN:
    print(f"TOKEN начинается с: {TOKEN[:15]}...")
print("=" * 50)

if not TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден! Добавь переменную в Railway")
if not GROQ_KEY:
    raise ValueError("❌ GROQ_KEY не найден! Добавь переменную в Railway")

# ========== ОСТАЛЬНОЙ КОД ==========
user_names = {}

def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

async def ask_groq(prompt):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 800,
        "temperature": 0.8
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=60) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data['choices'][0]['message']['content']
                return f"❌ Ошибка API: {resp.status}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

async def generate_image(prompt):
    url = f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}?width=1024&height=1024&nologo=true"
    return url

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_names:
        name = user_names[user_id]
        await update.message.reply_text(
            f"✨ С возвращением, {name}!",
            reply_markup=get_keyboard()
        )
    else:
        await update.message.reply_text(
            "🤍 Привет! Я **марGO**.\n\nКак тебя зовут?",
            parse_mode="Markdown"
        )
        context.user_data['waiting_for_name'] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    ud = context.user_data

    if ud.get('waiting_for_name'):
        user_names[user_id] = text.strip()
        ud['waiting_for_name'] = False
        await update.message.reply_text(
            f"🤍 Приятно познакомиться, {user_names[user_id]}!\n\nПросто пиши вопросы.",
            reply_markup=get_keyboard()
        )
        return

    if ud.get('waiting_for_image'):
        await update.message.reply_text("🎨 Рисую картинку...")
        img_url = await generate_image(text)
        await update.message.reply_photo(img_url, caption=f"🎨 {text}")
        ud['waiting_for_image'] = False
        return

    if text == "🎨 Картинка":
        await update.message.reply_text("🖌️ Опиши, что нарисовать")
        ud['waiting_for_image'] = True
        return

    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Что умеет марGO:**\n\n"
            "• 💬 Общение — просто напиши вопрос\n"
            "• 🎨 Картинка — нажми кнопку и опиши\n\n"
            "Примеры: «Напиши стих», «Расскажи шутку»",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text("💭 Думаю...")
    answer = await ask_groq(
        f"Ты — марGO, дружелюбный бот. Пользователь написал: {text}. Ответь естественно."
    )
    await update.message.reply_text(answer)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ марGO запущена на Railway!")
    app.run_polling()

if __name__ == "__main__":
    main()

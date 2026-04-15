import aiohttp
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import os

TOKEN = "8737782674:AAGFDh3KdhFaVu3lp4QFm-2_cR-_Ne7hICY"
GROQ_KEY = "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"

user_names = {}

def get_keyboard():
    buttons = [
        [KeyboardButton("📝 Текст"), KeyboardButton("🎨 Картинка")],
        [KeyboardButton("📖 Стих"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

async def ask_groq(prompt):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 800}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=headers, json=payload, timeout=60) as r:
            if r.status == 200:
                return (await r.json())['choices'][0]['message']['content']
            return f"Ошибка: {r.status}"

async def generate_image(prompt):
    return f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}?width=1024&height=1024"

async def start(update, context):
    user_id = update.effective_user.id
    if user_id in user_names:
        await update.message.reply_text(f"С возвращением, {user_names[user_id]}!", reply_markup=get_keyboard())
    else:
        await update.message.reply_text("Привет! Как тебя зовут?")
        context.user_data['waiting_for_name'] = True

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    ud = context.user_data

    if ud.get('waiting_for_name'):
        user_names[user_id] = text.strip()
        ud['waiting_for_name'] = False
        await update.message.reply_text("Приятно познакомиться!", reply_markup=get_keyboard())
        return

    if ud.get('waiting_for_text'):
        await update.message.reply_text("Пишу...")
        ans = await ask_groq(f"Напиши текст на тему: {text}")
        await update.message.reply_text(ans)
        ud['waiting_for_text'] = False
        return

    if ud.get('waiting_for_image'):
        await update.message.reply_text("Рисую...")
        img = await generate_image(text)
        await update.message.reply_photo(img, caption=text)
        ud['waiting_for_image'] = False
        return

    if ud.get('waiting_for_poem'):
        await update.message.reply_text("Сочиняю стих...")
        ans = await ask_groq(f"Напиши стих на тему: {text}. 4-8 строк, с рифмой.")
        await update.message.reply_text(ans)
        ud['waiting_for_poem'] = False
        return

    if text == "📝 Текст":
        await update.message.reply_text("Напиши тему")
        ud['waiting_for_text'] = True
    elif text == "🎨 Картинка":
        await update.message.reply_text("Опиши что нарисовать")
        ud['waiting_for_image'] = True
    elif text == "📖 Стих":
        await update.message.reply_text("Напиши тему стиха")
        ud['waiting_for_poem'] = True
    elif text == "❓ Помощь":
        await update.message.reply_text("Кнопки: Текст, Картинка, Стих")
    else:
        await update.message.reply_text("Думаю...")
        ans = await ask_groq(text)
        await update.message.reply_text(ans)

app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
print("Бот запущен!")
app.run_polling()

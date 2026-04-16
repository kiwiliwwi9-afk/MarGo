import os
import asyncio
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")
if not GROQ_KEY:
    raise ValueError("GROQ_KEY не задан")

# Веб-сервер для Render (чтобы был открытый порт)
app = Flask(__name__)

@app.route('/')
def health():
    return "Бот работает!"

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

# Бот
async def start(update, context):
    await update.message.reply_text("Привет! Бот работает!")

async def handle(update, context):
    await update.message.reply_text("Я жив! Отвечаю на вопросы.")

def run_bot():
    # Фикс для Python 3.14
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    
    bot_app = Application.builder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    print("✅ Бот запущен!")
    bot_app.run_polling()

if __name__ == "__main__":
    # Запускаем веб-сервер в отдельном потоке
    web_thread = threading.Thread(target=run_web)
    web_thread.start()
    # Запускаем бота в основном потоке
    run_bot()

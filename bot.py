import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")
if not GROQ_KEY:
    raise ValueError("GROQ_KEY не задан")

async def start(update, context):
    await update.message.reply_text("Привет! Бот работает!")

async def handle(update, context):
    await update.message.reply_text("Я жив!")

def main():
    # ФИКС ДЛЯ PYTHON 3.14
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    print("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()

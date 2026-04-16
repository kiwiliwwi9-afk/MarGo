import os
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

app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
print("✅ Бот запущен!")
app.run_polling()

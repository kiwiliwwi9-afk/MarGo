import os
import asyncio
import aiohttp
import random
import threading
import time
import sqlite3
import json
from datetime import datetime
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from gtts import gTTS
import io

TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")

if not TOKEN or not GROQ_KEY:
    raise ValueError("BOT_TOKEN и GROQ_KEY должны быть заданы")

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('margo.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        facts TEXT,
        history TEXT,
        voice_lang TEXT DEFAULT 'ru',
        last_active TIMESTAMP
    )
''')
conn.commit()

def get_user_data(user_id):
    cursor.execute("SELECT name, facts, history, voice_lang FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if row:
        name = row[0]
        facts = json.loads(row[1]) if row[1] else {}
        history = json.loads(row[2]) if row[2] else []
        voice_lang = row[3] if row[3] else 'ru'
        return name, facts, history, voice_lang
    return None, {}, [], 'ru'

def save_user_data(user_id, name, facts, history, voice_lang='ru'):
    if len(history) > 20:
        history = history[-20:]
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, name, facts, history, voice_lang, last_active)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, name, json.dumps(facts), json.dumps(history), voice_lang, datetime.now()))
    conn.commit()

# ========== ВЕБ-СЕРВЕР ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "Бот марGO работает 24/7!"

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

def keep_alive():
    url = f"https://{os.environ.get('RENDER_EXTERNAL_URL', 'localhost')}"
    while True:
        try:
            import requests
            requests.get(url, timeout=10)
        except:
            pass
        time.sleep(240)

def get_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("🎤 Голос"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

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

# ========== КАРТИНКИ ==========
async def generate_image(prompt):
    salt = random.randint(1, 999999)
    return f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}?width=1024&height=1024&nologo=true&seed={salt}"

# ========== ГОЛОС ==========
async def text_to_voice(text, lang='ru'):
    try:
        tts = gTTS(text=text[:500], lang=lang, slow=False)
        audio_bytes = io.BytesIO()
        tts.write_to_fp(audio_bytes)
        audio_bytes.seek(0)
        return audio_bytes
    except Exception as e:
        print(f"Ошибка озвучки: {e}")
        return None

VOICE_LANGS = {
    'ru': '🇷🇺 Русский',
    'en': '🇬🇧 English',
    'fr': '🇫🇷 Français',
    'de': '🇩🇪 Deutsch',
    'es': '🇪🇸 Español',
    'it': '🇮🇹 Italiano'
}

# ========== МЕМЫ И ЦИТАТЫ ==========
def get_meme():
    memes = [
        "🐱 Кот: 'Я вас не слышу'",
        "😂 Программист утром: 'Знаю как исправить!' Вечером: 'Переустановлю завтра'",
        "🤖 Нейросеть: 'Я умная' Пользователь: '2+2?' Нейросеть: '5'"
    ]
    return random.choice(memes)

def get_quote():
    quotes = [
        "💡 Код — это поэзия, которую понимает компьютер.",
        "🚀 Лучший способ предсказать будущее — создать его самому.",
        "🌍 GO World — твой мир."
    ]
    return random.choice(quotes)

# ========== ОСНОВНЫЕ ФУНКЦИИ ==========
async def ask_groq(prompt):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
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

# ========== БОТ ==========
waiting_for_image = {}
waiting_for_weather = {}

async def start(update, context):
    user_id = update.effective_user.id
    name, facts, history, voice_lang = get_user_data(user_id)
    waiting_for_image[user_id] = False
    waiting_for_weather[user_id] = False
    
    if name:
        await update.message.reply_text(
            f"🤍 С возвращением, {name}!\n\n"
            f"🎤 Голос: /voice\n"
            f"🌤️ Погода: «погода в Москве»\n"
            f"🎨 Картинка: «нарисуй кота»",
            reply_markup=get_keyboard()
        )
    else:
        await update.message.reply_text(
            "🤍 Привет! Я **марGO**.\n\n"
            "🎨 «нарисуй ...»\n"
            "🌤️ «погода в ...»\n"
            "🎤 /voice\n"
            "😂 /meme\n"
            "💡 /quote\n\n"
            "Как тебя зовут?",
            parse_mode="Markdown"
        )
        context.user_data['waiting_for_name'] = True

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    ud = context.user_data
    name, facts, history, voice_lang = get_user_data(user_id)

    if user_id not in waiting_for_image:
        waiting_for_image[user_id] = False
    if user_id not in waiting_for_weather:
        waiting_for_weather[user_id] = False

    # Ожидание имени
    if ud.get('waiting_for_name'):
        new_name = text.strip()
        save_user_data(user_id, new_name, facts, history, 'ru')
        await update.message.reply_text(
            f"🤍 Приятно познакомиться, **{new_name}**!",
            parse_mode="Markdown",
            reply_markup=get_keyboard()
        )
        ud['waiting_for_name'] = False
        return

    # ===== КАРТИНКИ =====
    if text.lower().startswith("нарисуй"):
        prompt = text[7:].strip()
        if not prompt:
            await update.message.reply_text("Что нарисовать?")
            return
        await update.message.reply_text("🎨 Рисую...")
        img = await generate_image(prompt)
        await update.message.reply_photo(img, caption=f"🎨 {prompt}")
        return

    # ===== ПОГОДА =====
    if text.lower().startswith("погода в"):
        city = text[8:].strip()
        if not city:
            await update.message.reply_text("Напиши город")
            return
        if "на неделю" in city.lower():
            city = city.replace("на неделю", "").strip()
            weather = await get_weather_forecast(city)
        else:
            weather = await get_weather(city)
        await update.message.reply_text(weather)
        return

    # ===== КНОПКИ =====
    if text == "🌤️ Погода":
        await update.message.reply_text("Напиши город. Например: «погода в Москве»")
        waiting_for_weather[user_id] = True
        return

    if text == "🎨 Картинка":
        await update.message.reply_text("Опиши, что нарисовать. Например: «нарисуй кота в космосе»")
        waiting_for_image[user_id] = True
        return

    if text == "🎤 Голос":
        await update.message.reply_text("🎤 /voice — озвучить последний ответ")
        return

    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Команды:**\n"
            "• нарисуй ...\n"
            "• погода в ...\n"
            "• /voice\n"
            "• /meme\n"
            "• /quote",
            parse_mode="Markdown"
        )
        return

    if text.lower() == "отмена":
        waiting_for_image[user_id] = False
        waiting_for_weather[user_id] = False
        await update.message.reply_text("✅ Отменено")
        return

    if waiting_for_image.get(user_id, False):
        await update.message.reply_text("🎨 Рисую...")
        img = await generate_image(text)
        await update.message.reply_photo(img, caption=f"🎨 {text}")
        waiting_for_image[user_id] = False
        return

    if waiting_for_weather.get(user_id, False):
        if "на неделю" in text.lower():
            city = text.replace("на неделю", "").strip()
            weather = await get_weather_forecast(city)
        else:
            weather = await get_weather(text)
        await update.message.reply_text(weather)
        waiting_for_weather[user_id] = False
        return

    # ===== ОБЫЧНЫЙ ДИАЛОГ =====
    await update.message.reply_text("💭 Думаю...")

    history.append({"role": "user", "content": text})

    memory_prompt = ""
    if history:
        memory_prompt = "История диалога:\n"
        for msg in history[-10:]:
            memory_prompt += f"{msg['role']}: {msg['content']}\n"
        memory_prompt += f"\nПользователь: {text}\n"
        memory_prompt += "Ответь естественно. НЕ используй фразы 'в предыдущем разговоре', 'учитывая историю'. Просто отвечай."
    else:
        memory_prompt = text

    answer = await ask_groq(memory_prompt)

    history.append({"role": "assistant", "content": answer})
    save_user_data(user_id, name if name else "друг", facts, history, voice_lang)

    await update.message.reply_text(answer)
    context.user_data['last_answer'] = answer

# ===== КОМАНДЫ =====
async def meme(update, context):
    await update.message.reply_text(get_meme())

async def quote(update, context):
    await update.message.reply_text(get_quote())

async def voice(update, context):
    user_id = update.effective_user.id
    name, facts, history, voice_lang = get_user_data(user_id)
    
    last_answer = None
    if history:
        for msg in reversed(history):
            if msg.get('role') == 'assistant':
                last_answer = msg.get('content')
                break
    
    if last_answer:
        await update.message.reply_text("🎤 Озвучиваю...")
        audio = await text_to_voice(last_answer, voice_lang.split('-')[0])
        if audio:
            await update.message.reply_voice(audio, caption="🎙️ марGO")
        else:
            await update.message.reply_text("Не удалось озвучить")
    else:
        await update.message.reply_text("Сначала задай вопрос")

async def voice_lang(update, context):
    user_id = update.effective_user.id
    name, facts, history, voice_lang = get_user_data(user_id)
    
    lang_list = "\n".join([f"{code} — {name}" for code, name in VOICE_LANGS.items()])
    await update.message.reply_text(
        f"🎧 **Выбери язык:**\n{lang_list}\n\nТекущий: {VOICE_LANGS.get(voice_lang, 'ru')}\n\nНапиши код (ru, en, fr, de, es, it)",
        parse_mode="Markdown"
    )
    context.user_data['waiting_for_voice_lang'] = True

async def set_voice_lang(update, context):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if text in VOICE_LANGS:
        name, facts, history, _ = get_user_data(user_id)
        save_user_data(user_id, name, facts, history, text)
        await update.message.reply_text(f"✅ Голос изменён на {VOICE_LANGS[text]}")
        context.user_data['waiting_for_voice_lang'] = False
    else:
        await update.message.reply_text(f"❌ Доступны: {', '.join(VOICE_LANGS.keys())}")

def run_bot():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    
    bot_app = Application.builder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("meme", meme))
    bot_app.add_handler(CommandHandler("quote", quote))
    bot_app.add_handler(CommandHandler("voice", voice))
    bot_app.add_handler(CommandHandler("voice_lang", voice_lang))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ марGO с голосом запущена!")
    bot_app.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    threading.Thread(target=keep_alive).start()
    run_bot()
    

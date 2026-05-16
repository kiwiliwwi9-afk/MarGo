import os
import aiohttp
import asyncio
import random
import logging
import threading
import time
import requests
import sqlite3
import json
import re
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import xml.etree.ElementTree as ET

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
OPENWEATHER_KEY = os.environ.get("OPENWEATHER_KEY")

if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")

logging.basicConfig(level=logging.INFO)

# ========== RSS ДЛЯ НОВОСТЕЙ ==========
NEWS_RSS = {
    'us': 'https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml',
    'ru': 'https://meduza.io/rss/all',
    'uk': 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',
    'fr': 'https://rss.nytimes.com/services/xml/rss/nyt/Europe.xml',
    'de': 'https://rss.nytimes.com/services/xml/rss/nyt/Europe.xml',
    'jp': 'https://rss.nytimes.com/services/xml/rss/nyt/AsiaPacific.xml',
    'cn': 'https://rss.nytimes.com/services/xml/rss/nyt/AsiaPacific.xml',
}

async def fetch_news(country='us'):
    url = NEWS_RSS.get(country, NEWS_RSS['us'])
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    root = ET.fromstring(text)
                    items = root.findall('.//item')
                    news = []
                    for item in items[:5]:
                        title = item.find('title').text
                        link = item.find('link').text
                        news.append(f"[{title[:80]}]({link})")
                    return news
    except:
        pass
    return None

async def cmd_news(update, context, country=None):
    if country is None:
        text = update.message.text.replace('/news', '').strip().lower()
        countries_map = {
            'россия': 'ru', 'russia': 'ru',
            'сша': 'us', 'usa': 'us', 'америка': 'us',
            'великобритания': 'uk', 'britain': 'uk', 'англия': 'uk',
            'франция': 'fr', 'france': 'fr',
            'германия': 'de', 'germany': 'de',
            'япония': 'jp', 'japan': 'jp',
            'китай': 'cn', 'china': 'cn'
        }
        country_code = countries_map.get(text, 'us')
    else:
        country_code = country
    
    await update.message.reply_text("📰 Загружаю новости...")
    news = await fetch_news(country_code)
    
    if news:
        result = "📰 **Новости:**\n\n"
        for i, item in enumerate(news, 1):
            result += f"{i}. {item}\n"
        await update.message.reply_text(result, parse_mode="Markdown", disable_web_page_preview=True)
    else:
        await update.message.reply_text("❌ Не удалось загрузить новости. Попробуй позже.")

# ========== КЛАВИАТУРА ==========
def get_main_keyboard():
    buttons = [
        [KeyboardButton("🎨 Картинка"), KeyboardButton("🌤️ Погода")],
        [KeyboardButton("📰 Новости"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_image_keyboard():
    buttons = [
        [KeyboardButton("❌ Отмена"), KeyboardButton("🎨 Картинка")],
        [KeyboardButton("🌤️ Погода"), KeyboardButton("📰 Новости"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_news_keyboard():
    buttons = [
        [KeyboardButton("🌍 Главные"), KeyboardButton("🇷🇺 Россия")],
        [KeyboardButton("🇺🇸 США"), KeyboardButton("🇬🇧 Великобритания")],
        [KeyboardButton("🇫🇷 Франция"), KeyboardButton("🇩🇪 Германия")],
        [KeyboardButton("🇯🇵 Япония"), KeyboardButton("🇨🇳 Китай")],
        [KeyboardButton("🔙 Назад")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

# ========== ОСТАЛЬНЫЕ ФУНКЦИИ ==========
waiting_for_image = {}
waiting_for_city = {}

async def generate_image(prompt):
    enhanced = f"masterpiece, best quality, highly detailed, {prompt}"
    seed = random.randint(1, 999999)
    url = f"https://image.pollinations.ai/prompt/{enhanced.replace(' ', '%20')}?width=1024&height=1024&nologo=true&seed={seed}"
    return url

async def get_weather(city):
    if not OPENWEATHER_KEY:
        return "🔌 Погода не настроена"
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_KEY}&units=metric&lang=ru"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status == 200:
                    data = await r.json()
                    temp = round(data['main']['temp'])
                    desc = data['weather'][0]['description']
                    return f"🌤️ {city.capitalize()}: {desc}, {temp}°C"
                return f"❌ Город '{city}' не найден"
    except:
        return "❌ Ошибка погоды"

async def ask_groq(prompt):
    if not GROQ_KEY:
        return "🔌 Groq не настроен"
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
            async with s.post(url, headers=headers, json=payload, timeout=15) as r:
                if r.status == 200:
                    data = await r.json()
                    return data['choices'][0]['message']['content']
                return f"❌ Ошибка: {r.status}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
async def start(update, context):
    user_id = update.effective_user.id
    waiting_for_image[user_id] = False
    waiting_for_city[user_id] = False
    await update.message.reply_text(
        "🤍 Привет! Я марGO — твой помощник.\n\n"
        "🎨 **Картинка** — нажми кнопку и опиши\n"
        "🌤️ **Погода** — нажми кнопку и напиши город\n"
        "📰 **Новости** — нажми кнопку и выбери страну\n\n"
        "💬 **Вопрос** — просто спроси\n\n"
        "❌ **Отмена** — выйти из режима картинки",
        reply_markup=get_main_keyboard()
    )

async def handle_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text

    if user_id not in waiting_for_image:
        waiting_for_image[user_id] = False
    if user_id not in waiting_for_city:
        waiting_for_city[user_id] = False

    # ===== НОВОСТИ (ВЫБОР СТРАНЫ) =====
    if text == "📰 Новости":
        await update.message.reply_text(
            "📰 **Выбери страну:**",
            reply_markup=get_news_keyboard()
        )
        return

    if text == "🌍 Главные":
        await cmd_news(update, context, 'us')
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return
    if text == "🇷🇺 Россия":
        await cmd_news(update, context, 'ru')
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return
    if text == "🇺🇸 США":
        await cmd_news(update, context, 'us')
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return
    if text == "🇬🇧 Великобритания":
        await cmd_news(update, context, 'uk')
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return
    if text == "🇫🇷 Франция":
        await cmd_news(update, context, 'fr')
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return
    if text == "🇩🇪 Германия":
        await cmd_news(update, context, 'de')
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return
    if text == "🇯🇵 Япония":
        await cmd_news(update, context, 'jp')
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return
    if text == "🇨🇳 Китай":
        await cmd_news(update, context, 'cn')
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return

    if text == "🔙 Назад":
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return

    # ===== ОТМЕНА =====
    if text.lower() == "отмена":
        waiting_for_image[user_id] = False
        waiting_for_city[user_id] = False
        await update.message.reply_text("✅ Режим отменён.", reply_markup=get_main_keyboard())
        return

    # ===== РЕЖИМ ОЖИДАНИЯ КАРТИНКИ =====
    if waiting_for_image.get(user_id, False):
        await update.message.reply_text("🎨 Рисую...")
        img = await generate_image(text)
        await update.message.reply_photo(img, caption=f"🎨 {text}")
        waiting_for_image[user_id] = False
        await update.message.reply_text("Меню:", reply_markup=get_main_keyboard())
        return

    # ===== РЕЖИМ ОЖИДАНИЯ ГОРОДА =====
    if waiting_for_city.get(user_id, False):
        weather = await get_weather(text)
        await update.message.reply_text(weather)
        waiting_for_city[user_id] = False
        return

    # ===== КНОПКА "КАРТИНКА" =====
    if text == "🎨 Картинка":
        await update.message.reply_text(
            "🖌️ Опиши, что нарисовать.\nНапример: «кот в космосе»\n\n❌ «отмена» — выйти",
            reply_markup=get_image_keyboard()
        )
        waiting_for_image[user_id] = True
        return

    # ===== КНОПКА "ПОГОДА" =====
    if text == "🌤️ Погода":
        await update.message.reply_text("🏙️ Напиши город (например: «Москва»)")
        waiting_for_city[user_id] = True
        return

    # ===== КНОПКА "ПОМОЩЬ" =====
    if text == "❓ Помощь":
        await update.message.reply_text(
            "📋 **Что умеет марGO:**\n\n"
            "🎨 **Картинка** — нажми кнопку и опиши\n"
            "🌤️ **Погода** — нажми кнопку и напиши город\n"
            "📰 **Новости** — нажми кнопку и выбери страну\n"
            "💬 **Вопрос** — просто напиши\n"
            "❌ **Отмена** — выйти из режима картинки\n\n"
            "**Быстрые команды:**\n"
            "• «нарисуй кота»\n"
            "• «погода в Москве»\n"
            "• «отмена»",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return

    # ===== БЫСТРАЯ КОМАНДА "НАРИСУЙ" =====
    if text.lower().startswith("нарисуй"):
        prompt = text[7:].strip()
        if prompt:
            await update.message.reply_text("🎨 Рисую...")
            img = await generate_image(prompt)
            await update.message.reply_photo(img, caption=f"🎨 {prompt}")
        else:
            await update.message.reply_text("🖌️ Что нарисовать? Например: «нарисуй кота в космосе»")
        return

    # ===== БЫСТРАЯ КОМАНДА "ПОГОДА В" =====
    if text.lower().startswith("погода в"):
        city = text[8:].strip()
        weather = await get_weather(city)
        await update.message.reply_text(weather)
        return

    # ===== ОБЫЧНЫЙ ВОПРОС =====
    await update.message.reply_text("💭 Думаю...")
    answer = await ask_groq(text)
    await update.message.reply_text(answer)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ марGO с новостями запущена!")
    app.run_polling()

if __name__ == "__main__":
    main()
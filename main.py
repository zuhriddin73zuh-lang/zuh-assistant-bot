# -*- coding: utf-8 -*-
import telebot
import os
from flask import Flask, request

# =========================
# Загрузка токенов из переменных окружения
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_API_KEY = os.getenv("CHAT_API_KEY", "").strip()
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY", "").strip()
VIDEO_API_KEY = os.getenv("VIDEO_API_KEY", "").strip()

# =========================
# Проверка токена Telegram
# =========================
if not BOT_TOKEN:
    raise ValueError("Ошибка: переменная окружения BOT_TOKEN пуста или содержит пробелы!")

# =========================
# Инициализация бота и сервера Flask
# =========================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# =========================
# Команда /start
# =========================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message, 
        "Привет! Я Zuh, ассистент-бот 🤖\nОтправь мне текст или голосовое сообщение, и я создам рекламный контент!"
    )

# =========================
# Обработка любых сообщений
# =========================
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    text = message.text or "Голосовое сообщение"
    # Здесь позже можно добавить генерацию контента через ChatAPI, ImageAPI, VideoAPI
    bot.reply_to(message, f"Вы отправили: {text}\n(Здесь будет готовый рекламный контент)")

# =========================
# Проверка работы сервера
# =========================
@app.route('/')
def index():
    return "Бот работает!"

# =========================
# Webhook для Telegram
# =========================
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try:
        json_str = request.stream.read().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        print(f"Ошибка обработки вебхука: {e}")
    return "!", 200

# =========================
# Запуск Flask на Render
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)












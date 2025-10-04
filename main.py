# -*- coding: utf-8 -*-
import os
import telebot
from flask import Flask, request
import requests

# ==========================
# Получение токенов из переменных окружения
# ==========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()       # Telegram
CHAT_API_KEY = os.getenv("CHAT_API_KEY", "").strip() # ChatGPT или другой текстовый AI
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY", "").strip() # AI генерация изображений
VIDEO_API_KEY = os.getenv("VIDEO_API_KEY", "").strip() # AI генерация видео

# Проверка, что все токены установлены
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен или содержит пробелы")
if not CHAT_API_KEY:
    raise ValueError("CHAT_API_KEY не установлен")
if not IMAGE_API_KEY:
    raise ValueError("IMAGE_API_KEY не установлен")
if not VIDEO_API_KEY:
    raise ValueError("VIDEO_API_KEY не установлен")

# ==========================
# Инициализация Telegram бота
# ==========================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ==========================
# Команда /start
# ==========================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я Zuh Assistant Bot 🤖\nЯ могу создавать тексты, баннеры и короткие видео.")

# ==========================
# Обработка текста
# ==========================
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_text = message.text

    # Генерация ответа через Chat API
    chat_response = generate_chat_response(user_text)

    # Генерация изображения (баннера)
    image_url = generate_image(user_text)

    # Генерация короткого видео
    video_url = generate_video(user_text)

    # Отправка пользователю
    response_msg = f"Текст:\n{chat_response}\n\nБаннер: {image_url}\nВидео: {video_url}"
    bot.reply_to(message, response_msg)

# ==========================
# Функции для генерации через API
# ==========================
def generate_chat_response(prompt):
    # Пример запроса к Chat API
    url = "https://api.openai.com/v1/chat/completions"  # или свой сервис
    headers = {"Authorization": f"Bearer {CHAT_API_KEY}"}
    json_data = {
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200
    }
    r = requests.post(url, headers=headers, json=json_data)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"]
    return "Ошибка генерации текста"

def generate_image(prompt):
    # Пример запроса к Image API
    url = "https://api.fakeimage.ai/generate"
    headers = {"Authorization": f"Bearer {IMAGE_API_KEY}"}
    json_data = {"prompt": prompt, "size": "512x512"}
    r = requests.post(url, headers=headers, json=json_data)
    if r.status_code == 200:
        return r.json().get("url", "Нет ссылки на изображение")
    return "Ошибка генерации изображения"

def generate_video(prompt):
    # Пример запроса к Video API
    url = "https://api.fakevideo.ai/generate"
    headers = {"Authorization": f"Bearer {VIDEO_API_KEY}"}
    json_data = {"prompt": prompt, "duration": 10}
    r = requests.post(url, headers=headers, json=json_data)
    if r.status_code == 200:
        return r.json().get("video_url", "Нет ссылки на видео")
    return "Ошибка генерации видео"

# ==========================
# Вебхук для Telegram
# ==========================
@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    json_str = request.stream.read().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def index():
    return "Zuh Assistant Bot работает!"

# ==========================
# Запуск сервера
# ==========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)














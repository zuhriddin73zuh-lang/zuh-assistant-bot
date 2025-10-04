import os
import telebot
from flask import Flask, request
import requests

# Токены и ключи из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_API_KEY = os.getenv("CHAT_API_KEY")
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY")
VIDEO_API_KEY = os.getenv("VIDEO_API_KEY")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# --- Обработка команды /start ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я твой Зох-ассистент-бот. Пришли текст или голос, и я создам рекламу!")

# --- Обработка текстовых сообщений ---
@bot.message_handler(content_types=['text'])
def handle_text(message):
    prompt = message.text

    # 1️⃣ Генерация текста через ChatGPT
    chat_response = generate_text(prompt)

    # 2️⃣ Генерация баннера через Image API
    image_url = generate_image(prompt)

    # 3️⃣ Генерация видео через DeepAI Video API
    video_url = generate_video(prompt)

    # Отправляем результат пользователю
    bot.send_message(message.chat.id, f"Текст для поста:\n{chat_response}")
    bot.send_message(message.chat.id, f"Баннер:\n{image_url}")
    bot.send_message(message.chat.id, f"Видео (10-15 сек):\n{video_url}")

# --- Функции генерации через API ---
def generate_text(prompt):
    headers = {"Authorization": f"Bearer {CHAT_API_KEY}"}
    data = {"prompt": prompt, "max_tokens": 150}
    response = requests.post("https://api.openai.com/v1/completions", headers=headers, json=data)
    return response.json()["choices"][0]["text"]

def generate_image(prompt):
    headers = {"api-key": IMAGE_API_KEY}
    data = {"prompt": prompt}
    response = requests.post("https://api.deepai.org/api/text2img", headers=headers, data=data)
    return response.json()["output_url"]

def generate_video(prompt):
    headers = {"api-key": VIDEO_API_KEY}
    data = {"text": prompt}
    response = requests.post("https://api.deepai.org/api/text2video", headers=headers, data=data)
    return response.json()["output_url"]

# --- Webhook для Render ---
@app.route('/' + BOT_TOKEN, methods=['POST'])
def getMessage():
    json_str = request.stream.read().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "!", 200

@app.route('/')
def index():
    return "Бот работает!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)










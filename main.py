import os
import telebot
from flask import Flask, request

# Берём токен из переменной окружения
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ Переменная окружения BOT_TOKEN не найдена")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Команда /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! 👋 Я твой ассистент-бот. Отправь мне заявку!")

# Корневая страница (проверка работы сервера)
@app.route('/')
def index():
    return "Бот работает на Render ✅", 200

# Маршрут для вебхука
@app.route('/webhook', methods=['POST'])
def webhook():
    json_str = request.stream.read().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "ok", 200

if __name__ == "__main__":
    # Для локального запуска (не обязательно на Render)
    app.run(host="0.0.0.0", port=10000)



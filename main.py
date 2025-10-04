import telebot
import os
from flask import Flask, request

# Берём токен и сразу убираем пробелы/переводы строки
TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise ValueError("❌ Ошибка: BOT_TOKEN не найден. Укажи его в Render → Environment Variables.")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Команда /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я твой ассистент-бот 🤖")

# Проверка сервера
@app.route('/')
def index():
    return "✅ Бот работает!"

# Вебхук
@app.route('/' + TOKEN, methods=['POST'])
def getMessage():
    json_str = request.stream.read().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)








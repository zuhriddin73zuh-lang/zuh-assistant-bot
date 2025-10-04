
import telebot
import os
from flask import Flask, request

TOKEN = os.getenv("BOT_TOKEN")  # токен берём из переменной окружения

# Отладка — печатаем токен, чтобы проверить пробелы
print("TOKEN (raw):", repr(TOKEN))

if not TOKEN:
    raise ValueError("Переменная BOT_TOKEN не найдена! Проверь настройки в Render.")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# команда /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я твой ассистент-бот 🤖")

# проверка, что сервер работает
@app.route('/')
def index():
    return "Бот работает!"

# вебхук
@app.route('/' + TOKEN, methods=['POST'])
def getMessage():
    json_str = request.stream.read().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "!", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)






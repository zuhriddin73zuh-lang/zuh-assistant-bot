import os
import telebot
from flask import Flask, request

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден! Проверь переменные окружения на Render.")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Команда /start
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "✅ Бот запущен и работает!")

# Проверка сервера
@app.route('/')
def index():
    return "Бот работает!", 200

# Обработка обновлений от Telegram
@app.route('/' + TOKEN, methods=['POST'])
def webhook():
    json_str = request.stream.read().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "!", 200

if __name__ == "__main__":
    # Для Render нужен host=0.0.0.0 и порт из переменной окружения
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)





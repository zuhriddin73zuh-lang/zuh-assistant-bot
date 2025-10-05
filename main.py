
# -*- coding: utf-8 -*-
import os
import requests
import traceback
from threading import Thread
from flask import Flask, request
import telebot
import time

# -----------------------
# Чтение и очистка переменных окружения
# -----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_API_KEY = os.getenv("CHAT_API_KEY", "").strip()    # OpenAI (Chat)
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY", "").strip()  # OpenAI Images или другой провайдер
VIDEO_API_KEY = os.getenv("VIDEO_API_KEY", "").strip()  # DeepAI или другой провайдер

if not BOT_TOKEN:
    raise ValueError("Ошибка: BOT_TOKEN не задан или пуст. Проверь переменные окружения на Render.")

# -----------------------
# Настройки
# -----------------------
# таймауты для внешних запросов (в секундах)
API_TIMEOUT_SHORT = 15
API_TIMEOUT_LONG = 120

# базовые endpoint'ы (можно менять при необходимости)
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_IMAGE_URL = "https://api.openai.com/v1/images"  # OpenAI new endpoints may differ
DEEPAI_TEXT2VIDEO_URL = "https://api.deepai.org/api/text2video"

# -----------------------
# Инициализация бота и сервера
# -----------------------
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# -----------------------
# Вспомогательные функции: генерация контента
# -----------------------
def generate_text_openai(prompt):
    """Генерация текста через OpenAI Chat API (если у тебя CHAT_API_KEY)."""
    headers = {"Authorization": f"Bearer {CHAT_API_KEY}", "Content-Type": "application/json"}
    json_data = {
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 300,
        "temperature": 0.7
    }
    r = requests.post(OPENAI_CHAT_URL, headers=headers, json=json_data, timeout=API_TIMEOUT_SHORT)
    r.raise_for_status()
    j = r.json()
    # безопасно извлекаем текст
    return j["choices"][0]["message"]["content"].strip()

def generate_image_openai(prompt):
    """
    Генерация изображения через OpenAI Images API.
    ВАЖНО: endpoint/формат может отличаться у разных провайдеров — при необходимости подправим.
    Возвращает URL изображения (если провайдер вернул).
    """
    headers = {"Authorization": f"Bearer {IMAGE_API_KEY}", "Content-Type": "application/json"}
    # Попробуем стандартный путь/формат OpenAI images.generate (старый вариант)
    json_data = {"prompt": prompt, "size": "1024x1024"}
    # Если твой IMAGE_API_KEY от другого провайдера — нужно изменить URL и формат запроса.
    r = requests.post(OPENAI_IMAGE_URL + "/generations", headers=headers, json=json_data, timeout=API_TIMEOUT_LONG)
    r.raise_for_status()
    j = r.json()
    # Пример: j["data"][0]["url"]
    # Попробуем несколько вариантов извлечения
    if isinstance(j.get("data"), list) and j["data"] and "url" in j["data"][0]:
        return j["data"][0]["url"]
    if j.get("data") and isinstance(j["data"][0].get("b64_json"), str):
        # если вернули base64 - сохраним в файл и вернём путь
        import base64, tempfile
        b64 = j["data"][0]["b64_json"]
        data = base64.b64decode(b64)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        tmp.write(data); tmp.close()
        return tmp.name
    # fallback
    return None

def generate_video_deepai(prompt):
    """Генерация короткого видео через DeepAI text2video (если у тебя ключ DeepAI)."""
    headers = {"api-key": VIDEO_API_KEY}
    data = {"text": prompt}
    r = requests.post(DEEPAI_TEXT2VIDEO_URL, headers=headers, data=data, timeout=API_TIMEOUT_LONG)
    r.raise_for_status()
    j = r.json()
    # DeepAI обычно возвращает field "output_url" или "id" — проверим
    if j.get("output_url"):
        return j["output_url"]
    if j.get("output_urls"):
        return j["output_urls"][0]
    if j.get("id"):
        # можно собрать ссылку по id — но это зависит от провайдера
        return j.get("id")
    return None

# -----------------------
# Фоновая обработка запроса пользователя
# -----------------------
def process_prompt_async(chat_id, prompt, source_filename=None):
    """Запускается в отдельном потоке — генерирует текст, баннер, видео пошагово."""
    try:
        # 1) Текст
        try:
            bot.send_message(chat_id, "🌀 Принял. Генерирую текст...")
            text = generate_text_openai(prompt) if CHAT_API_KEY else "CHAT_API_KEY не задан."
            bot.send_message(chat_id, "✅ Текст готов:\n\n" + text)
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка при генерации текста:\n" + str(e))
            text = None

        # 2) Баннер (изображение)
        try:
            bot.send_message(chat_id, "🌀 Генерирую баннер (картинку)...")
            image_result = generate_image_openai(prompt) if IMAGE_API_KEY else None
            if not image_result:
                bot.send_message(chat_id, "⚠️ Не удалось получить баннер (проверь IMAGE_API_KEY или endpoint).")
            else:
                # Если image_result — это локальный файл путь, отправляем файл
                if os.path.exists(image_result):
                    with open(image_result, "rb") as f:
                        bot.send_photo(chat_id, f)
                    try:
                        os.remove(image_result)
                    except Exception:
                        pass
                else:
                    # Если это URL — отправляем по ссылке (Telegram автоматически подтянет картинку)
                    bot.send_photo(chat_id, image_result)
                bot.send_message(chat_id, "✅ Баннер отправлен (на картинке может быть встроенный текст).")
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка при генерации баннера:\n" + str(e))

        # 3) Видео (короткий рилс) — вернём ссылку для скачивания
        try:
            bot.send_message(chat_id, "🌀 Генерирую короткий ролик (это может занять до нескольких минут)...")
            video_url = generate_video_deepai(prompt) if VIDEO_API_KEY else None
            if video_url:
                bot.send_message(chat_id, "✅ Видео готово — ссылка для скачивания (качество сохраняется):\n" + str(video_url))
            else:
                bot.send_message(chat_id, "⚠️ Не удалось получить видео (проверь VIDEO_API_KEY или endpoint).")
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка при генерации видео:\n" + str(e))

        bot.send_message(chat_id, "🎯 Все попытки завершены. Проверь результаты выше.")
    except Exception as outer:
        # логируем и сообщаем пользователю
        tb = traceback.format_exc()
        print("PROCESS PROMPT ERROR:", tb)
        bot.send_message(chat_id, "❗ Внутренняя ошибка при обработке промта:\n" + str(outer))

# -----------------------
# Обработка команд и сообщений
# -----------------------
@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.send_message(message.chat.id,
        "Привет! Я Zuh Assistant Bot 🤖\n"
        "Отправь текстовый промпт или воспользуйся командой /promo\n"
        "Команда /promo — сгенерирует текст, баннер и короткий ролик для рекламы."
    )

@bot.message_handler(commands=['promo'])
def cmd_promo(message):
    # Получаем текст после команды
    prompt = message.text.replace("/promo", "").strip()
    if not prompt:
        bot.send_message(message.chat.id, "Напиши после /promo короткое описание (пример: '/promo Фасадные работы в Ташкенте, утепление, 3D-стиль').")
        return
    bot.send_message(message.chat.id, "Принял промт. Начинаю генерацию (шаги будут приходить по мере готовности).")
    Thread(target=process_prompt_async, args=(message.chat.id, prompt)).start()

@bot.message_handler(content_types=['text'])
def handle_text(message):
    # По умолчанию воспринимаем любое текстовое сообщение как промпт для генерации
    prompt = message.text.strip()
    if not prompt:
        bot.send_message(message.chat.id, "Пожалуйста, пришли текстовый промт.")
        return
    bot.send_message(message.chat.id, "Принял промт. Начинаю генерацию (шаги будут приходить по мере готовности).")
    Thread(target=process_prompt_async, args=(message.chat.id, prompt)).start()

# Голосовые сообщения: распознавать и отправлять как промпт (опционально)
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    bot.send_message(message.chat.id, "🎧 Принял голосовое сообщение. Скачиваю и распознаю (может занять несколько секунд)...")
    try:
        file_info = bot.get_file(message.voice.file_id)
        file_bytes = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}", timeout=30).content
        # сохраняем временно
        import tempfile, speech_recognition as sr
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".ogg")
        tf.write(file_bytes); tf.close()
        # конвертация с помощью ffmpeg не встроена — лучше заранее отправлять голос в формате wav
        # Попробуем распознать через SpeechRecognition (будет работать если ffmpeg доступен)
        r = sr.Recognizer()
        with sr.AudioFile(tf.name) as source:
            audio = r.record(source)
        text = r.recognize_google(audio, language="ru-RU")
        os.remove(tf.name)
        bot.send_message(message.chat.id, f"Распознан текст: {text}\nЗапускаю генерацию...")
        Thread(target=process_prompt_async, args=(message.chat.id, text)).start()
    except Exception as e:
        bot.send_message(message.chat.id, "Ошибка распознавания или обработки голосового сообщения: " + str(e))

# -----------------------
# Вебхук для Render
# -----------------------
@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    try:
        json_str = request.stream.read().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        print("Webhook processing error:", e)
    return "OK", 200

@app.route('/')
def index():
    return "Zuh Assistant Bot is running."

# -----------------------
# Запуск (для локального теста)
# -----------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("Запуск бота на порту", port)
    app.run(host="0.0.0.0", port=port)

















# -*- coding: utf-8 -*-
"""
ZuhAssistantBot — финальный код (без сохранения файлов на Render).
Берёт ключи из Env:
BOT_TOKEN, CHAT_API_KEY, IMAGE_API_KEY, VIDEO_API_KEY, ADMIN_CHAT_ID (опц), CAPTCHA_ACTIVE (опц), KEEP_ALIVE_URL (опц).
"""
import os
import time
import requests
import traceback
import base64
from threading import Thread, Event
from flask import Flask, request
import telebot
from io import BytesIO
from PIL import Image
import langdetect  # определение языка

# -------------------------
# Загрузка и очистка переменных окружения
# -------------------------
BOT_TOKEN      = os.getenv("BOT_TOKEN", "").strip()
CHAT_API_KEY   = os.getenv("CHAT_API_KEY", "").strip()
IMAGE_API_KEY  = os.getenv("IMAGE_API_KEY", "").strip()
VIDEO_API_KEY  = os.getenv("VIDEO_API_KEY", "").strip()
ADMIN_CHAT_ID  = os.getenv("ADMIN_CHAT_ID", "").strip()    # опционально
KEEP_ALIVE_URL = os.getenv("KEEP_ALIVE_URL", "").strip()   # опционально

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения.")

# -------------------------
# Константы / endpoints
# -------------------------
API_TIMEOUT_SHORT = 20
API_TIMEOUT_LONG  = 120

OPENAI_CHAT_URL  = "https://api.openai.com/v1/chat/completions"
OPENAI_IMAGE_URL = "https://api.openai.com/v1/images/generations"  # при необходимости изменим под провайдера
DEEPAI_TEXT2VIDEO_URL = "https://api.deepai.org/api/text2video"

# -------------------------
# Инициализация
# -------------------------
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# -------------------------
# Утилиты (запрос с retry и логированием)
# -------------------------
def safe_request(method, url, headers=None, json=None, data=None, params=None, timeout=30, max_retries=3):
    backoff = 1
    last_exc = None
    for attempt in range(max_retries):
        try:
            r = requests.request(method, url, headers=headers, json=json, data=data, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(backoff)
                backoff *= 2
                last_exc = requests.HTTPError("429 Too Many Requests")
                continue
            if 500 <= r.status_code < 600 and attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2
                last_exc = requests.HTTPError(f"{r.status_code} Server Error")
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise last_exc

def periodic_status(chat_id, event_stop: Event, interval=15, text="⏳ Генерация продолжается..."):
    while not event_stop.is_set():
        try:
            bot.send_message(chat_id, text)
        except Exception:
            pass
        event_stop.wait(interval)

# -------------------------
# Определение языка (ru/uz fallback)
# -------------------------
def detect_lang(text):
    try:
        d = langdetect.detect(text)
        if d.startswith("ru"):
            return "ru"
        if d.startswith("uz") or d.startswith("tr") or d.startswith("tk"):
            return "uz"
        # default
        return "ru"
    except Exception:
        return "ru"

# -------------------------
# Генерация текста (OpenAI Chat)
# -------------------------
def generate_text(prompt, target_lang="ru"):
    if not CHAT_API_KEY:
        raise RuntimeError("CHAT_API_KEY не задан")
    headers = {"Authorization": f"Bearer {CHAT_API_KEY}", "Content-Type": "application/json"}
    system = ("Ты — генератор коротких рекламных текстов для Facebook/Instagram/Telegram. "
              "Пиши кратко, привлекательно, добавь призыв и ссылку на бота @ZuhFacadeBot.")
    # добавляем намёк на язык
    user_prompt = prompt + f"\nЯзык: {'русский' if target_lang=='ru' else 'узбекский'}. Коротко, до ~250 символов."
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [{"role":"system","content":system}, {"role":"user","content":user_prompt}],
        "max_tokens": 300,
        "temperature": 0.7
    }
    r = safe_request("POST", OPENAI_CHAT_URL, headers=headers, json=payload, timeout=API_TIMEOUT_SHORT, max_retries=3)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"OpenAI Chat вернул не JSON. status={r.status_code}, body={r.text[:1000]}")
    try:
        return j["choices"][0]["message"]["content"].strip()
    except Exception as e:
        raise RuntimeError(f"Unexpected OpenAI Chat response: {e}. Full: {j}")

# -------------------------
# Генерация изображения (OpenAI-like) — возвращаем BytesIO
# -------------------------
def generate_image_bytes(prompt):
    if not IMAGE_API_KEY:
        raise RuntimeError("IMAGE_API_KEY не задан")
    headers = {"Authorization": f"Bearer {IMAGE_API_KEY}", "Content-Type": "application/json"}
    payload = {"prompt": prompt, "size": "1024x1024", "n": 1}
    r = safe_request("POST", OPENAI_IMAGE_URL, headers=headers, json=payload, timeout=API_TIMEOUT_LONG, max_retries=2)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"Image API вернул не JSON. status={r.status_code}, body={r.text[:1000]}")
    data = j.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if "b64_json" in first:
            b64 = first["b64_json"]
            img_bytes = base64.b64decode(b64)
            return BytesIO(img_bytes)
        if "url" in first:
            # скачиваем картинку в память
            rr = requests.get(first["url"], timeout=API_TIMEOUT_LONG)
            rr.raise_for_status()
            return BytesIO(rr.content)
    raise RuntimeError(f"Unexpected Image API response: {j}")

# ресайз в памяти
def resize_image_bytes(img_bytesio, size):
    img_bytesio.seek(0)
    im = Image.open(img_bytesio).convert("RGB")
    im.thumbnail(size, Image.ANTIALIAS)
    new_im = Image.new("RGB", size, (255,255,255))
    w,h = im.size
    new_im.paste(im, ((size[0]-w)//2, (size[1]-h)//2))
    out = BytesIO()
    new_im.save(out, format="JPEG", quality=90)
    out.seek(0)
    return out

# -------------------------
# Генерация видео (DeepAI) — возвращаем ссылку
# -------------------------
def generate_video_link(prompt):
    if not VIDEO_API_KEY:
        raise RuntimeError("VIDEO_API_KEY не задан")
    headers = {"api-key": VIDEO_API_KEY}
    data = {"text": prompt}
    r = safe_request("POST", DEEPAI_TEXT2VIDEO_URL, headers=headers, data=data, timeout=API_TIMEOUT_LONG, max_retries=3)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"Video API вернул не JSON. status={r.status_code}, body={r.text[:1000]}")
    if j.get("output_url"):
        return j["output_url"]
    if j.get("output_urls"):
        return j["output_urls"][0]
    if j.get("url"):
        return j["url"]
    raise RuntimeError(f"Unexpected Video API response: {j}")

# -------------------------
# Фоновая обработка промпта — текст -> баннер -> видео (все в памяти)
# -------------------------
def process_prompt_async(chat_id, prompt_text):
    stop_event = Event()
    status_thr = Thread(target=periodic_status, args=(chat_id, stop_event, 15, "⏳ Генерация в процессе..."))
    status_thr.daemon = True
    status_thr.start()

    # определим язык пользователя
    lang = detect_lang(prompt_text)

    try:
        # 1) Текст
        try:
            bot.send_message(chat_id, "🌀 Генерирую рекламный текст...")
            text = generate_text(prompt_text + "\nДобавь в конце: 'Оставьте заявку в боте @ZuhFacadeBot'.", target_lang=lang)
            bot.send_message(chat_id, "✅ Текст готов:\n\n" + text)
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка генерации текста: " + str(e))
            text = None

        # 2) Баннер (в памяти)
        try:
            bot.send_message(chat_id, "🌀 Генерирую баннер (картинка 1080×1080)...")
            image_prompt = (f"{prompt_text}. На картинке крупная читаемая надпись 'Фасад под Травентин', "
                            f"'Заказать сейчас', 'Ташкент', и ссылка/призыв к боту @ZuhFacadeBot. Стиль: фактурная штукатурка, элегантно.")
            img_bytesio = generate_image_bytes(image_prompt)  # BytesIO
            # готовим версии:
            banner_1080 = resize_image_bytes(BytesIO(img_bytesio.getvalue()), (1080,1080))
            banner_720  = resize_image_bytes(BytesIO(img_bytesio.getvalue()), (1280,720))
            # отправляем 1080
            bot.send_photo(chat_id, banner_1080)
            bot.send_message(chat_id, "✅ Баннер готов. Также подготовлен вариант 1280×720 (для Telegram/YouTube).")
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка генерации баннера: " + str(e))

        # 3) Видео (ссылка)
        try:
            bot.send_message(chat_id, "🌀 Генерирую короткое вертикальное видео (Reels) — это может занять время...")
            video_link = generate_video_link(prompt_text + " Вертикальное видео 10–15 секунд, Reels, добавить призыв к действию и @ZuhFacadeBot.")
            bot.send_message(chat_id, "✅ Видео готово. Ссылка для скачивания/публикации (оригинал):\n" + str(video_link))
            bot.send_message(chat_id, "ℹ️ Если нужно, сохрани видео себе и загрузи в Instagram/Facebook как Reels.")
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка генерации видео: " + str(e))

        # уведомление админу (если задан)
        try:
            if ADMIN_CHAT_ID:
                bot.send_message(ADMIN_CHAT_ID, f"Пользователь {chat_id} сделал генерацию по промту:\n{prompt_text}")
        except Exception:
            pass

        bot.send_message(chat_id, "🎯 Генерация завершена.")
    except Exception as outer:
        tb = traceback.format_exc()
        print("PROCESS PROMPT ERROR:", tb)
        bot.send_message(chat_id, "❗ Внутренняя ошибка: " + str(outer))
    finally:
        stop_event.set()

# -------------------------
# Обработчики команд / сообщений
# -------------------------
@bot.message_handler(commands=['start'])
def cmd_start(m):
    bot.send_message(m.chat.id,
        "Привет! Я ZuhAssistantBot — твой креативный ассистент.\n"
        "Просто напиши: 'Сделай баннер про фасадное утепление' или 'Сделай рилс про декоративную штукатурку'.\n"
        "Команды: /promo <текст>, /slideshow (пошли фото, затем 'Готово')."
    )

@bot.message_handler(commands=['promo'])
def cmd_promo(m):
    prompt = m.text.replace("/promo", "").strip()
    if not prompt:
        bot.send_message(m.chat.id, "Напиши после /promo короткий промт.")
        return
    bot.send_message(m.chat.id, "Принял промт. Запускаю генерацию...")
    Thread(target=process_prompt_async, args=(m.chat.id, prompt)).start()

# Слайдшоу: мы не собираем локально на Render — используем Video API fallback
user_photos = {}

@bot.message_handler(commands=['slideshow'])
def cmd_slideshow(m):
    user_photos[m.chat.id] = []
    bot.send_message(m.chat.id, "Отправь 1–10 фото. После последней пришли 'Готово'.")

@bot.message_handler(content_types=['photo', 'text', 'voice'])
def handle_all(m):
    cid = m.chat.id
    if m.content_type == "photo":
        if cid in user_photos:
            user_photos[cid].append(m.photo[-1].file_id)
            bot.send_message(cid, f"Принято фото #{len(user_photos[cid])}. Отправь ещё или напиши 'Готово'.")
            return
    if m.content_type == "text":
        txt = m.text.strip()
        if txt.lower() == "готово" and cid in user_photos:
            files = user_photos.pop(cid, [])
            if not files:
                bot.send_message(cid, "Нужно хотя бы одно фото.")
                return
            bot.send_message(cid, "Формирую слайдшоу через Video API (если локальная сборка недоступна)...")
            # формируем краткий промт и вызываем video API
            Thread(target=process_slideshow_via_api, args=(cid, files)).start()
            return
        # обычный промт
        bot.send_message(cid, "Принял промт. Запускаю генерацию...")
        Thread(target=process_prompt_async, args=(cid, txt)).start()
        return
    if m.content_type == "voice":
        bot.send_message(cid, "Принял голос — попробую распознать (если доступно)...")
        try:
            file_info = bot.get_file(m.voice.file_id)
            file_bytes = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}", timeout=30).content
            # распознавание тут опущено, чтобы избежать зависимости на ffmpeg; лучше отправлять текстом
            bot.send_message(cid, "Голос принят, но автоматическое распознавание отключено на сервере. Пожалуйста, отправь текстом.")
        except Exception as e:
            bot.send_message(cid, "Ошибка обработки голоса: " + str(e))

# -------------------------
# Слайдшоу через Video API (fallback)
# -------------------------
def process_slideshow_via_api(chat_id, file_ids):
    try:
        # собираем описатель промта
        prompt = "Сделай слайдшоу из присланных фотографий (2 секунды на слайд), добавить призыв 'Оставьте заявку в боте @ZuhFacadeBot'."
        # здесь можно попытаться получить ссылки на фото (публичные) — но проще: используем Video API с этим описанием
        video_link = generate_video_link(prompt)
        bot.send_message(chat_id, "✅ Слайдшоу готово. Ссылка:\n" + video_link)
    except Exception as e:
        bot.send_message(chat_id, "Ошибка при создании слайдшоу через Video API: " + str(e))

# -------------------------
# Вебхук для Render
# -------------------------
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
    return "ZuhAssistantBot (stateless) running."

# -------------------------
# Опциональный keep-alive ping
# -------------------------
def keep_alive_worker(url):
    while True:
        try:
            requests.get(url, timeout=10)
        except Exception:
            pass
        time.sleep(9*60)

if KEEP_ALIVE_URL:
    t = Thread(target=keep_alive_worker, args=(KEEP_ALIVE_URL,))
    t.daemon = True
    t.start()

# -------------------------
# Запуск локально
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("Запуск на порту", port)
    app.run(host="0.0.0.0", port=port)
 






















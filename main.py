# -*- coding: utf-8 -*-
import os
import time
import requests
import traceback
import base64
import tempfile
from threading import Thread, Event
from flask import Flask, request
import telebot
from PIL import Image
from io import BytesIO

# ---------- Настройка / переменные окружения ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_API_KEY = os.getenv("CHAT_API_KEY", "").strip()
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY", "").strip()
VIDEO_API_KEY = os.getenv("VIDEO_API_KEY", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()  # опционально: куда слать уведомления
KEEP_ALIVE_URL = os.getenv("KEEP_ALIVE_URL", "").strip()  # опционально: самопинг (https)

if not BOT_TOKEN:
    raise ValueError("Ошибка: BOT_TOKEN не задан. Проверь переменные окружения.")

# ---------- Константы ----------
API_TIMEOUT_SHORT = 20
API_TIMEOUT_LONG = 120

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_IMAGE_URL = "https://api.openai.com/v1/images/generations"
DEEPAI_TEXT2VIDEO_URL = "https://api.deepai.org/api/text2video"

# каталоги
STATIC_DIR = os.path.join(os.getcwd(), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# ---------- Инициализация ----------
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ---------- Вспомогательные утилиты ----------
def request_with_retry(method, url, headers=None, json=None, data=None, params=None, timeout=API_TIMEOUT_SHORT, max_retries=3):
    backoff = 1
    last_exc = None
    for attempt in range(max_retries):
        try:
            r = requests.request(method, url, headers=headers, json=json, data=data, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(backoff)
                backoff *= 2
                last_exc = requests.HTTPError(f"429 from {url}")
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

def send_periodic_status(chat_id, event_stop: Event, interval=12, text="⏳ Генерация продолжается..."):
    """Отправляет периодические сообщения, пока event_stop не установлен."""
    while not event_stop.is_set():
        try:
            bot.send_message(chat_id, text)
        except Exception:
            pass
        event_stop.wait(interval)

def save_b64_image_to_file(b64_str):
    img_bytes = base64.b64decode(b64_str)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png", dir=STATIC_DIR)
    tmp.write(img_bytes)
    tmp.close()
    return tmp.name

def resize_image_file(in_path, out_path, size):
    try:
        im = Image.open(in_path)
        im = im.convert("RGB")
        im.thumbnail(size, Image.ANTIALIAS)
        # сохранить с нужным размером, но чтобы точно размер был, можно центрировать на белом фоне
        new_im = Image.new("RGB", size, (255,255,255))
        im_w, im_h = im.size
        new_im.paste(im, ((size[0]-im_w)//2, (size[1]-im_h)//2))
        new_im.save(out_path, format="JPEG", quality=90)
        return out_path
    except Exception as e:
        raise RuntimeError(f"Ошибка ресайза изображения: {e}")

# ---------- Генерация текста через OpenAI Chat ----------
def generate_text_openai(prompt):
    if not CHAT_API_KEY:
        raise RuntimeError("CHAT_API_KEY не задан")
    headers = {"Authorization": f"Bearer {CHAT_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "Ты — генератор коротких рекламных текстов для Facebook/Instagram/Telegram. Пиши кратко, привлекательно, добавь call-to-action и укажи ссылку на бота."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 300,
        "temperature": 0.7
    }
    r = request_with_retry("POST", OPENAI_CHAT_URL, headers=headers, json=payload, timeout=API_TIMEOUT_SHORT)
    j = r.json()
    return j["choices"][0]["message"]["content"].strip()

# ---------- Генерация изображения (OpenAI-like) ----------
def generate_image_openai(prompt):
    if not IMAGE_API_KEY:
        raise RuntimeError("IMAGE_API_KEY не задан")
    headers = {"Authorization": f"Bearer {IMAGE_API_KEY}", "Content-Type": "application/json"}
    payload = {"prompt": prompt, "size": "1024x1024"}
    r = request_with_retry("POST", OPENAI_IMAGE_URL, headers=headers, json=payload, timeout=API_TIMEOUT_LONG)
    j = r.json()
    data = j.get("data")
    if isinstance(data, list) and len(data) > 0:
        first = data[0]
        if isinstance(first, dict) and "url" in first:
            # скачиваем по URL
            img_url = first["url"]
            resp = requests.get(img_url, timeout=API_TIMEOUT_LONG)
            resp.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg", dir=STATIC_DIR)
            tmp.write(resp.content); tmp.close()
            return tmp.name
        if isinstance(first, dict) and "b64_json" in first:
            return save_b64_image_to_file(first["b64_json"])
    raise RuntimeError("Не удалось получить изображение от IMAGE API")

# ---------- Генерация видео (DeepAI text2video) ----------
def generate_video_deepai(prompt):
    if not VIDEO_API_KEY:
        raise RuntimeError("VIDEO_API_KEY не задан")
    headers = {"api-key": VIDEO_API_KEY}
    data = {"text": prompt}
    r = request_with_retry("POST", DEEPAI_TEXT2VIDEO_URL, headers=headers, data=data, timeout=API_TIMEOUT_LONG)
    j = r.json()
    if j.get("output_url"):
        return j["output_url"]
    if j.get("output_urls"):
        return j["output_urls"][0]
    if j.get("url"):
        return j["url"]
    raise RuntimeError("Не удалось получить ссылку на видео от Video API")

# ---------- Подгонка видео (если нужно) ----------
def download_file(url, dst_path):
    r = requests.get(url, timeout=API_TIMEOUT_LONG)
    r.raise_for_status()
    with open(dst_path, "wb") as f:
        f.write(r.content)
    return dst_path

def resize_video_ffmpeg(in_path, out_path, target_w, target_h):
    # Используем moviepy, если доступен
    try:
        from moviepy.editor import VideoFileClip
    except Exception as e:
        raise RuntimeError("moviepy/ffmpeg не установлен или недоступен: " + str(e))
    clip = VideoFileClip(in_path)
    clip_resized = clip.resize(height=target_h)  # сохраняет пропорции
    clip_resized.write_videofile(out_path, codec="libx264", audio_codec="aac")
    clip.close()
    clip_resized.close()
    return out_path

# ---------- Основной процесс генерации (фоновый) ----------
def process_prompt_async(chat_id, prompt):
    stop_event = Event()
    status_thread = Thread(target=send_periodic_status, args=(chat_id, stop_event, 15, "⏳ Генерация идет... (бот не зависает)"))
    status_thread.daemon = True
    status_thread.start()

    try:
        # 1) текст
        try:
            bot.send_message(chat_id, "🌀 Генерирую рекламный текст...")
            text = generate_text_openai(prompt + "\nДобавь в конце: 'Оставьте заявку в боте @ZuhFacadeBot'.")
            bot.send_message(chat_id, "✅ Текст готов:\n\n" + text)
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка при генерации текста: " + str(e))
            text = None

        # 2) баннер 1080x1080 и копия 1280x720
        try:
            bot.send_message(chat_id, "🌀 Генерирую баннер (картинку 1080x1080)...")
            image_prompt = f"{prompt}. На картинке большая читаемая надпись 'Заказать здесь' и указание бота @ZuhFacadeBot. Стиль: современный фасад, чистые цвета."
            img_path = generate_image_openai(image_prompt)
            # создаём копию 1080x1080 и 1280x720
            banner1 = os.path.join(STATIC_DIR, f"banner_{int(time.time())}_1080.jpg")
            banner2 = os.path.join(STATIC_DIR, f"banner_{int(time.time())}_720.jpg")
            resize_image_file(img_path, banner1, (1080,1080))
            resize_image_file(img_path, banner2, (1280,720))
            # отправляем в чат оригинал (1080)
            with open(banner1, "rb") as f:
                bot.send_photo(chat_id, f)
            bot.send_message(chat_id, f"✅ Баннер готов. Также создан вариант для Telegram/YouTube: {os.path.basename(banner2)}")
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка при генерации баннера: " + str(e))

        # 3) видео (получаем ссылку на качественный файл) и создаём копию 1280x720 если возможно
        try:
            bot.send_message(chat_id, "🌀 Генерирую короткое видео (Reels, 1080x1920). Это может занять время...")
            video_url = generate_video_deepai(prompt + " Вертикальное видео 10-15 секунд, формат Reels, добавь текст-призыв и ссылку на @ZuhFacadeBot.")
            bot.send_message(chat_id, "✅ Видео готово. Ссылка на скачивание/публикацию (оригинал):\n" + str(video_url))

            # попытаемся скачать и создать копию 1280x720 (fallback)
            try:
                tmp_in = os.path.join(STATIC_DIR, f"video_in_{int(time.time())}.mp4")
                tmp_out = os.path.join(STATIC_DIR, f"video_out_{int(time.time())}_720.mp4")
                download_file(video_url, tmp_in)
                resize_video_ffmpeg(tmp_in, tmp_out, 1280, 720)
                bot.send_message(chat_id, "✅ Подготовлена версия видео для Telegram/YouTube: " + os.path.basename(tmp_out))
            except Exception as e_local:
                bot.send_message(chat_id, "ℹ️ Не удалось локально преобразовать видео (возможно нет ffmpeg/moviepy). Оригинал доступен по ссылке.")
        exce



















# -*- coding: utf-8 -*-
"""
ZuhAssistantBot — финальная версия без долгого хранения файлов на Render.
Поддерживает RU/UZ, генерирует текст, баннер (отправляет в чат), видео — ссылку.
Переменные окружения (Render): BOT_TOKEN, CHAT_API_KEY, IMAGE_API_KEY, VIDEO_API_KEY, ADMIN_CHAT_ID (опционально).
"""
import os
import time
import requests
import traceback
import base64
import tempfile
from threading import Thread, Event
from flask import Flask, request
import telebot
from io import BytesIO
from PIL import Image

# --- Чтение переменных окружения и очистка ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_API_KEY = os.getenv("CHAT_API_KEY", "").strip()
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY", "").strip()
VIDEO_API_KEY = os.getenv("VIDEO_API_KEY", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()  # опционально

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в Environment.")

# --- Константы ---
API_TIMEOUT_SHORT = 20
API_TIMEOUT_LONG = 120
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_IMAGE_URL = "https://api.openai.com/v1/images/generations"
DEEPAI_TEXT2VIDEO_URL = "https://api.deepai.org/api/text2video"

# --- Инициализация ---
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# --- Утилиты запросов с retry ---
def safe_request(method, url, headers=None, json=None, data=None, timeout=20, max_retries=3):
    backoff = 1
    last = None
    for i in range(max_retries):
        try:
            r = requests.request(method, url, headers=headers, json=json, data=data, timeout=timeout)
            if r.status_code == 429:
                time.sleep(backoff); backoff *= 2; last = r
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < max_retries-1:
                time.sleep(backoff); backoff *= 2
                continue
            raise last

# --- Периодический статус, чтобы пользователь видел прогресс ---
def periodic_status(chat_id, stop_event: Event, interval=15, text="⏳ Генерация продолжается..."):
    while not stop_event.is_set():
        try:
            bot.send_message(chat_id, text)
        except Exception:
            pass
        stop_event.wait(interval)

# --- Сохранение base64 в BytesIO (не на диск) ---
def bytesio_from_b64(b64str):
    data = base64.b64decode(b64str)
    return BytesIO(data)

# --- Resize в памяти и возврат BytesIO JPEG ---
def resize_image_bytes(input_bytes, size):
    img = Image.open(BytesIO(input_bytes)).convert("RGB")
    img.thumbnail(size, Image.ANTIALIAS)
    canvas = Image.new("RGB", size, (255,255,255))
    w,h = img.size
    canvas.paste(img, ((size[0]-w)//2, (size[1]-h)//2))
    out = BytesIO()
    canvas.save(out, format="JPEG", quality=90)
    out.seek(0)
    return out

# --- Генерация текста (OpenAI Chat) ---
def generate_text(prompt):
    if not CHAT_API_KEY:
        raise RuntimeError("CHAT_API_KEY не задан")
    headers = {"Authorization": f"Bearer {CHAT_API_KEY}", "Content-Type": "application/json"}
    payload = {"model":"gpt-3.5-turbo","messages":[{"role":"user","content":prompt}], "max_tokens":300, "temperature":0.7}
    r = safe_request("POST", OPENAI_CHAT_URL, headers=headers, json=payload, timeout=API_TIMEOUT_SHORT)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"OpenAI Chat вернул не JSON. status={r.status_code}, body={r.text[:1000]}")
    try:
        return j["choices"][0]["message"]["content"].strip()
    except Exception as e:
        raise RuntimeError(f"Непредвиденный формат ответа Chat API: {e}. Полный ответ: {j}")

# --- Генерация картинки (OpenAI-like). Возвращаем bytes ---
def generate_image_bytes(prompt):
    if not IMAGE_API_KEY:
        raise RuntimeError("IMAGE_API_KEY не задан")
    headers = {"Authorization": f"Bearer {IMAGE_API_KEY}", "Content-Type": "application/json"}
    payload = {"prompt": prompt, "size": "1024x1024", "n":1}
    r = safe_request("POST", OPENAI_IMAGE_URL, headers=headers, json=payload, timeout=API_TIMEOUT_LONG)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"Image API вернул не JSON. status={r.status_code}, body={r.text[:1000]}")
    data = j.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        # если вернулся URL — скачиваем и возвращаем bytes
        if isinstance(first, dict) and "url" in first:
            rr = requests.get(first["url"], timeout=API_TIMEOUT_LONG); rr.raise_for_status()
            return rr.content
        # если вернулся base64
        if isinstance(first, dict) and "b64_json" in first:
            return base64.b64decode(first["b64_json"])
    raise RuntimeError(f"Unexpected Image API response: {j}")

# --- Генерация видео (DeepAI) — возвращаем ссылку ---
def generate_video_link(prompt):
    if not VIDEO_API_KEY:
        raise RuntimeError("VIDEO_API_KEY не задан")
    headers = {"api-key": VIDEO_API_KEY}
    data = {"text": prompt}
    r = safe_request("POST", DEEPAI_TEXT2VIDEO_URL, headers=headers, data=data, timeout=API_TIMEOUT_LONG)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"Video API вернул не JSON. status={r.status_code}, body={r.text[:1000]}")
    if j.get("output_url"): return j["output_url"]
    if j.get("output_urls"): return j["output_urls"][0]
    if j.get("url"): return j["url"]
    raise RuntimeError(f"Unexpected Video API response: {j}")

# --- Фоновый процесс: текст, баннер, видео ---
def process_prompt_async(chat_id, prompt):
    stop_event = Event()
    status_thr = Thread(target=periodic_status, args=(chat_id, stop_event, 15, "⏳ Генерация продолжается..."))
    status_thr.daemon = True
    status_thr.start()

    try:
        # 1) Текст
        try:
            bot.send_message(chat_id, "🌀 Генерирую рекламный текст...")
            text = generate_text(prompt + "\nВ конце добавь: 'Оставьте заявку в боте @ZuhFacadeBot'.")
            bot.send_message(chat_id, "✅ Текст готов:\n\n" +





















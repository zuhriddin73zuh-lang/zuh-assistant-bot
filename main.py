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
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка при генерации видео: " + str(e))

        # уведомление админу
        try:
            if ADMIN_CHAT_ID:
                bot.send_message(ADMIN_CHAT_ID, f"Пользователь {chat_id} запустил генерацию по промту:\n{prompt}")
        except Exception:
            pass

        bot.send_message(chat_id, "🎯 Генерация завершена. Проверь результаты выше.")
    except Exception as outer:
        tb = traceback.format_exc()
        print("PROCESS_PROMPT ERROR:", tb)
        bot.send_message(chat_id, "❗ Внутренняя ошибка при обработке промта: " + str(outer))
    finally:
        stop_event.set()

# ---------- Обработчики команд и сообщений ----------
@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.send_message(message.chat.id,
                     "Привет! Я ZuhAssistantBot — твой креативный ассистент.\n"
                     "Просто напиши: 'Сделай баннер про фасадное утепление' или 'Сделай рилс про декоративную штукатурку'.\n"
                     "Команды: /promo <текст>, /slideshow (отправь фото потом 'Готово')."
                    )

@bot.message_handler(commands=['promo'])
def cmd_promo(message):
    prompt = message.text.replace("/promo", "").strip()
    if not prompt:
        bot.send_message(message.chat.id, "Напиши после /promo короткий промт.")
        return
    bot.send_message(message.chat.id, "Принял промт. Запускаю генерацию...")
    Thread(target=process_prompt_async, args=(message.chat.id, prompt)).start()

@bot.message_handler(commands=['slideshow'])
def cmd_slideshow(message):
    bot.send_message(message.chat.id, "Отправь 1–10 фотографий. После последней пришли слово 'Готово'.")
    # слайдшоу реализовано отдельно через state (ниже)

# state для slideshow
user_slideshow = {}

@bot.message_handler(content_types=['photo', 'text'])
def handle_all(message):
    # если фото для слайдшоу
    if message.content_type == 'photo':
        # сохраняем file_id
        chat_id = message.chat.id
        if chat_id in user_slideshow:
            user_slideshow[chat_id].append(message.photo[-1].file_id)
            bot.send_message(chat_id, f"Принял фото #{len(user_slideshow[chat_id])}. Отправь ещё или напиши 'Готово'.")
            return
    if message.content_type == 'text':
        text = message.text.strip()
        if text.lower() == "готово":
            chat_id = message.chat.id
            photos = user_slideshow.get(chat_id, [])
            if not photos:
                bot.send_message(chat_id, "Нужно прислать хотя бы одно фото перед 'Готово'.")
                return
            bot.send_message(chat_id, "Собираю слайдшоу... Это может занять время.")
            Thread(target=process_slideshow, args=(chat_id, photos)).start()
            user_slideshow.pop(chat_id, None)
            return
        # иначе — обычный промт
        bot.send_message(message.chat.id, "Принял промт. Запускаю генерацию...")
        Thread(target=process_prompt_async, args=(message.chat.id, text)).start()

# ---------- Сборка слайдшоу (фон) ----------
def process_slideshow(chat_id, photo_file_ids):
    stop_event = Event()
    Thread(target=send_periodic_status, args=(chat_id, stop_event, 15, "⏳ Сборка слайдшоу...")).start()
    try:
        tmp_files = []
        for fid in photo_file_ids:
            f_info = bot.get_file(fid)
            file_bytes = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f_info.file_path}", timeout=30).content
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg", dir=STATIC_DIR)
            tmp.write(file_bytes); tmp.close()
            tmp_files.append(tmp.name)
        # пробуем собрать локально
        try:
            out_path = os.path.join(STATIC_DIR, f"slideshow_{int(time.time())}.mp4")
            from moviepy.editor import ImageClip, concatenate_videoclips
            clips = [ImageClip(p).set_duration(2).resize(width=720) for p in tmp_files]
            video = concatenate_videoclips(clips, method="compose")
            video.write_videofile(out_path, fps=24, codec="libx264", audio=False)
            bot.send_video(chat_id, open(out_path, "rb"))
            bot.send_message(chat_id, "✅ Слайдшоу готово и отправлено.")
            try: os.remove(out_path)
            except: pass
        except Exception as e_local:
            # fallback: создать описание и попробовать Video API
            try:
                bot.send_message(chat_id, "Локальная сборка не удалась. Пытаюсь через Video API...")
                video_link = generate_video_deepai("Сделай слайдшоу из присланных фотографий, каждый слайд 2 секунды, добавь призыв к действию и ссылку на @ZuhFacadeBot")
                bot.send_message(chat_id, "✅ Слайдшоу (через Video API) готово. Ссылка:\n" + video_link)
            except Exception as e_api:
                bot.send_message(chat_id, "❗ Ошибка при создании слайдшоу: " + str(e_api))
        # очистка временных фото
        for p in tmp_files:
            try: os.remove(p)
            except: pass
    except Exception as outer:
        bot.send_message(chat_id, "Ошибка в процессе слайдшоу: " + str(outer))
    finally:
        stop_event.set()

# ---------- Вебхук для Render ----------
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
    return "ZuhAssistantBot запущен."

# ---------- Keep-alive (опционально) ----------
def keep_alive_worker(url):
    while True:
        try:
            requests.get(url, timeout=10)
        except Exception:
            pass
        time.sleep(9 * 60)  # каждые ~9 минут

if KEEP_ALIVE_URL:
    t = Thread(target=keep_alive_worker, args=(KEEP_ALIVE_URL,))
    t.daemon = True
    t.start()

# ---------- Запуск (локально) ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("Запуск на порту", port)
    app.run(host="0.0.0.0", port=port)


















# -*- coding: utf-8 -*-
"""ZuhAssistantBot — финальный код для Render.
Берёт ключи из Environment:
BOT_TOKEN, CHAT_API_KEY, IMAGE_API_KEY, VIDEO_API_KEY, ADMIN_CHAT_ID (опционально),
CAPTCHA_ACTIVE (опционально, True/False), KEEP_ALIVE_URL (опционально).
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
from PIL import Image
from io import BytesIO

# ---------------------------
# Загрузка и чистка переменных окружения
# ---------------------------
BOT_TOKEN      = os.getenv("BOT_TOKEN", "").strip()
CHAT_API_KEY   = os.getenv("CHAT_API_KEY", "").strip()
IMAGE_API_KEY  = os.getenv("IMAGE_API_KEY", "").strip()
VIDEO_API_KEY  = os.getenv("VIDEO_API_KEY", "").strip()
ADMIN_CHAT_ID  = os.getenv("ADMIN_CHAT_ID", "").strip()    # опционально
CAPTCHA_ACTIVE = os.getenv("CAPTCHA_ACTIVE", "False").strip().lower() == "true"
KEEP_ALIVE_URL = os.getenv("KEEP_ALIVE_URL", "").strip()   # опционально

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения. Проверь Render Environment.")

# ---------------------------
# Константы и эндпоинты (можно менять под другой провайдер)
# ---------------------------
API_TIMEOUT_SHORT = 20
API_TIMEOUT_LONG  = 120

OPENAI_CHAT_URL  = "https://api.openai.com/v1/chat/completions"
OPENAI_IMAGE_URL = "https://api.openai.com/v1/images/generations"  # если у тебя другой провайдер — поменяем
DEEPAI_TEXT2VIDEO_URL = "https://api.deepai.org/api/text2video"

# статическая папка для временных файлов
STATIC_DIR = os.path.join(os.getcwd(), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# ---------------------------
# Инициализация бота и Flask
# ---------------------------
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ---------------------------
# Утилиты
# ---------------------------
def request_with_retry(method, url, headers=None, json=None, data=None, params=None, timeout=API_TIMEOUT_SHORT, max_retries=3):
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
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise last_exc

def save_b64_image(b64_str):
    img_bytes = base64.b64decode(b64_str)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg", dir=STATIC_DIR)
    tmp.write(img_bytes); tmp.close()
    return tmp.name

def resize_image(in_path, out_path, size):
    im = Image.open(in_path).convert("RGB")
    im.thumbnail(size, Image.ANTIALIAS)
    new_im = Image.new("RGB", size, (255,255,255))
    w,h = im.size
    new_im.paste(im, ((size[0]-w)//2, (size[1]-h)//2))
    new_im.save(out_path, format="JPEG", quality=90)
    return out_path

def send_periodic_status(chat_id, stop_event: Event, interval=15, text="⏳ Генерация продолжается..."):
    while not stop_event.is_set():
        try:
            bot.send_message(chat_id, text)
        except Exception:
            pass
        stop_event.wait(interval)

# ---------------------------
# Генерация текста (OpenAI)
# ---------------------------
def generate_text(prompt, lang="ru"):
    if not CHAT_API_KEY:
        raise RuntimeError("CHAT_API_KEY не задан")
    headers = {"Authorization": f"Bearer {CHAT_API_KEY}", "Content-Type": "application/json"}
    system = "Ты — генератор рекламных текстов для Facebook/Instagram/Telegram. Пиши коротко, с призывом и ссылкой на бота @ZuhFacadeBot."
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 300,
        "temperature": 0.7
    }
    r = request_with_retry("POST", OPENAI_CHAT_URL, headers=headers, json=payload, timeout=API_TIMEOUT_SHORT)
    j = r.json()
    return j["choices"][0]["message"]["content"].strip()

# ---------------------------
# Генерация изображения (OpenAI-like)
# ---------------------------
def generate_image(prompt):
    if not IMAGE_API_KEY:
        raise RuntimeError("IMAGE_API_KEY не задан")
    headers = {"Authorization": f"Bearer {IMAGE_API_KEY}", "Content-Type": "application/json"}
    payload = {"prompt": prompt, "size": "1024x1024"}
    r = request_with_retry("POST", OPENAI_IMAGE_URL, headers=headers, json=payload, timeout=API_TIMEOUT_LONG)
    j = r.json()
    data = j.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and "url" in first:
            # скачиваем изображение по URL
            resp = requests.get(first["url"], timeout=API_TIMEOUT_LONG)
            resp.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg", dir=STATIC_DIR)
            tmp.write(resp.content); tmp.close()
            return tmp.name
        if isinstance(first, dict) and "b64_json" in first:
            return save_b64_image(first["b64_json"])
    raise RuntimeError("IMAGE API вернул неожиданный формат")

# ---------------------------
# Генерация видео (DeepAI text2video) — возвращает ссылку
# ---------------------------
def generate_video(prompt):
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
    raise RuntimeError("Video API вернул неожиданный формат")

# ---------------------------
# Скачивание файла
# ---------------------------
def download_file(url, dst):
    r = requests.get(url, timeout=API_TIMEOUT_LONG)
    r.raise_for_status()
    with open(dst, "wb") as f:
        f.write(r.content)
    return dst

# ---------------------------
# Основной фоновой процесс генерации
# ---------------------------
def process_prompt(chat_id, prompt):
    stop_event = Event()
    Thread(target=send_periodic_status, args=(chat_id, stop_event, 15, "⏳ Генерация не завершена — работаю...")).start()

    try:
        # Текст
        try:
            bot.send_message(chat_id, "🌀 Генерирую рекламный текст...")
            text = generate_text(prompt + "\nДобавь в конце: 'Оставьте заявку в боте @ZuhFacadeBot'.")
            bot.send_message(chat_id, "✅ Текст готов:\n\n" + text)
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка генерации текста: " + str(e))
            text = None

        # Баннер (1080x1080 и копия 1280x720)
        try:
            bot.send_message(chat_id, "🌀 Генерирую баннер 1080×1080...")
            image_prompt = f"{prompt}. На картинке крупная надпись 'Заказать здесь' и ссылка на @ZuhFacadeBot. Стиль: современный фасад."
            img_path = generate_image(image_prompt)
            banner_1080 = os.path.join(STATIC_DIR, f"banner_{int(time.time())}_1080.jpg")
            banner_720  = os.path.join(STATIC_DIR, f"banner_{int(time.time())}_720.jpg")
            resize_image(img_path, banner_1080, (1080,1080))
            resize_image(img_path, banner_720, (1280,720))
            with open(banner_1080, "rb") as f:
                bot.send_photo(chat_id, f)
            bot.send_message(chat_id, f"✅ Баннер готов. Вариант для Telegram/YouTube: {os.path.basename(banner_720)}")
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка генерации баннера: " + str(e))

        # Видео — ссылка (и пробуем сделать копию 720 если сможем)
        try:
            bot.send_message(chat_id, "🌀 Генерирую вертикальное видео (Reels) — это может занять время...")
            video_url = generate_video(prompt + " Вертикальное видео 10-15 секунд, Reels, призыв к действию и ссылка на @ZuhFacadeBot.")
            bot.send_message(chat_id, "✅ Видео готово (оригинал):\n" + str(video_url))
            # пробуем скачать и конвертировать (если ffmpeg/moviepy доступен)
            try:
                in_tmp = os.path.join(STATIC_DIR, f"video_in_{int(time.time())}.mp4")
                out_tmp = os.path.join(STATIC_DIR, f"video_720_{int(time.time())}.mp4")
                download_file(video_url, in_tmp)
                # пробуем resize через moviepy (если доступно)
                try:
                    from moviepy.editor import VideoFileClip
                    clip = VideoFileClip(in_tmp)
                    clip_resized = clip.resize(height=720)
                    clip_resized.write_videofile(out_tmp, codec="libx264", audio_codec="aac")
                    clip.close(); clip_resized.close()
                    bot.send_message(chat_id, "✅ Подготовлена версия видео 1280×720: " + os.path.basename(out_tmp))
                except Exception:
                    # если moviepy/ffmpeg нет — просто оставим оригинал
                    pass
            except Exception:
                pass
        except Exception as e:
            bot.send_message(chat_id, "⚠️ Ошибка генерации видео: " + str(e))

        # уведомление админу (опционально)
        try:
            if ADMIN_CHAT_ID:
                bot.send_message(ADMIN_CHAT_ID, f"Пользователь {chat_id} сделал генерацию по промту:\n{prompt}")
        except Exception:
            pass

        bot.send_message(chat_id, "🎯 Генерация завершена.")
    except Exception as outer:
        tb = traceback.format_exc()
        print("PROCESS ERROR:", tb)
        bot.send_message(chat_id, "❗ Внутренняя ошибка: " + str(outer))
    finally:
        stop_event.set()

# ---------------------------
# Обработчики команд и сообщений
# ---------------------------
@bot.message_handler(commands=['start'])
def cmd_start(m):
    bot.send_message(m.chat.id,
        "Привет! Я ZuhAssistantBot — твой креативный AI-ассистент.\n"
        "Просто напиши: 'Сделай баннер про фасадное утепление' или 'Сделай рилс про декоративную штукатурку'.\n"
        "Команды: /promo <текст>, /slideshow (пошлите фото, затем 'Готово')."
    )

@bot.message_handler(commands=['promo'])
def cmd_promo(m):
    prompt = m.text.replace("/promo", "").strip()
    if not prompt:
        bot.send_message(m.chat.id, "Напиши после /promo короткий промт.")
        return
    bot.send_message(m.chat.id, "Принял промт. Запускаю генерацию (шаги будут приходить).")
    Thread(target=process_prompt, args=(m.chat.id, prompt)).start()

# слайдшоу: multi-step
user_slideshow = {}

@bot.message_handler(commands=['slideshow'])
def cmd_slideshow(m):
    user_slideshow[m.chat.id] = []
    bot.send_message(m.chat.id, "Отправь 1–10 фото. После последней напиши 'Готово'.")

@bot.message_handler(content_types=['photo', 'text', 'voice'])
def handle_all(m):
    cid = m.chat.id
    # фото для слайдшоу
    if m.content_type == "photo":
        if cid in user_slideshow:
            user_slideshow[cid].append(m.photo[-1].file_id)
            bot.send_message(cid, f"Принято фото #{len(user_slideshow[cid])}. Отправь ещё или напиши 'Готово'.")
            return
    if m.content_type == "text":
        txt = m.text.strip()
        if txt.lower() == "готово" and cid in user_slideshow:
            files = user_slideshow.pop(cid, [])
            if not files:
                bot.send_message(cid, "Надо прислать хотя бы одно фото.")
                return
            bot.send_message(cid, "Собираю слайдшоу — это может занять время.")
            Thread(target=process_slideshow, args=(cid, files)).start()
            return
        # обычный промт — умный режим
        bot.send_message(cid, "Принял промт. Стартую генерацию...")
        Thread(target=process_prompt, args=(cid, txt)).start()
        return
    # голос (опционально) — пытаемся распознать
    if m.content_type == "voice":
        bot.send_message(cid, "Принял голос. Попытаюсь распознать...")
        try:
            file_info = bot.get_file(m.voice.file_id)
            file_bytes = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}", timeout=30).content
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ogg", dir=STATIC_DIR)
            tmp.write(file_bytes); tmp.close()
            # распознавание через speech_recognition (может требовать ffmpeg)
            try:
                import speech_recognition as sr
                r = sr.Recognizer()
                with sr.AudioFile(tmp.name) as source:
                    audio = r.record(source)
                text = r.recognize_google(audio, language="ru-RU")
            except Exception:
                text = None
            os.remove(tmp.name)
            if text:
                bot.send_message(cid, "Распознал текст: " + text + "\nЗапускаю генерацию...")
                Thread(target=process_prompt, args=(cid, text)).start()
            else:
                bot.send_message(cid, "Не удалось распознать голос. Отправь текстом, пожалуйста.")
        except Exception as e:
            bot.send_message(cid, "Ошибка обработки голоса: " + str(e))

# ---------------------------
# Сборка слайдшоу (фон)
# ---------------------------
def process_slideshow(chat_id, file_ids):
    stop_event = Event()
    Thread(target=send_periodic_status, args=(chat_id, stop_event, 15, "⏳ Сборка слайдшоу...")).start()
    try:
        tmp_files = []
        for fid in file_ids:
            info = bot.get_file(fid)
            data = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{info.file_path}", timeout=30).content
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg", dir=STATIC_DIR)
            tmp.write(data); tmp.close()
            tmp_files.append(tmp.name)
        # пробуем локальную сборку через moviepy
        try:
            from moviepy.editor import ImageClip, concatenate_videoclips
            clips = [ImageClip(p).set_duration(2).resize(width=720) for p in tmp_files]
            video = concatenate_videoclips(clips, method="compose")
            out_path = os.path.join(STATIC_DIR, f"slideshow_{int(time.time())}.mp4")
            video.write_videofile(out_path, fps=24, codec="libx264", audio=False)
            bot.send_video(chat_id, open(out_path, "rb"))
            try: os.remove(out_path)
            except: pass
        except Exception:
            # fallback — описываем и просим Video API собрать
            try:
                bot.send_message(chat_id, "Локальная сборка не удалась. Пытаюсь через Video API...")
                video_link = generate_video("Сделай слайдшоу из присланных фотографий, 2 секунды на слайд, добавь призыв и ссылку на @ZuhFacadeBot.")
                bot.send_message(chat_id, "Ссылка на слайдшоу: " + video_link)
            except Exception as e:
                bot.send_message(chat_id, "Ошибка создания слайдшоу: " + str(e))
        # очистка
        for p in tmp_files:
            try: os.remove(p)
            except: pass
    except Exception as outer:
        bot.send_message(chat_id, "Ошибка в слайдшоу: " + str(outer))
    finally:
        stop_event.set()

# ---------------------------
# Вебхук (Render)
# ---------------------------
@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    try:
        json_str = request.stream.read().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        print("Webhook error:", e)
    return "OK", 200

@app.route('/')
def index():
    return "ZuhAssistantBot running."

# ---------------------------
# Keep-alive ping (опционально)
# ---------------------------
def keep_alive_worker(url):
    while True:
        try:
            requests.get(url, timeout=10)
        except Exception:
            pass
        time.sleep(9 * 60)

if KEEP_ALIVE_URL:
    t = Thread(target=keep_alive_worker, args=(KEEP_ALIVE_URL,))
    t.daemon = True
    t.start()

# ---------------------------
# Запуск
# ---------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print("Запуск ZuhAssistantBot на порту", port)
    app.run(host="0.0.0.0", port=port)




















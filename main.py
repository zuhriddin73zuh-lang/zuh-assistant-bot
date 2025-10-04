import os
import uuid
import requests
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# =============================
# Настройки и ключи
# =============================
BOT_TOKEN = "ТВОЙ_BOT_TOKEN_ЗДЕСЬ_БЕЗ_ПРОБЕЛОВ"
CHAT_API_KEY = "ТВОЙ_CHAT_API_KEY_ЗДЕСЬ_БЕЗ_ПРОБЕЛОВ"
IMAGE_API_KEY = "ТВОЙ_IMAGE_API_KEY_ЗДЕСЬ_БЕЗ_ПРОБЕЛОВ"
VIDEO_API_KEY = "ТВОЙ_VIDEO_API_KEY_ЗДЕСЬ_БЕЗ_ПРОБЕЛОВ"

# Entertainment сервис
SERVICE_URL = "https://zuh-assistant-bot.onrender.com"

# Временные папки
os.makedirs("banners", exist_ok=True)
os.makedirs("videos", exist_ok=True)

# =============================
# Команды бота
# =============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пришли промт для рекламы фасадных работ, и я сгенерирую текст, баннер и видео."
    )

# =============================
# Генерация контента
# =============================
async def generate_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = update.message.text.strip()  # убираем пробелы вокруг

    # -----------------------------
    # 1️⃣ Генерация текста
    # -----------------------------
    try:
        r_text = requests.post(
            f"{SERVICE_URL}/generate_text",
            headers={"Authorization": f"Bearer {CHAT_API_KEY}"},
            json={"prompt": prompt}
        )
        r_text.raise_for_status()
        text_post = r_text.json().get("text", "")
    except Exception as e:
        text_post = f"Ошибка генерации текста: {str(e)}"

    await update.message.reply_text(f"Текст готов:\n\n{text_post}")

    # -----------------------------
    # 2️⃣ Генерация баннера
    # -----------------------------
    banner_file = f"banners/{uuid.uuid4()}.png"
    try:
        r_image = requests.post(
            f"{SERVICE_URL}/generate_image",
            headers={"Authorization": f"Bearer {IMAGE_API_KEY}"},
            json={"prompt": f"{prompt} + ссылка @ZuhFacadeBot", "design": "best"}
        )
        r_image.raise_for_status()
        with open(banner_file, "wb") as f:
            f.write(r_image.content)
        with open(banner_file, "rb") as f:
            await update.message.reply_photo(photo=InputFile(f), caption="Баннер готов")
    except Exception as e:
        await update.message.reply_text(f"Ошибка генерации баннера: {str(e)}")

    # -----------------------------
    # 3️⃣ Генерация видео
    # -----------------------------
    try:
        r_video = requests.post(
            f"{SERVICE_URL}/generate_video",
            headers={"Authorization": f"Bearer {VIDEO_API_KEY}"},
            json={"prompt": f"{prompt} + ссылка @ZuhFacadeBot", "duration": 15}
        )
        r_video.raise_for_status()
        video_data = r_video.json()
        video_url = video_data.get("video_url")
        await update.message.reply_text(f"Видео готово, скачивайте по ссылке:\n{video_url}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка генерации видео: {str(e)}")

# =============================
# Запуск бота
# =============================
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN.strip()).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_content))
    print("Бот запущен и готов принимать промты...")
    app.run_polling()
















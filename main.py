import os
import uuid
import requests
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# =============================
# Настройки и ключи
# =============================
BOT_TOKEN = "YOUR_BOT_TOKEN"
CHAT_API_KEY = "YOUR_CHAT_API_KEY"
IMAGE_API_KEY = "YOUR_IMAGE_API_KEY"
VIDEO_API_KEY = "YOUR_VIDEO_API_KEY"

# Временные папки для баннеров и видео
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
    prompt = update.message.text

    # -----------------------------
    # 1️⃣ Генерация текста через Chat API
    # -----------------------------
    chat_response = requests.post(
        "https://api.render.com/v1/chat",
        headers={"Authorization": f"Bearer {CHAT_API_KEY}"},
        json={"prompt": f"Сделай рекламный текст поста для Facebook/Instagram: {prompt}"}
    )
    if chat_response.status_code == 200:
        text_post = chat_response.json().get("text", "")
    else:
        text_post = "Ошибка генерации текста."
    await update.message.reply_text(f"Текст готов:\n\n{text_post}")

    # -----------------------------
    # 2️⃣ Генерация баннера через Image API
    # -----------------------------
    banner_file = f"banners/{uuid.uuid4()}.png"
    image_response = requests.post(
        "https://api.render.com/v1/image",
        headers={"Authorization": f"Bearer {IMAGE_API_KEY}"},
        json={"prompt": f"Рекламный баннер фасадные работы: {prompt}. Добавь ссылку @ZuhFacadeBot", "size": "1080x1080"}
    )
    if image_response.status_code == 200:
        with open(banner_file, "wb") as f:
            f.write(image_response.content)
        with open(banner_file, "rb") as f:
            await update.message.reply_photo(photo=InputFile(f), caption="Баннер готов")
    else:
        await update.message.reply_text("Ошибка генерации баннера.")

    # -----------------------------
    # 3️⃣ Генерация видео через Video API
    # -----------------------------
    video_response = requests.post(
        "https://api.render.com/v1/video",
        headers={"Authorization": f"Bearer {VIDEO_API_KEY}"},
        json={"prompt": f"Создай короткий рекламный ролик 10-15 секунд по промту: {prompt} с ссылкой @ZuhFacadeBot"}
    )
    if video_response.status_code == 200:
        video_id = video_response.json().get("video_id")
        video_url = f"https://api.render.com/v1/video/download/{video_id}"
        await update.message.reply_text(f"Видео готово, скачивайте по ссылке:\n{video_url}")
    else:
        await update.message.reply_text("Ошибка генерации видео.")

# =============================
# Запуск бота
# =============================
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_content))
    print("Бот запущен и готов принимать промты...")
    app.run_polling()















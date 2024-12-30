import os
import yt_dlp
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from pydub import AudioSegment
import re

TOKEN = '7828398845:AAFhNph7fQ6HkrCcCzSMWz8G6tmgRBA4VAk'
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Очередь задач
task_queue = asyncio.Queue()

def download_audio(video_url, output_path):
    """Скачивает аудио из видео и сохраняет его в указанный файл с прогрессом."""
    def progress_hook(d):
        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes', None)
            if total:
                progress = downloaded / total * 100
                print(f"Downloading... {progress:.2f}%")

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': '%(title)s',  # Используем название видео для имени файла
        'keepvideo': False,  # Не сохраняем исходный файл
        'progress_hooks': [progress_hook],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(video_url, download=True)
        return info_dict.get('title', None)


def sanitize_filename(filename):
    """Удаляет недопустимые символы из имени файла."""
    return re.sub(r'[\\/*?:"<>|]', "", filename)


async def process_video(video_url, chat_id):
    """Обрабатывает одно видео: скачивает, режет на сегменты и отправляет пользователю."""
    try:
        # Скачиваем аудио с названием видео
        video_title = download_audio(video_url, "%(title)s.mp3")
        if not video_title:
            await bot.send_message(chat_id, "Ошибка при скачивании видео.")
            return

        sanitized_title = sanitize_filename(video_title)  # Убираем недопустимые символы из названия
        mp3_file = f"{sanitized_title}.mp3"

        # Конвертируем и сохраняем файл
        audio = AudioSegment.from_file(mp3_file)
        audio.export(mp3_file, format="mp3")

        # Разбиваем на сегменты и отправляем
        segment_length = 10 * 60 * 1000  # 10 минут в миллисекундах
        audio = AudioSegment.from_file(mp3_file)
        segments = len(audio) // segment_length + (1 if len(audio) % segment_length > 0 else 0)

        for i in range(segments):
            start = i * segment_length
            end = start + segment_length if start + segment_length < len(audio) else len(audio)
            segment = audio[start:end]

            segment_name = f"{i + 1:02}_{sanitized_title}.mp3"
            segment.export(segment_name, format="mp3")

            audio_file = FSInputFile(segment_name)
            await bot.send_audio(chat_id, audio_file)
            await bot.send_message(chat_id, f"Отправлен сегмент {i + 1} из {segments}")

            # Удаляем отправленный сегмент
            os.remove(segment_name)

        await bot.send_message(chat_id, f"Обработка видео '{sanitized_title}' завершена.")

    except Exception as e:
        await bot.send_message(chat_id, f"Ошибка: {e}")

    finally:
        # Удаляем временные файлы
        if os.path.exists(mp3_file):
            os.remove(mp3_file)

        # Дополнительно удалить файлы сегментов, если они остались
        for file_name in os.listdir():
            if file_name.startswith(f"{sanitized_title}") and file_name.endswith(".mp3"):
                os.remove(file_name)


@dp.message(Command("start"))
async def send_welcome(message: Message):
    await message.reply("Привет! Отправь мне ссылку на YouTube видео, и я обработаю его аудио для тебя.")


@dp.message(lambda message: 'youtube.com' in message.text or 'youtu.be' in message.text)
async def handle_text(message: Message):
    url = message.text.strip()
    await message.reply("Добавляю видео в очередь на обработку...")
    await task_queue.put((url, message.chat.id))


async def task_worker():
    """Фоновая задача для обработки видео из очереди."""
    while True:
        video_url, chat_id = await task_queue.get()
        await process_video(video_url, chat_id)
        task_queue.task_done()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(task_worker())
    loop.create_task(dp.start_polling(bot, skip_updates=True))
    loop.run_forever()

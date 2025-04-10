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

def sanitize_filename(filename):
    """Удаляет или заменяет недопустимые символы из имени файла."""
    return re.sub(r'[\\/*?:"<>|/]', "-", filename)  # Заменим все запрещенные символы на дефис

def download_audio(video_url, sanitized_title):
    """Скачивает аудио из видео и сохраняет его в указанный файл с прогрессом."""
    def progress_hook(d):
        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes', None)
            if total:
                progress = downloaded / total * 100
                print(f"Загрузка... {progress:.2f}%")

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': f'{sanitized_title}.%(ext)s',  # Используем очищенное название
        'keepvideo': True   
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(video_url, download=True)
        return sanitized_title, ydl.prepare_filename(info_dict)

async def process_video(video_url, chat_id, original_message_id):
    """Обрабатывает одно видео: скачивает, режет на сегменты и отправляет пользователю."""
    mp3_file = None  # Инициализируем переменную заранее
    try:
        # Получаем информацию о видео
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            original_title = info_dict.get('title', '')

        # Очищаем название видео
        sanitized_title = sanitize_filename(original_title)

        # Скачиваем аудио с новым названием файла
        _, mp3_file = download_audio(video_url, sanitized_title)

        # Конвертируем и сохраняем файл
        audio = AudioSegment.from_file(mp3_file)

        # Разбиваем на сегменты и отправляем
        segment_length = 10 * 60 * 1000  # 10 минут в миллисекундах
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

        await bot.send_message(
            chat_id,
            f"Обработка видео '{sanitized_title}' завершена.",
            reply_to_message_id=original_message_id
        )

    except Exception as e:
        await bot.send_message(chat_id, f"Ошибка: {e}")

    finally:
        # Удаление исходного файла только после завершения всех задач
        if mp3_file and os.path.exists(mp3_file):
            os.remove(mp3_file)

  # Удаление видео после завершения обработки
        if os.path.exists(f"{sanitized_title}.webm"):
            os.remove(f"{sanitized_title}.webm")
        # Дополнительно удаляем сегменты, если они остались
        for file_name in os.listdir():
            if file_name.startswith(sanitized_title) and file_name.endswith(".mp3"):
                os.remove(file_name)

@dp.message(Command("start"))
async def send_welcome(message: Message):
    await message.reply("Привет! Отправь мне ссылку на YouTube видео, и я обработаю его аудио для тебя.")

@dp.message(lambda message: 'youtube.com' in message.text or 'youtu.be' in message.text)
async def handle_text(message: Message):
    url = message.text.strip()
    await message.reply("Добавляю видео в очередь на обработку...")
    # Добавляем ссылку в очередь и сохраняем ID исходного сообщения
    await task_queue.put((url, message.chat.id, message.message_id))

async def task_worker():
    """Фоновая задача для обработки видео из очереди."""
    while True:
        video_url, chat_id, original_message_id = await task_queue.get()
        try:
            await process_video(video_url, chat_id, original_message_id)
        except Exception as e:
            await bot.send_message(chat_id, f"Ошибка обработки очереди: {e}")
        finally:
            task_queue.task_done()

async def main():
    # Запускаем обработчик задач
    asyncio.create_task(task_worker())
    # Запускаем polling
    await dp.start_polling(bot, skip_updates=True)

if __name__ == '__main__':
    asyncio.run(main())

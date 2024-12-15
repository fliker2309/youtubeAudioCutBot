import os
import yt_dlp
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
import nest_asyncio
from pydub import AudioSegment
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor

nest_asyncio.apply()

TOKEN = '7828398845:AAFhNph7fQ6HkrCcCzSMWz8G6tmgRBA4VAk'
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Очередь для хранения ссылок на видео
video_queue = deque()
processing = False
current_speed = 1.0  # Глобальная переменная для хранения текущей скорости воспроизведения

executor = ThreadPoolExecutor(max_workers=2)

def download_audio(video_url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': 'audio.%(ext)s',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(video_url, download=True)
        video_title = info_dict.get('title', None)
    return video_title

def sanitize_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

def change_speed(audio, speed=1.0):
    return audio.set_frame_rate(int(audio.frame_rate * speed))

@dp.message(Command("start"))
async def send_welcome(message: Message):
    await message.reply("Привет! Отправь мне ссылку на YouTube видео и укажи желаемую скорость воспроизведения (1.0, 1.25 или 1.5), и я обработаю его аудио для тебя.")

@dp.message(Command("speed1"))
async def set_speed_1(message: Message):
    global current_speed
    current_speed = 1.0
    await message.reply(f"Скорость аудио по умолчанию равна {current_speed}x. Теперь отправьте ссылку на YouTube видео.")

@dp.message(Command("speed1_25"))
async def set_speed_1_25(message: Message):
    global current_speed
    current_speed = 1.25
    await message.reply(f"Скорость аудио по умолчанию равна {current_speed}x. Теперь отправьте ссылку на YouTube видео.")

@dp.message(Command("speed1_5"))
async def set_speed_1_5(message: Message):
    global current_speed
    current_speed = 1.5
    await message.reply(f"Скорость аудио по умолчанию равна {current_speed}x. Теперь отправьте ссылку на YouTube видео.")

@dp.message(lambda message: 'youtube.com' in message.text or 'youtu.be' in message.text)
async def handle_text(message: Message):
    global current_speed
    url = message.text.strip()
    video_queue.append((message.chat.id, url, current_speed))
    await message.reply("Видео добавлено в очередь. Начинаю обработку...")

    if not processing:
        await process_queue()

async def process_queue():
    global processing
    processing = True

    while video_queue:
        chat_id, url, speed = video_queue.popleft()
        try:
            await bot.send_message(chat_id, f"Начинаю загрузку аудио для видео: {url}")
            video_title = await asyncio.get_event_loop().run_in_executor(executor, download_audio, url)
            sanitized_title = sanitize_filename(video_title)
            await bot.send_message(chat_id, f"Аудио загружено. Начинаю обработку видео '{sanitized_title}' со скоростью {speed}x...")

            audio = AudioSegment.from_file('audio.mp3')
            os.remove('audio.mp3')  # Удаляем временный файл

            audio = change_speed(audio, speed)

            segment_length = 10 * 60 * 1000  # 10 минут в миллисекундах
            segments = len(audio) // segment_length + (1 if len(audio) % segment_length > 0 else 0)

            for i in range(segments):
                start = i * segment_length
                end = start + segment_length if start + segment_length < len(audio) else len(audio)
                
                segment = audio[start:end]
                segment_name = f"{i+1}_{sanitized_title}_{speed}x.mp3"
                segment.export(segment_name, format="mp3")
                
                audio_file = FSInputFile(segment_name)
                await bot.send_audio(chat_id, audio_file)
                
                os.remove(segment_name)  # Удаляем временные файлы

            await bot.send_message(chat_id, "Все сегменты успешно выгружены!")
        except Exception as e:
            await bot.send_message(chat_id, f"Произошла ошибка: {e}")

    processing = False

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(dp.start_polling(bot, skip_updates=True))
    loop.run_forever()

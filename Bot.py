import os
import yt_dlp
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
import nest_asyncio
from pydub import AudioSegment
import re

nest_asyncio.apply()

TOKEN = 'ваш_токен_бота'
bot = Bot(token=TOKEN)
dp = Dispatcher()

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

@dp.message(Command("start"))
async def send_welcome(message: Message):
    await message.reply("Привет! Отправь мне ссылку на YouTube видео, и я обработаю его аудио для тебя.")

@dp.message(lambda message: 'youtube.com' in message.text or 'youtu.be' in message.text)
async def handle_text(message: Message):
    url = message.text.strip()
    try:
        await message.reply("Начинаю загрузку аудио...")
        video_title = download_audio(url)
        sanitized_title = sanitize_filename(video_title)
        await message.reply(f"Аудио загружено. Начинаю обработку видео '{sanitized_title}'...")

        audio = AudioSegment.from_file('audio.mp3')
        os.remove('audio.mp3')  # Удаляем временный файл

        segment_length = 10 * 60 * 1000  # 10 минут в миллисекундах
        segments = len(audio) // segment_length + (1 if len(audio) % segment_length > 0 else 0)

        for i in range(segments):
            start = i * segment_length
            end = start + segment_length if start + segment_length < len(audio) else len(audio)
            
            segment = audio[start:end]
            segment_name = f"{sanitized_title}_{i + 1}.mp3"
            segment.export(segment_name, format="mp3")
            
            audio_file = FSInputFile(segment_name)
            await bot.send_audio(message.chat.id, audio_file)
            
            os.remove(segment_name)  # Удаляем временные файлы

        await message.reply("Все сегменты успешно выгружены!")

    except Exception as e:
        await message.reply(f"Произошла ошибка: {e}")

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(dp.start_polling(bot, skip_updates=True))
    loop.run_forever()

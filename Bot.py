import os
import re
import asyncio
import math
import subprocess
from pathlib import Path
from collections import deque
import yaml

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile
import yt_dlp

# --- Конфиг ---
config_path = Path(__file__).parent / "config.yaml"
with open(config_path, encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

TOKEN = cfg["telegram_token"]
SEGMENT_MS = cfg.get("segment_length_ms", 10 * 60 * 1000)
SEGMENT_S = SEGMENT_MS // 1000
SPEED_OPTIONS = cfg.get("speed_options", [1.0, 1.25, 1.5, 1.75, 2.0])

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Очередь задач и отложенные ссылки (url, message_id)
task_queue: asyncio.Queue[tuple[str, int, int, float]] = asyncio.Queue()
pending_videos: dict[int, deque[tuple[str, int]]] = {}

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:<>|"]', "-", name)

def speed_keyboard() -> types.InlineKeyboardMarkup:
    rows = []
    row = []
    for i, speed in enumerate(SPEED_OPTIONS, start=1):
        btn = types.InlineKeyboardButton(text=f"{speed}×",
                                         callback_data=f"speed:{speed}")
        row.append(btn)
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Пришли ссылку на YouTube, выбери скорость — "
        "я поставлю задачу в очередь."
    )

@dp.message(lambda m: 'youtube.com' in m.text or 'youtu.be' in m.text)
async def handle_link(message: types.Message):
    dq = pending_videos.setdefault(message.chat.id, deque())
    dq.append((message.text.strip(), message.message_id))
    await message.answer(
        f"Ссылка #{len(dq)} в очереди. Выбери скорость:",
        reply_markup=speed_keyboard()
    )

@dp.callback_query(lambda c: c.data.startswith("speed:"))
async def handle_speed(cb: types.CallbackQuery):
    chat_id = cb.message.chat.id
    dq = pending_videos.get(chat_id)
    if not dq:
        await cb.answer("Сначала отправь ссылку.", show_alert=True)
        return

    speed = float(cb.data.split(":", 1)[1])
    url, orig_msg_id = dq.popleft()
    await task_queue.put((url, chat_id, orig_msg_id, speed))
    await cb.message.answer(
        f"Задача на скорость {speed}× принята. "
        f"До тебя в очереди {task_queue.qsize()-1} видео."
    )
    await cb.answer()

async def task_worker():
    while True:
        url, chat_id, orig_msg_id, speed = await task_queue.get()

        # 1) Форвардим оригинал
        await bot.forward_message(chat_id, chat_id, orig_msg_id)
        # 2) Сообщаем, что начинаем
        await bot.send_message(chat_id, "Начинаю обработку...")

        try:
            await process_video(url, chat_id, orig_msg_id, speed)
        except Exception as e:
            await bot.send_message(chat_id, f"Ошибка: {e}")
        finally:
            task_queue.task_done()


async def process_video(
    video_url: str,
    chat_id: int,
    orig_msg_id: int,
    speed: float
):
    loop = asyncio.get_event_loop()

    # 1) Скачиваем аудио
    def blocking_download():
        opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'input.%(ext)s',
            'quiet': True
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            title = info.get('title', 'audio')
            safe_title = sanitize_filename(title)
            path = ydl.prepare_filename(info)
            return path, safe_title

    input_file, title_safe = await loop.run_in_executor(None, blocking_download)

    # 2) Узнаём длительность
    def ffprobe_duration(path: str) -> float:
        cmd = [
            'ffprobe','-v','error',
            '-show_entries','format=duration',
            '-of','default=noprint_wrappers=1:nokey=1', path
        ]
        out = subprocess.check_output(cmd)
        return float(out)

    duration = await loop.run_in_executor(None, ffprobe_duration, input_file)

    # 3) Готовим параметры для сегментации
    # Входной сегмент = SEGMENT_S * speed, чтобы выход после atempo был SEGMENT_S
    segment_in_s = SEGMENT_S * speed
    total_segments = math.ceil(duration / segment_in_s)

    # atempo-фильтр
    def atempo_filter(s: float) -> str:
        parts = []
        rem = s
        while rem > 2.0:
            parts.append('atempo=2.0')
            rem /= 2.0
        parts.append(f'atempo={rem:.2f}')
        return ','.join(parts)

    filter_str = atempo_filter(speed)

    # 4) Разбиваем и отправляем
    for i in range(total_segments):
        start = i * segment_in_s
        out_name = f"{i+1:02}__{title_safe}.mp3"
        cmd = [
            'ffmpeg','-y',
            '-ss', str(start),
            '-t', str(segment_in_s),
            '-i', input_file,
            '-filter:a', filter_str,
            out_name
        ]
        await loop.run_in_executor(None, lambda: subprocess.run(cmd, check=True))
        await bot.send_audio(chat_id, FSInputFile(out_name))
        await bot.send_message(chat_id, f"Сегмент {i+1}/{total_segments} отправлен")
        os.remove(out_name)

    os.remove(input_file)

    # 5) Пересылаем оригинал и сообщаем «Готово»
    await bot.forward_message(chat_id, chat_id, orig_msg_id)
    await bot.send_message(chat_id, "Готово!")

async def main():
    asyncio.create_task(task_worker())
    await dp.start_polling(bot, skip_updates=True)

if __name__ == '__main__':
    asyncio.run(main())

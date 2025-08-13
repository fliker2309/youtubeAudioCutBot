import os
import re
import asyncio
import math
import subprocess
from pathlib import Path
from collections import deque
import yaml
import logging
from concurrent.futures import ThreadPoolExecutor
import time
import threading

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile
import yt_dlp

# --- Настройка логирования ---
logging.basicConfig(
    level=logging.INFO,
    filename='bot.log',
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Конфиг ---
config_path = Path(__file__).parent / "config.yaml"
if not config_path.exists():
    raise FileNotFoundError("Файл config.yaml не найден")
with open(config_path, encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

TOKEN = cfg["telegram_token"]
SEGMENT_MS = cfg.get("segment_length_ms", 10 * 60 * 1000)
SEGMENT_S = SEGMENT_MS // 1000
SPEED_OPTIONS = cfg.get("speed_options", [1.0, 1.25, 1.5, 1.75, 2.0])

bot = Bot(token=TOKEN)
dp = Dispatcher()
executor = ThreadPoolExecutor(max_workers=4)

task_queue: asyncio.Queue[tuple[str, int, int, float]] = asyncio.Queue(maxsize=10)
pending_videos: dict[int, deque[tuple[str, int, int]]] = {}
progress_data: dict[int, tuple[float, int]] = {}
message_ids: dict[int, list[int]] = {}
active_tasks_lock = threading.Lock()
active_tasks: int = 0


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:<>|\"]', "-", name)


def speed_keyboard() -> types.InlineKeyboardMarkup:
    rows, row = [], []
    for i, speed in enumerate(SPEED_OPTIONS, 1):
        row.append(types.InlineKeyboardButton(text=f"{speed}×", callback_data=f"speed:{speed}"))
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def create_progress_bar(percent: float) -> str:
    filled = int(percent // 10)
    return f"{'█' * filled}{'░' * (10 - filled)} {percent:.1f}%"


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Пришли ссылку на YouTube, выбери скорость — я поставлю задачу в очередь.")


@dp.message(lambda m: 'youtube.com' in m.text or 'youtu.be' in m.text)
async def handle_link(message: types.Message):
    dq = pending_videos.setdefault(message.chat.id, deque())
    speed_msg = await message.answer(
        f"Ссылка #{len(dq)+1} в очереди. Выбери скорость:",
        reply_markup=speed_keyboard()
    )
    dq.append((message.text.strip(), message.message_id, speed_msg.message_id))


@dp.callback_query(lambda c: c.data.startswith("speed:"))
async def handle_speed(cb: types.CallbackQuery):
    chat_id = cb.message.chat.id
    dq = pending_videos.get(chat_id)
    if not dq:
        await cb.answer("Сначала отправь ссылку.", show_alert=True)
        return

    speed = float(cb.data.split(":", 1)[1])
    url, orig_msg_id, speed_msg_id = dq.popleft()
    try:
        await task_queue.put((url, chat_id, orig_msg_id, speed))
        with active_tasks_lock:
            total_tasks = task_queue.qsize() - 1 + active_tasks
        await cb.message.answer(f"Задача на {speed}× принята. До тебя в очереди {total_tasks} видео.")
        try:
            await bot.delete_message(chat_id=chat_id, message_id=speed_msg_id)
        except Exception as e:
            logger.error(f"Ошибка удаления сообщения 'Выбери скорость': {e}")
    except asyncio.QueueFull:
        await cb.message.answer("Очередь переполнена, попробуй позже.")
    await cb.answer()


async def edit_progress_message(chat_id: int, message_id: int, text: str):
    try:
        await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.error(f"Ошибка редактирования сообщения: {e}")
        msg = await bot.send_message(chat_id=chat_id, text=text)
        progress_data[chat_id] = (progress_data[chat_id][0], msg.message_id)


def download_progress_hook(d, chat_id, loop):
    if d['status'] == 'downloading':
        percent = d.get('downloaded_bytes', 0) / d.get('total_bytes', 1) * 100
        last_percent, message_id = progress_data.get(chat_id, (-10, None))
        last_update_time = progress_data.get(f"{chat_id}_time", 0)
        now = time.time()

        if percent >= last_percent + 10 and now - last_update_time > 1:
            progress_data[chat_id] = (percent, message_id)
            progress_data[f"{chat_id}_time"] = now
            text = f"Загрузка:\n{create_progress_bar(percent)}"
            if message_id is None:
                loop.call_soon_threadsafe(lambda: asyncio.create_task(
                    bot.send_message(chat_id=chat_id, text=text)))
            else:
                loop.call_soon_threadsafe(lambda: asyncio.create_task(
                    edit_progress_message(chat_id, message_id, text)))


async def task_worker():
    global active_tasks
    while True:
        url, chat_id, orig_msg_id, speed = await task_queue.get()
        with active_tasks_lock:
            active_tasks += 1
        await bot.forward_message(chat_id=chat_id, from_chat_id=chat_id, message_id=orig_msg_id)
        start_msg = await bot.send_message(chat_id=chat_id, text="Начинаю обработку...")
        message_ids[chat_id] = [start_msg.message_id]
        try:
            await process_video(url, chat_id, orig_msg_id, speed)
        except Exception as e:
            await bot.send_message(chat_id=chat_id, text=f"Ошибка: {e}")
            logger.error(f"Ошибка: {e}")
        finally:
            task_queue.task_done()
            with active_tasks_lock:
                active_tasks -= 1
            progress_data.pop(chat_id, None)
            message_ids.pop(chat_id, None)


def process_segment(input_file: str, start: float, duration: float, filter_str: str, output_file: str):
    """Обработка одного сегмента аудио"""
    cmd = [
        'ffmpeg', '-y', '-ss', str(start), '-t', str(duration),
        '-i', input_file, '-filter:a', filter_str, output_file
    ]
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


async def process_video(video_url: str, chat_id: int, orig_msg_id: int, speed: float):
    loop = asyncio.get_event_loop()
    progress_data[chat_id] = (-10, None)

    def blocking_download():
        try:
            opts = {
                'format': 'bestaudio/best',
                'outtmpl': 'input.%(ext)s',
                'quiet': True,
                'no-playlist': True,
                'progress_hooks': [lambda d: download_progress_hook(d, chat_id, loop)]
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                title = info.get('title', 'audio')
                safe_title = sanitize_filename(title)
                path = ydl.prepare_filename(info)
                return path, safe_title
        except Exception as e:
            logger.error(f"Ошибка загрузки видео {video_url}: {e}")
            raise Exception(f"Не удалось загрузить видео: {e}")

    input_file, title_safe = await loop.run_in_executor(executor, blocking_download)

    def get_duration(path: str) -> float:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1', path]
        return float(subprocess.check_output(cmd))

    duration = await loop.run_in_executor(executor, lambda: get_duration(input_file))
    segment_in_s = SEGMENT_S * speed
    total_segments = math.ceil(duration / segment_in_s)

    def atempo_filter(s: float) -> str:
        parts = []
        while s > 2.0:
            parts.append('atempo=2.0')
            s /= 2.0
        parts.append(f'atempo={s:.2f}')
        return ','.join(parts)

    filter_str = atempo_filter(speed)

    # Обрабатываем сегменты по порядку, чтобы не было путаницы с сортировкой
    for i in range(total_segments):
        start = i * segment_in_s
        segment_num = f"{i+1:02d}"
        out_name = f"{segment_num}__{title_safe}.mp3"
        
        # Исправляем проблему с lambda - создаем отдельную функцию
        try:
            await loop.run_in_executor(
                executor, 
                process_segment, 
                input_file, 
                start, 
                segment_in_s, 
                filter_str, 
                out_name
            )
            await bot.send_audio(chat_id=chat_id, audio=FSInputFile(out_name))
            seg_msg = await bot.send_message(chat_id=chat_id, text=f"Сегмент {i+1}/{total_segments} отправлен")
            message_ids[chat_id].append(seg_msg.message_id)
        except subprocess.CalledProcessError as e:
            logger.error(f"Ошибка обработки сегмента {i+1}: {e}")
            await bot.send_message(chat_id=chat_id, text=f"Ошибка обработки сегмента {i+1}")
            continue
        finally:
            # Удаляем файл сегмента
            if os.path.exists(out_name):
                try:
                    os.remove(out_name)
                except Exception as e:
                    logger.error(f"Не удалось удалить сегмент {out_name}: {e}")

    # Удаляем исходный файл
    if os.path.exists(input_file):
        try:
            os.remove(input_file)
        except Exception as e:
            logger.error(f"Не удалось удалить файл {input_file}: {e}")

    await bot.forward_message(chat_id=chat_id, from_chat_id=chat_id, message_id=orig_msg_id)
    await bot.send_message(chat_id=chat_id, text="Готово!")

    if chat_id in progress_data:
        _, msg_id = progress_data[chat_id]
        if msg_id:
            await bot.delete_message(chat_id, msg_id)
        progress_data.pop(chat_id)

    for msg_id in message_ids.get(chat_id, []):
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception as e:
            logger.error(f"Ошибка удаления сообщения {msg_id}: {e}")


def check_dependencies():
    """Проверяет наличие необходимых системных зависимостей"""
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        subprocess.run(['ffprobe', '-version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError("ffmpeg и ffprobe должны быть установлены в системе")


async def main():
    # Проверяем зависимости
    check_dependencies()
    
    asyncio.create_task(task_worker())
    await dp.start_polling(bot, skip_updates=True)


if __name__ == '__main__':
    asyncio.run(main())

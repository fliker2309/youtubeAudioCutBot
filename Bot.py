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

# Очередь задач и отложенные ссылки (url, orig_msg_id, speed_msg_id)
task_queue: asyncio.Queue[tuple[str, int, int, float]] = asyncio.Queue(maxsize=10)
pending_videos: dict[int, deque[tuple[str, int, int]]] = {}
progress_data: dict[int, tuple[float, int]] = {}  # {chat_id: (last_percent, progress_msg_id)}
message_ids: dict[int, list[int]] = {}  # {chat_id: [start_msg_id, segment_msg_id1, ...]}
active_tasks: int = 0  # Счетчик активных задач

def sanitize_filename(name: str) -> str:
    """Удаляет недопустимые символы из имени файла для безопасного сохранения."""
    return re.sub(r'[\\/*?:<>|"]', "-", name)

def speed_keyboard() -> types.InlineKeyboardMarkup:
    """Создает inline-клавиатуру с вариантами скорости."""
    rows = []
    row = []
    for i, speed in enumerate(SPEED_OPTIONS, start=1):
        btn = types.InlineKeyboardButton(text=f"{speed}×", callback_data=f"speed:{speed}")
        row.append(btn)
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)

def create_progress_bar(percent: float) -> str:
    """Создает строку прогресс-бара на основе процента."""
    filled = int(percent // 10)
    empty = 10 - filled
    return f"{'█' * filled}{'░' * empty} {percent:.1f}%"

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start."""
    await message.answer(
        "Привет! Пришли ссылку на YouTube, выбери скорость — я поставлю задачу в очередь."
    )

@dp.message(lambda m: 'youtube.com' in m.text or 'youtu.be' in m.text)
async def handle_link(message: types.Message):
    """Обработчик YouTube-ссылок."""
    dq = pending_videos.setdefault(message.chat.id, deque())
    speed_msg = await message.answer(
        f"Ссылка #{len(dq)+1} в очереди. Выбери скорость:",
        reply_markup=speed_keyboard()
    )
    dq.append((message.text.strip(), message.message_id, speed_msg.message_id))

@dp.callback_query(lambda c: c.data.startswith("speed:"))
async def handle_speed(cb: types.CallbackQuery):
    """Обработчик выбора скорости."""
    chat_id = cb.message.chat.id
    dq = pending_videos.get(chat_id)
    if not dq:
        await cb.answer("Сначала отправь ссылку.", show_alert=True)
        return

    speed = float(cb.data.split(":", 1)[1])
    url, orig_msg_id, speed_msg_id = dq.popleft()
    try:
        await task_queue.put((url, chat_id, orig_msg_id, speed))
        # Учитываем активные задачи и очередь
        total_tasks = task_queue.qsize() - 1 + active_tasks
        await cb.message.answer(
            f"Задача на скорость {speed}× принята. До тебя в очереди {total_tasks} видео."
        )
        # Удаляем сообщение "Выбери скорость"
        try:
            await bot.delete_message(chat_id=chat_id, message_id=speed_msg_id)
        except Exception as e:
            logger.error(f"Ошибка удаления сообщения 'Выбери скорость': {e}")
    except asyncio.QueueFull:
        await cb.message.answer("Очередь переполнена, попробуй позже.")
    await cb.answer()

async def edit_progress_message(chat_id: int, message_id: int, text: str):
    """Редактирует сообщение с прогрессом, обрабатывая возможные ошибки."""
    try:
        await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.error(f"Ошибка редактирования сообщения: {e}")
        msg = await bot.send_message(chat_id=chat_id, text=text)
        progress_data[chat_id] = (progress_data[chat_id][0], msg.message_id)

def download_progress_hook(d, chat_id, loop):
    """Синхронный хук для отслеживания прогресса загрузки."""
    if d['status'] == 'downloading':
        percent = d.get('downloaded_bytes', 0) / d.get('total_bytes', 1) * 100
        logger.debug(f"Прогресс загрузки: {percent:.1f}% для chat_id {chat_id}")
        last_percent, message_id = progress_data.get(chat_id, (-10, None))
        last_update_time = progress_data.get(f"{chat_id}_time", 0)
        current_time = time.time()

        if percent >= last_percent + 10 and current_time - last_update_time > 1:
            progress_data[chat_id] = (percent, message_id)
            progress_data[f"{chat_id}_time"] = current_time
            text = f"Загрузка:\n{create_progress_bar(percent)}"
            if message_id is None:
                async def send_initial():
                    msg = await bot.send_message(chat_id=chat_id, text=text)
                    progress_data[chat_id] = (percent, msg.message_id)
                loop.call_soon_threadsafe(lambda: asyncio.create_task(send_initial()))
            else:
                logger.debug(f"Редактирование сообщения: chat_id={chat_id}, message_id={message_id}, text={text}")
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(
                        edit_progress_message(chat_id, message_id, text)
                    )
                )
    elif d['status'] == 'finished':
        logger.info(f"Загрузка завершена для chat_id {chat_id}")

async def task_worker():
    """Обработчик задач из очереди."""
    global active_tasks
    while True:
        url, chat_id, orig_msg_id, speed = await task_queue.get()
        active_tasks += 1  # Увеличиваем счетчик активных задач
        logger.info(f"Начата обработка видео {url} со скоростью {speed}")
        await bot.forward_message(chat_id=chat_id, from_chat_id=chat_id, message_id=orig_msg_id)
        start_msg = await bot.send_message(chat_id=chat_id, text="Начинаю обработку...")
        # Сохраняем ID сообщения "Начинаю обработку"
        message_ids[chat_id] = [start_msg.message_id]
        try:
            await process_video(url, chat_id, orig_msg_id, speed)
        except yt_dlp.DownloadError as e:
            await bot.send_message(chat_id=chat_id, text="Ошибка: не удалось загрузить видео с YouTube.")
            logger.error(f"Ошибка загрузки: {url}, {e}")
        except subprocess.CalledProcessError as e:
            await bot.send_message(chat_id=chat_id, text="Ошибка: не удалось обработать аудио.")
            logger.error(f"Ошибка обработки: {url}, {e}")
        except Exception as e:
            await bot.send_message(chat_id=chat_id, text=f"Неизвестная ошибка: {e}")
            logger.error(f"Неизвестная ошибка: {e}")
        finally:
            task_queue.task_done()
            active_tasks -= 1  # Уменьшаем счетчик активных задач
            progress_data.pop(chat_id, None)
            message_ids.pop(chat_id, None)

async def process_video(video_url: str, chat_id: int, orig_msg_id: int, speed: float):
    """Обрабатывает видео: загружает, изменяет скорость и отправляет сегменты."""
    loop = asyncio.get_event_loop()
    progress_data[chat_id] = (-10, None)

    def blocking_download():
        opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'input.%(ext)s',
            'quiet': False,
            'no-playlist': True,
            'progress_hooks': [lambda d: download_progress_hook(d, chat_id, loop)],
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            title = info.get('title', 'audio')
            safe_title = sanitize_filename(title)
            path = ydl.prepare_filename(info)
            return path, safe_title

    input_file, title_safe = await loop.run_in_executor(executor, blocking_download)

    try:
        def ffprobe_duration(path: str) -> float:
            cmd = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', path
            ]
            out = subprocess.check_output(cmd)
            return float(out)

        duration = await loop.run_in_executor(executor, ffprobe_duration, input_file)
        segment_in_s = SEGMENT_S * speed
        total_segments = math.ceil(duration / segment_in_s)

        def atempo_filter(s: float) -> str:
            parts = []
            rem = s
            while rem > 2.0:
                parts.append('atempo=2.0')
                rem /= 2.0
            parts.append(f'atempo={rem:.2f}')
            return ','.join(parts)

        filter_str = atempo_filter(speed)

        for i in range(total_segments):
            start = i * segment_in_s
            out_name = f"{i+1:02}__{title_safe}.mp3"
            cmd = [
                'ffmpeg', '-y',
                '-ss', str(start),
                '-t', str(segment_in_s),
                '-i', input_file,
                '-filter:a', filter_str,
                out_name
            ]
            segment_percent = (i / total_segments) * 100
            text = f"Обработка сегментов:\n{create_progress_bar(segment_percent)}"
            if chat_id in progress_data:
                _, message_id = progress_data[chat_id]
                if message_id:
                    await edit_progress_message(chat_id, message_id, text)
                else:
                    msg = await bot.send_message(chat_id=chat_id, text=text)
                    progress_data[chat_id] = (segment_percent, msg.message_id)
            else:
                msg = await bot.send_message(chat_id=chat_id, text=text)
                progress_data[chat_id] = (segment_percent, msg.message_id)

            await loop.run_in_executor(executor, lambda: subprocess.run(cmd, check=True))
            await bot.send_audio(chat_id=chat_id, audio=FSInputFile(out_name))
            segment_msg = await bot.send_message(chat_id=chat_id, text=f"Сегмент {i+1}/{total_segments} отправлен")
            # Сохраняем ID сообщения "Сегмент X/Y отправлен"
            message_ids[chat_id].append(segment_msg.message_id)
            if os.path.exists(out_name):
                try:
                    os.remove(out_name)
                except OSError as e:
                    logger.error(f"Ошибка удаления файла {out_name}: {e}")

        await bot.forward_message(chat_id=chat_id, from_chat_id=chat_id, message_id=orig_msg_id)
        await bot.send_message(chat_id=chat_id, text="Готово!")
        logger.info(f"Обработка {video_url} завершена успешно")

        # Удаляем сообщение с прогрессом
        if chat_id in progress_data:
            _, progress_msg_id = progress_data[chat_id]
            if progress_msg_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=progress_msg_id)
                except Exception as e:
                    logger.error(f"Ошибка удаления сообщения с прогрессом: {e}")
                progress_data.pop(chat_id, None)

        # Удаляем сообщения "Начинаю обработку" и "Сегмент X/Y отправлен"
        if chat_id in message_ids:
            for msg_id in message_ids[chat_id]:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception as e:
                    logger.error(f"Ошибка удаления сообщения {msg_id}: {e}")
            message_ids.pop(chat_id, None)

    finally:
        if os.path.exists(input_file):
            try:
                os.remove(input_file)
            except OSError as e:
                logger.error(f"Ошибка удаления файла {input_file}: {e}")

async def main():
    """Запускает бота и обработчик задач."""
    asyncio.create_task(task_worker())
    await dp.start_polling(bot, skip_updates=True)

if __name__ == '__main__':
    asyncio.run(main())
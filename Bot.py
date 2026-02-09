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
import uuid
import glob

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile
import yt_dlp


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

TOKEN = cfg.get("telegram_token")
SEGMENT_MS = cfg.get("segment_length_ms", 10 * 60 * 1000)
SEGMENT_S = SEGMENT_MS // 1000
SPEED_OPTIONS = cfg.get("speed_options", [1.0, 1.25, 1.5, 1.75, 2.0])

bot = Bot(token=TOKEN)
dp = Dispatcher()
executor = ThreadPoolExecutor(max_workers=4)

# --- Очереди и состояния ---
task_queue: asyncio.Queue[tuple[str, int, int, float]] = asyncio.Queue(maxsize=10)
pending_videos: dict[int, deque[tuple[str, int, int]]] = {}
active_tasks_lock = threading.Lock()
active_tasks: int = 0

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:<>|\"]', "-", name)

def cleanup_temp_files():
    """Очищает все временные файлы"""
    temp_patterns = [
        'input_*.mp4', 'input_*.webm', 'input_*.m4a', 'input_*.mp3',
        'input_*.part*', 'input_*.frag*', 'input_*.ytdl', 'input_*.info.json',
        'input_*.temp*', 'input_*.tmp*',
    ]
    cleaned_count = 0
    for pattern in temp_patterns:
        for file_path in glob.glob(pattern):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    cleaned_count += 1
                    logger.info(f"Удален временный файл: {file_path}")
            except Exception as e:
                logger.warning(f"Не удалось удалить временный файл {file_path}: {e}")
    if cleaned_count > 0:
        logger.info(f"Очищено {cleaned_count} временных файлов")

def cleanup_download_artifacts(downloaded_path: str):
    """Удаляет скачанный файл и связанные артефакты yt-dlp (.part/.ytdl/.json)."""
    try:
        p = Path(downloaded_path)
        base = str(p.with_suffix(''))  # без расширения
        patterns = [
            str(p),
            base + ".*",
            str(p) + ".part*",
            base + ".*.part*",
            base + ".ytdl",
            base + ".info.json",
        ]
        for pat in patterns:
            for fp in glob.glob(pat):
                try:
                    if os.path.exists(fp):
                        os.remove(fp)
                except Exception as e:
                    logger.warning(f"Не удалось удалить артефакт {fp}: {e}")
    except Exception as e:
        logger.warning(f"Ошибка cleanup_download_artifacts: {e}")

def clear_logs():
    """Очищает файл логов после успешной обработки (без повторной записи в него)."""
    try:
        for h in list(logger.handlers):
            try:
                h.flush()
            except Exception:
                pass
        root = logging.getLogger()
        for h in list(root.handlers):
            try:
                h.flush()
            except Exception:
                pass
        with open('bot.log', 'w', encoding='utf-8'):
            pass
    except Exception as e:
        logger.error(f"Ошибка очистки логов: {e}")

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

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    await message.answer("Привет! Пришли ссылку на YouTube, выбери скорость — я поставлю задачу в очередь.")

@dp.message(lambda m: 'youtube.com' in m.text or 'youtu.be' in m.text)
async def handle_link(message: types.Message):
    logger.info(f"Получена ссылка от пользователя {message.from_user.id}: {message.text[:50]}...")
    dq = pending_videos.setdefault(message.chat.id, deque())
    speed_msg = await message.answer(
        f"Ссылка #{len(dq)+1} в очереди. Выбери скорость:",
        reply_markup=speed_keyboard()
    )
    dq.append((message.text.strip(), message.message_id, speed_msg.message_id))
    logger.info(f"Ссылка добавлена в очередь для чата {message.chat.id}")

@dp.callback_query(lambda c: c.data.startswith("speed:"))
async def handle_speed(cb: types.CallbackQuery):
    chat_id = cb.message.chat.id
    logger.info(f"Получен выбор скорости от пользователя {cb.from_user.id}: {cb.data}")
    dq = pending_videos.get(chat_id)
    if not dq:
        await cb.answer("Сначала отправь ссылку.", show_alert=True)
        return

    speed = float(cb.data.split(":", 1)[1])
    url, orig_msg_id, speed_msg_id = dq.popleft()
    try:
        logger.info(f"Добавляем задачу в очередь: URL={url[:50]}..., speed={speed}, chat_id={chat_id}")
        await task_queue.put((url, chat_id, orig_msg_id, speed))
        with active_tasks_lock:
            total_tasks = task_queue.qsize() - 1 + active_tasks
        logger.info(f"Задача добавлена в очередь. Всего задач: {total_tasks}")
        await cb.message.answer(f"Задача на {speed}× принята. До тебя в очереди {total_tasks} видео.")
        try:
            await bot.delete_message(chat_id=chat_id, message_id=speed_msg_id)
        except Exception as e:
            logger.error(f"Ошибка удаления сообщения 'Выбери скорость': {e}")
    except asyncio.QueueFull:
        await cb.message.answer("Очередь переполнена, попробуй позже.")
    await cb.answer()

async def task_worker():
    global active_tasks
    logger.info("Task worker запущен")
    while True:
        logger.info("Ожидание задачи из очереди...")
        url, chat_id, orig_msg_id, speed = await task_queue.get()
        logger.info(f"Получена задача: URL={url[:50]}..., chat_id={chat_id}, speed={speed}")
        
        with active_tasks_lock:
            active_tasks += 1
        logger.info(f"Активных задач: {active_tasks}")

        try:
            # Пересылаем оригинальное сообщение
            logger.info(f"Пересылаем оригинальное сообщение в чат {chat_id}")
            await bot.forward_message(chat_id=chat_id, from_chat_id=chat_id, message_id=orig_msg_id)
            
            # Обрабатываем видео
            logger.info(f"Начинаем обработку видео для чата {chat_id}")
            await process_video(url, chat_id, orig_msg_id, speed)
            logger.info(f"Обработка видео завершена для чата {chat_id}")
            
        except Exception as e:
            error_msg = f"❌ Ошибка при обработке видео:\n{str(e)}"
            await bot.send_message(chat_id=chat_id, text=error_msg)
            logger.error(f"Ошибка обработки видео {url}: {e}")
                
        finally:
            task_queue.task_done()
            with active_tasks_lock:
                active_tasks -= 1
            cleanup_temp_files()


def process_segment(input_file: str, start: float, duration: float, filter_str: str, output_file: str):
    """Обработка одного сегмента аудио"""
    cmd = [
        'ffmpeg', '-y', '-ss', str(start), '-t', str(duration),
        '-i', input_file, '-filter:a', filter_str, output_file
    ]
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
    except UnicodeDecodeError:
        # Если UTF-8 не работает, используем бинарный режим
        return subprocess.run(cmd, check=True, capture_output=True)


async def process_video(video_url: str, chat_id: int, orig_msg_id: int, speed: float):
    logger.info(f"process_video вызвана: URL={video_url[:50]}..., chat_id={chat_id}, speed={speed}")
    loop = asyncio.get_event_loop()

    def blocking_download():
        try:
            logger.info(f"Начинаем загрузку видео: {video_url[:50]}...")
            # Создаем уникальное имя файла для каждого процесса
            import uuid
            unique_id = str(uuid.uuid4())[:8]
            filename_template = f'input_{unique_id}.%(ext)s'
            logger.info(f"Создан шаблон имени файла: {filename_template}")
            
            opts = {
                'format': 'bestaudio/best',  # Возвращаем лучшее качество
                'outtmpl': filename_template,
                'quiet': True,
                'no-playlist': True,
                'noprogress': True,  # Отключаем прогресс-бар yt-dlp
                'retries': 5,  # Увеличиваем количество попыток
                'fragment_retries': 5,  # Попытки для фрагментов
                'file_access_retries': 5,  # Попытки доступа к файлу
                'sleep_interval': 1,  # Пауза между попытками
                'max_sleep_interval': 5,  # Максимальная пауза
                'ignoreerrors': False,  # Не игнорируем ошибки
                'no_warnings': False,  # Показываем предупреждения
                # YouTube: используем только клиенты БЕЗ PO Token (см. PO Token Guide / issue 12482).
                # android/ios/web требуют GVS PO Token или дают только SABR без URL → 403.
                'extractor_args': {
                    'youtube': {
                        'player_client': ['tv', 'tv_simply', 'tv_embedded', 'web_embedded', 'android_vr'],
                    }
                },
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                },
                'socket_timeout': 30,
            }

            # Если задан PO Token (см. issue 12482/PO Token Guide) — используем
            youtube_po_token = cfg.get("youtube_po_token")
            if youtube_po_token:
                try:
                    opts['extractor_args']['youtube']['po_token'] = [str(youtube_po_token)]
                except Exception:
                    pass

            # Если хотим включить форматы без PO Token (может привести к 403, но иногда помогает)
            if cfg.get("youtube_formats_missing_pot"):
                try:
                    opts['extractor_args']['youtube']['formats'] = ['missing_pot']
                except Exception:
                    pass

            # Cookies (часто критично против 403): либо cookiefile, либо cookies_from_browser
            cookiefile = cfg.get("youtube_cookiefile")
            if cookiefile:
                opts["cookiefile"] = str(cookiefile)

            cookies_from_browser = cfg.get("youtube_cookies_from_browser")
            if cookies_from_browser:
                # формат: "firefox" или "chrome", опционально можно передать как список в config
                opts["cookiesfrombrowser"] = cookies_from_browser

            with yt_dlp.YoutubeDL(opts) as ydl:
                logger.info("Создан экземпляр YoutubeDL, начинаем извлечение информации...")
                info = ydl.extract_info(video_url, download=True)
                logger.info("Информация о видео извлечена, проверяем результат...")
                if not info:
                    raise Exception("Не удалось получить информацию о видео")
                title = info.get('title', 'audio')
                safe_title = sanitize_filename(title)
                logger.info(f"Название видео: {title}")
                path = ydl.prepare_filename(info)
                logger.info(f"Путь к файлу: {path}")
                if not path or not os.path.exists(path):
                    raise Exception("Файл не был загружен")
                logger.info(f"Файл успешно загружен: {path}")
                return path, safe_title
        except Exception as e:
            logger.error(f"Ошибка загрузки видео {video_url}: {e}")
            raise Exception(f"Не удалось загрузить видео: {e}")
        except UnicodeDecodeError as e:
            logger.error(f"Ошибка кодировки при загрузке видео {video_url}: {e}")
            raise Exception(f"Ошибка кодировки при загрузке видео: {e}")

    logger.info("Запускаем загрузку в отдельном потоке...")
    input_file, title_safe = await loop.run_in_executor(executor, blocking_download)
    logger.info(f"Загрузка завершена: {input_file}")

    def get_duration(path: str) -> float:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1', path]
        try:
            return float(subprocess.check_output(cmd, encoding='utf-8', errors='ignore'))
        except UnicodeDecodeError:
            # Если UTF-8 не работает, используем бинарный режим
            result = subprocess.check_output(cmd)
            return float(result.decode('utf-8', errors='ignore').strip())

    logger.info("Получаем длительность видео...")
    duration = await loop.run_in_executor(executor, lambda: get_duration(input_file))
    logger.info(f"Длительность видео: {duration} секунд")
    segment_in_s = SEGMENT_S * speed
    total_segments = math.ceil(duration / segment_in_s)
    logger.info(f"Будет создано {total_segments} сегментов по {segment_in_s} секунд каждый")

    def atempo_filter(s: float) -> str:
        parts = []
        while s > 2.0:
            parts.append('atempo=2.0')
            s /= 2.0
        parts.append(f'atempo={s:.2f}')
        return ','.join(parts)

    filter_str = atempo_filter(speed)

    # Обрабатываем сегменты по порядку, чтобы не было путаницы с сортировкой
    logger.info("Начинаем обработку сегментов...")
    failed_segments = 0
    for i in range(total_segments):
        start = i * segment_in_s
        segment_num = f"{i+1:02d}"
        out_name = f"{segment_num}__{title_safe}.mp3"
        logger.info(f"Обрабатываем сегмент {i+1}/{total_segments}: {out_name}")
        
        # Исправляем проблему с lambda - создаем отдельную функцию
        try:
            logger.info(f"Запускаем обработку сегмента {i+1} в отдельном потоке...")
            await loop.run_in_executor(
                executor, 
                process_segment, 
                input_file, 
                start, 
                segment_in_s, 
                filter_str, 
                out_name
            )
            logger.info(f"Сегмент {i+1} обработан, отправляем в чат...")
            await bot.send_audio(chat_id=chat_id, audio=FSInputFile(out_name))
            logger.info(f"Сегмент {i+1} отправлен в чат {chat_id}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Ошибка обработки сегмента {i+1}: {e}")
            await bot.send_message(chat_id=chat_id, text=f"Ошибка обработки сегмента {i+1}")
            failed_segments += 1
            continue
        except UnicodeDecodeError as e:
            logger.error(f"Ошибка кодировки при обработке сегмента {i+1}: {e}")
            await bot.send_message(chat_id=chat_id, text=f"Ошибка кодировки при обработке сегмента {i+1}")
            failed_segments += 1
            continue
        except Exception as e:
            logger.error(f"Ошибка отправки/обработки сегмента {i+1}: {e}")
            failed_segments += 1
            raise
        finally:
            # Удаляем файл сегмента
            if os.path.exists(out_name):
                try:
                    os.remove(out_name)
                except Exception as e:
                    logger.error(f"Не удалось удалить сегмент {out_name}: {e}")

    # Всегда чистим скачанные артефакты
    cleanup_download_artifacts(input_file)
    cleanup_temp_files()

    if failed_segments == 0:
        await bot.send_message(chat_id=chat_id, text="Готово!")
        # Очищаем логи только если все сегменты успешно доставлены
        clear_logs()
    else:
        await bot.send_message(chat_id=chat_id, text=f"Готово, но с ошибками: {failed_segments} сегм.")


def check_dependencies():
    """Проверяет наличие необходимых системных зависимостей"""
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True, encoding='utf-8', errors='ignore')
        subprocess.run(['ffprobe', '-version'], capture_output=True, check=True, encoding='utf-8', errors='ignore')
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError("ffmpeg и ffprobe должны быть установлены в системе")





async def main():
    logger.info("Запуск бота...")
    # Проверяем зависимости
    logger.info("Проверяем системные зависимости...")
    check_dependencies()
    logger.info("Зависимости проверены успешно")
    
    # Очищаем старые временные файлы при запуске
    logger.info("Очищаем временные файлы...")
    cleanup_temp_files()
    
    # Создаем один воркер для последовательной обработки
    logger.info("Создаем task worker...")
    asyncio.create_task(task_worker())
    
    try:
        logger.info("Начинаем polling...")
        await dp.start_polling(bot, skip_updates=True)
    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания...")
        # Очищаем временные файлы при завершении
        cleanup_temp_files()
        logger.info("Бот остановлен")


if __name__ == '__main__':
    asyncio.run(main())

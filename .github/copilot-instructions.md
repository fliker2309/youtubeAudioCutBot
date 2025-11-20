# Copilot Instructions for YouTube Audio Cut Bot

## Project Overview
- **Purpose:** Telegram bot to download, speed up, and segment YouTube audio for easy listening on devices like smartwatches.
- **Core Tech:** Python 3.10+, [aiogram 3.x](https://docs.aiogram.dev/), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [FFmpeg](https://ffmpeg.org/), YAML config, logging to `bot.log`.
- **Entry Point:** `Bot.py` (main bot logic, async queue, Telegram handlers, download/segment logic)
- **Config:** `config.yaml` (Telegram token, segment length, speed options)
- **Other Files:**
  - `requirements.txt`: Python dependencies
  - `cookies.txt`: (optional) for YouTube cookies
  - `bot.log`: runtime logs

## Architecture & Patterns
- **Single Worker Model:** One async worker processes the download/segment queue sequentially for reliability.
- **Task Queue:** Uses `asyncio.Queue` for pending video tasks; per-user queues tracked in `pending_videos`.
- **Speed Selection:** User selects speed via inline keyboard; handled by callback query.
- **Segmenting:** Audio is split into segments (default 10 min, configurable) using FFmpeg, with speed-up via `atempo` filter.
- **Temp File Cleanup:** All temp files and logs are cleaned up after each job and on startup/shutdown.
- **Logging:** All major actions and errors are logged to `bot.log`.

## Developer Workflows
- **Run the Bot:**
  ```bash
  python Bot.py
  ```
- **Config:** Edit `config.yaml` for bot token and options before running.
- **Dependencies:** Install Python deps with `pip install -r requirements.txt`. Ensure `ffmpeg` and `ffprobe` are in your system PATH.
- **Debugging:** Check `bot.log` for runtime issues.
- **Testing:** No formal test suite; test by running the bot and interacting via Telegram.

## Project-Specific Conventions
- **Filename Sanitization:** All output files are sanitized for cross-platform compatibility.
- **Segment Naming:** Segments are named as `NN__<title>.mp3` for easy sorting.
- **Error Handling:** User-facing errors are sent as Telegram messages; all exceptions are logged.
- **No Progress Bar:** Progress bars are disabled to avoid Telegram timeouts.
- **Queue Size:** Task queue is limited to 10; user is notified if full.

## Integration Points
- **Telegram:** Uses `aiogram` for bot logic and message handling.
- **YouTube:** Downloads via `yt-dlp` (optionally with cookies).
- **Audio Processing:** Segmentation and speed-up via FFmpeg subprocess calls.

## Examples
- **Add new speed option:** Edit `speed_options` in `config.yaml`.
- **Change segment length:** Edit `segment_length_ms` in `config.yaml`.
- **Add new dependency:** Update `requirements.txt` and install with pip.

---

For more details, see `README.md` and inline comments in `Bot.py`.

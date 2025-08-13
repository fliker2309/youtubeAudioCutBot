# YouTube Audio Cut Bot 🎧🤖

A Telegram bot that downloads audio from YouTube videos, speeds it up on request, and splits it into compact segments — perfect for listening on smartwatches like the Huawei Watch GT 4. Built with `aiogram`, `yt-dlp`, and `ffmpeg`.

## 🚀 Features

- 📥 Download YouTube audio by simply sending a video link
- ⚡ Select playback speed (1.0×, 1.25×, ..., 2.0×)
- 🪓 Auto-split into segments (default 10 minutes per segment)
- 📤 Receive segmented `.mp3` files in chat
- 🧠 Asynchronous task queue with progress updates
- ✅ Smart file naming and sanitization

---

## 📦 Installation

1. **Clone the repo**:
   ```bash
   git clone https://github.com/fliker2309/youtubeAudioCutBot.git
   cd youtubeAudioCutBot
   ```

2. **Install requirements**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Install FFmpeg**  
   Make sure `ffmpeg` and `ffprobe` are available in your system `PATH`.

   ```
4. Configure the bot by editing `config.yaml`:
    ```yaml
    telegram_token: "your_bot_token_here"
    segment_length_ms: 600000  # 10 minutes in milliseconds
    speed_options: [1.0, 1.25, 1.5, 1.75, 2.0]
    ```
=======
5. **Run the bot**:
   ```bash
   python bot.py
   ```
---

## 🧠 Usage


2. **In Telegram**:
   - Send `/start` to the bot
   - Paste a YouTube URL
   - Choose speed from the inline buttons
   - Wait for `.mp3` segments to arrive

---

## ⚙️ Tech Stack

- Python 3.10+
- [aiogram 3.x](https://docs.aiogram.dev/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [FFmpeg](https://ffmpeg.org/)
- YAML for config parsing
- Logging to `bot.log`

---

## 💡 Planned Enhancements

- Download queue persistence across restarts
- Audio normalization and tagging
- Optional transcription or auto summaries
- Web-based dashboard (?)

---

## 📄 License

MIT — free to use, modify, and redistribute.

---

> Made with 💡 by [@fliker2309](https://github.com/fliker2309)

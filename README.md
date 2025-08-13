## YouTube Audio Cut Bot

YouTube Audio Cut Bot is a Telegram bot that allows users to download audio from YouTube videos and split it into 10-minute segments. These audio segments can be played on smartwatches, such as the Huawei Watch GT 4. This bot uses `aiogram`, `yt-dlp`, and `pydub` libraries to provide a seamless experience.

### How to Use the Bot

1. **Start the Bot**: Open Telegram and start a chat with your bot. Use the command `/start` to initiate the bot.

2. **Send YouTube Link**: Send a message containing the YouTube video link you want to download and split. The bot will start downloading the audio from the video.

3. **Processing**: The bot will notify you when it starts downloading the audio and when it begins processing the audio into segments.

4. **Receive Segments**: Once the processing is complete, the bot will send you the audio segments in 10-minute intervals. These segments can be played on your smartwatch or any other device.

### Example Commands

- `/start` - Initiate the bot and receive a welcome message.
- Send a YouTube link - The bot will download and process the audio.

### Installation

1. Clone the repository:
    ```sh
    git clone https://github.com/fliker2309/youtubeAudioCutBot.git
    cd youtubeAudioCutBot
    ```

2. Install the required dependencies:
    ```sh
    pip install -r requirements.txt
    ```

3. Configure the bot by editing `config.yaml`:
    ```yaml
    telegram_token: "your_bot_token_here"
    segment_length_ms: 600000  # 10 minutes in milliseconds
    speed_options: [1.0, 1.25, 1.5, 1.75, 2.0]
    ```

### Running the Bot

1. Start the bot:
    ```sh
    python bot.py
    ```

2. Open Telegram and interact with your bot using the commands mentioned above.

### License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.



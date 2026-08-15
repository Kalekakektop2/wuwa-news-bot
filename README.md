# wuwa-news-bot

Cron pings for WuWa Telegram news worker.

- `daily/` — два поста в день в канал: календарь, коды, карточки, арт, русик. Стримы не берём.
- `dtf/` — отдельный пост на DTF, когда вышла официалка в Telegram
- `pikabu-bot/` — черновики Пикабу в личку

Секреты: `WUWA_RUN_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `DTF_TOKEN`, `DTF_USER_ID`, `GEMINI_API_KEY`
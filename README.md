# wuwa-news-bot

Cron pings for WuWa Telegram news worker.

Когда воркер публикует новость в Telegram, тот же прогон сразу делает отдельный пост в DTF: другой текст и ссылка на канал https://t.me/WuwaNewss

- `dtf/` — публикация на [Wuwa News (RU)](https://dtf.ru/id3524243)
- `pikabu-bot/` — черновики Пикабу в личку, канал не трогает

Секреты: `WUWA_RUN_URL`, `DTF_TOKEN`, `DTF_USER_ID`, `GEMINI_API_KEY`
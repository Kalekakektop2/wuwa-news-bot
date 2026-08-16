from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

OFFICIAL_BASE = (
    "https://hw-media-cdn-mingchao.kurogame.com/akiwebsite/website2.0/json/G152/en"
)
OFFICIAL_PAGE = "https://wutheringwaves.kurogames.com/en/main/news/detail/{id}"
TG_CHANNEL = "https://t.me/WuwaNewss"
GITHUB_RUS = "https://github.com/Kalekakektop2/Wuwa3.5"
FOOTER = "Актуальная информация всегда у нас в Telegram: https://t.me/WuwaNewss"
REWRITE_PROMPT = """
Ты админ русскоязычного фан-канала по Wuthering Waves (вува).
Нужен готовый пост на Пикабу: заголовок, теги и живой текст.

Тон текста — как в нашем Telegram-канале:
- живой, спокойный, чуть разговорный
- будто сам только что прочитал официалку и кидаешь людям
- не пресс-служба и не машинный перевод
- без кринжа, без «брооо», без канцелярита
- можно «короче», «поставили», «завезут», «техработы»
- 0–2 эмодзи, можно без них
- в тексте поста без кучи хештегов
- не называй себя ботом
- не притворяйся официальным аккаунтом Kuro

Факты:
- ничего не выдумывай
- даты, время, UTC+8, числа наград, имена — точно
- имена резонаторов и оружия оставляй как в оригинале
- астриты можно называть астритами

Заголовок:
- по-русски, до 80 символов
- живой, не канцелярия и не английский официальный title

Теги:
- 5–8 штук через запятую
- обязательно: wuthering waves, вува, wuwa, новости, игры
- плюс версия, если есть, например «вува 3.6»

Текст:
- сразу суть, без вступления «разработчики объявили»
- 4–10 коротких строк
- длинные инструкции «как обновить клиент» не копируй
- если техработы: когда, сколько, какая компенсация
- если патч/превью: 5–8 главных пунктов
- промокод, если есть, поставь отдельно и заметно
- не пиши про Telegram и не ставь футер — его добавлю сам
- в конце одна строка: источник — и URL официалки

Верни строго:
TITLE: заголовок
TAGS: тег1, тег2, тег3
BODY:
текст поста
""".strip()

WORTHY = [
    r"version preview",
    r"update content",
    r"update maintenance",
    r"patch notes",
    r"special program",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pikabu-bot")


def _setup_console() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def html_to_text(raw: str) -> str:
    text = str(raw or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.I)
    return match.group(1).strip() if match else None


def fetch_menu(client: httpx.Client) -> list[dict]:
    url = f"{OFFICIAL_BASE}/ArticleMenu.json?t={int(time.time() * 1000)}"
    data = client.get(url, timeout=30).raise_for_status().json()
    return sorted(data, key=lambda item: item.get("createTime") or "", reverse=True)[:40]


def fetch_article(client: httpx.Client, article_id: str) -> dict:
    url = f"{OFFICIAL_BASE}/article/{article_id}.json?t={int(time.time() * 1000)}"
    return client.get(url, timeout=30).raise_for_status().json()


def parse_article(raw: dict, menu_item: dict) -> dict:
    article_id = str(raw.get("articleId") or menu_item.get("articleId") or "")
    text = html_to_text(raw.get("articleContent") or "")
    return {
        "id": article_id,
        "title": (raw.get("articleTitle") or menu_item.get("articleTitle") or "").strip(),
        "url": OFFICIAL_PAGE.format(id=article_id),
        "text": text,
        "created_at": str(menu_item.get("createTime") or raw.get("createTime") or ""),
    }


def is_worthy(article: dict) -> bool:
    title = article.get("title") or ""
    return any(re.search(pattern, title, re.I) for pattern in WORTHY)


def extract_version(article: dict) -> str:
    match = re.search(
        r"version\s+(\d+\.\d+)",
        f"{article.get('title', '')}\n{article.get('text', '')}",
        re.I,
    )
    return match.group(1) if match else ""


def default_tags(article: dict) -> str:
    tags = ["wuthering waves", "вува", "wuwa", "новости", "игры"]
    version = extract_version(article)
    if version:
        tags.append(f"вува {version}")
    title_l = (article.get("title") or "").lower()
    if "maintenance" in title_l:
        tags.append("патч")
    elif "preview" in title_l:
        tags.append("превью")
    return ", ".join(tags)


def fallback_title(article: dict) -> str:
    version = extract_version(article)
    title_l = (article["title"] or "").lower()
    if "preview" in title_l:
        return f"Wuthering Waves {version}: официальное превью".strip()
    if "maintenance" in title_l:
        return f"Wuthering Waves {version}: техработы по патчу".strip()
    if version:
        return f"Wuthering Waves {version}: что завезут в патче"
    return (article["title"] or "Wuthering Waves")[:80]


def fallback_draft(article: dict) -> dict[str, str]:
    return {
        "title": fallback_title(article),
        "tags": default_tags(article),
        "body": fallback_post(article),
    }


def parse_model_draft(raw: str, article: dict) -> dict[str, str]:
    title = first(r"^TITLE:\s*(.+)$", raw) or fallback_title(article)
    tags = first(r"^TAGS:\s*(.+)$", raw) or default_tags(article)
    body_match = re.search(r"^BODY:\s*(.*)$", raw, re.I | re.S | re.M)
    body = (body_match.group(1).strip() if body_match else raw).strip()
    body = re.sub(r"^(TITLE|TAGS|BODY):.*$", "", body, flags=re.I | re.M).strip()
    if not body:
        body = fallback_post(article)
    if article["url"] not in body:
        body = f"{body}\n\nисточник — {article['url']}"
    return {
        "title": title[:80],
        "tags": tags,
        "body": body,
    }


def fallback_post(article: dict) -> str:
    version = extract_version(article)
    title_l = (article["title"] or "").lower()
    maint = first(r"Maintenance Time:\s*([^\n]+)", article["text"])
    comp = first(r"Maintenance Compensation:\s*([^\n]+)", article["text"])
    release = first(
        r"(?:planned for release|scheduled for release|release(?: date)?)\s+on\s+([^\n.]+)",
        f"{article['title']}\n{article['text']}",
    )
    lines: list[str] = []
    if "preview" in title_l:
        lines.append(f"вышло превью {version or 'новой версии'}".strip())
    elif "maintenance" in title_l:
        lines.append(f"техработы по {version or 'патчу'}".strip())
    else:
        lines.append(f"патч {version}".strip() if version else article["title"])
    if release:
        lines.append(f"релиз: {release}")
    if maint:
        lines.append(f"окно: {maint}")
    if comp:
        lines.append(f"компенсация: {comp}")
    lines.append("")
    lines.append(f"источник — {article['url']}")
    return "\n".join(lines).strip()


def rewrite_post(article: dict) -> dict[str, str]:
    key = env("GEMINI_API_KEY")
    if not key:
        return fallback_draft(article)

    body = article["text"]
    if len(body) > 7000:
        body = body[:7000] + "\n\n[текст обрезан]"
    user = (
        f"Заголовок: {article['title']}\n"
        f"Дата публикации (оф. сайт): {article.get('created_at') or 'не указана'}\n"
        f"Ссылка: {article['url']}\n\n"
        f"Официальный текст:\n{body}"
    )
    base = env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    model = env("GEMINI_MODEL", "gemini-3.6-flash") or "gemini-3.6-flash"
    if base.endswith("/v1beta") or base.endswith("/v1"):
        url = f"{base}/models/{model}:generateContent"
    else:
        url = f"{base}/v1beta/models/{model}:generateContent"

    payload = {
        "system_instruction": {"parts": [{"text": REWRITE_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }
    try:
        with httpx.Client(timeout=60) as client:
            response = client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": key,
                },
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Gemini HTTP {response.status_code}: {response.text[:300]}")
        parts = (
            response.json()
            .get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        text = "".join(part.get("text", "") for part in parts).strip()
        text = re.sub(r"^```(?:\w+)?\n|\n```$", "", text).strip()
        if not text:
            raise RuntimeError("пустой ответ Gemini")
        return parse_model_draft(text, article)
    except Exception:
        logger.exception("рерайт не вышел, беру живой каркас")
        return fallback_draft(article)


def with_footer(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"(?:актуальная информация всегда у нас в telegram:[^\n]*)\s*$", "", cleaned, flags=re.I)
    return f"{cleaned.rstrip()}\n\n{FOOTER}"


def build_header(draft: dict[str, str]) -> str:
    return "\n".join(
        [
            "Нужно залить пост на Пикабу.",
            "",
            f"Заголовок: {draft['title']}",
            f"Теги: {draft['tags']}",
            "",
            "Текст ниже — копируй и вставляй как есть.",
        ]
    )


def build_draft(article: dict) -> tuple[str, str]:
    draft = rewrite_post(article)
    return build_header(draft), with_footer(draft["body"])


class SeenStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ids: set[str] = set()
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.ids = {str(item) for item in raw.get("ids", [])}

    def empty(self) -> bool:
        return not self.ids

    def seen(self, article_id: str) -> bool:
        return article_id in self.ids

    def mark(self, article_id: str) -> None:
        self.ids.add(article_id)
        self.save()

    def mark_many(self, article_ids: list[str]) -> None:
        self.ids.update(article_ids)
        self.save()

    def save(self) -> None:
        payload = {}
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["ids"] = sorted(self.ids)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def send_dm(token: str, admin_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": admin_id,
        "text": text[:3900],
        "disable_web_page_preview": False,
    }
    with httpx.Client(timeout=30) as client:
        data = client.post(url, json=payload).json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram sendMessage: {data}")


def poll_once(token: str, admin_id: str, store: SeenStore, *, force_latest: bool) -> None:
    with httpx.Client(headers={"user-agent": "wuwa-pikabu-bot/1.0"}) as client:
        menu = fetch_menu(client)
        items = [item for item in menu if item.get("articleId")]
        ids = [str(item["articleId"]) for item in items]

        if store.empty() and not force_latest:
            store.mark_many(ids)
            logger.info("первый запуск: запомнил %s текущих новостей, жду новые", len(ids))
            return

        candidates: list[dict] = []
        if force_latest:
            candidates = items[:8]
        else:
            candidates = [item for item in reversed(items) if not store.seen(str(item["articleId"]))]

        if not candidates:
            logger.info("нового нет")
            return

        sent = False
        for item in candidates:
            article_id = str(item["articleId"])
            raw = fetch_article(client, article_id)
            article = parse_article(raw, item)
            if not is_worthy(article):
                store.mark(article_id)
                logger.info("не для Пикабу, пропускаю %s: %s", article_id, article["title"])
                continue
            header, text = build_draft(article)
            send_dm(token, admin_id, header)
            send_dm(token, admin_id, text)
            store.mark(article_id)
            sent = True
            logger.info("написал в личку про %s: %s", article_id, article["title"])
            if force_latest:
                break

        if force_latest and not sent:
            logger.info("среди свежих нет патча/превью/техработ")


def preview_latest() -> None:
    with httpx.Client(headers={"user-agent": "wuwa-pikabu-bot/1.0"}) as client:
        for item in fetch_menu(client):
            if not item.get("articleId"):
                continue
            article = parse_article(fetch_article(client, str(item["articleId"])), item)
            if not is_worthy(article):
                continue
            header, text = build_draft(article)
            print(header)
            print()
            print(text)
            if not text.strip().endswith(FOOTER):
                raise RuntimeError("в черновике нет обязательной строки про Telegram")
            return
    print("среди свежих нет патча/превью/техработ")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pikabu draft notifier for WuWa")
    parser.add_argument("--once", action="store_true", help="один круг проверки")
    parser.add_argument("--preview", action="store_true", help="показать черновик, в Telegram не писать")
    parser.add_argument(
        "--send-latest",
        action="store_true",
        help="сразу написать черновик по последнему патчу/превью",
    )
    return parser.parse_args()


def main() -> int:
    _setup_console()
    args = parse_args()
    token = env("PIKABU_BOT_TOKEN")
    admin_id = env("PIKABU_ADMIN_ID", "855159275")
    state_path = Path(env("PIKABU_STATE") or str(ROOT / "data" / "seen.json"))
    interval = max(60, int(env("POLL_INTERVAL_SECONDS", "300")))

    if args.preview:
        preview_latest()
        return 0

    if not token:
        logger.error("нет PIKABU_BOT_TOKEN. Создай бота в @BotFather и впиши токен в .env")
        return 0 if args.once else 1
    if not admin_id:
        logger.error("нет PIKABU_ADMIN_ID")
        return 1

    store = SeenStore(state_path)
    if args.send_latest:
        poll_once(token, admin_id, store, force_latest=True)
        return 0
    if args.once:
        poll_once(token, admin_id, store, force_latest=False)
        try:
            from community import CommunityState, run_community

            run_community(token, admin_id, CommunityState(state_path))
        except Exception:
            logger.exception("ошибка мониторинга сообщества")
        return 0

    logger.info("пикabu-бот запущен, пишу только в личку %s, интервал %s сек", admin_id, interval)
    while True:
        try:
            poll_once(token, admin_id, store, force_latest=False)
            from community import CommunityState, run_community

            run_community(token, admin_id, CommunityState(state_path))
        except Exception:
            logger.exception("ошибка цикла")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

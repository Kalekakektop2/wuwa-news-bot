from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / "pikabu-bot" / ".env")

OFFICIAL_BASE = (
    "https://hw-media-cdn-mingchao.kurogame.com/akiwebsite/website2.0/json/G152/en"
)
OFFICIAL_PAGE = "https://wutheringwaves.kurogames.com/en/main/news/detail/{id}"
TG_CHANNEL = "https://t.me/WuwaNewss"
TG_LINE = "Актуальные новости всегда в Telegram: https://t.me/WuwaNewss"
DEFAULT_USER_ID = "3524243"
USER_AGENT = "wuwa-news-app/1.0 (GitHub Actions; Linux; ru; 1080x1920)"

REWRITE_PROMPT = """
Ты автор русскоязычного игрового блога на DTF про Wuthering Waves (вува).
Пишешь отдельный пост, не копию Telegram-канала: другие формулировки, чуть спокойнее.

Тон:
- живой человек, не пресс-служба
- без кринжа, без «брооо», без канцелярита
- не называй себя ботом
- не притворяйся официальным аккаунтом Kuro

Факты — жёстко:
- ничего не выдумывай
- только то, что есть в официальном тексте
- даты, время, UTC+8, компенсация, имена — точно, без перевода в МСК
- UTC+8 это серверное время Kuro, не Москва
- имена резонаторов и оружия не переводи
- если в тексте нет списка новинок патча — не добавляй персонажей, локации и фичи

Формат:
- сразу суть
- 5–10 коротких строк
- без кучи хештегов
- не пиши футер про Telegram — его добавлю сам
- в конце одна строка: источник — и URL официалки

Верни строго:
TITLE: заголовок до 80 символов
BODY:
текст поста
""".strip()


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


def fetch_article(article_id: str) -> dict:
    with httpx.Client(timeout=30, headers={"user-agent": "wuwa-dtf/1.0"}) as client:
        menu = client.get(f"{OFFICIAL_BASE}/ArticleMenu.json?t={int(time.time() * 1000)}")
        menu.raise_for_status()
        item = next(
            (x for x in menu.json() if str(x.get("articleId")) == str(article_id)),
            {},
        )
        raw = client.get(f"{OFFICIAL_BASE}/article/{article_id}.json?t={int(time.time() * 1000)}")
        raw.raise_for_status()
        data = raw.json()
    text = html_to_text(data.get("articleContent") or "")
    return {
        "id": str(article_id),
        "title": (data.get("articleTitle") or item.get("articleTitle") or "").strip(),
        "url": OFFICIAL_PAGE.format(id=article_id),
        "text": text,
        "created_at": str(item.get("createTime") or data.get("createTime") or ""),
    }


def fallback_title(article: dict) -> str:
    match = re.search(r"version\s+(\d+\.\d+)", f"{article['title']}\n{article['text']}", re.I)
    version = match.group(1) if match else ""
    title_l = article["title"].lower()
    if "preview" in title_l:
        return f"Wuthering Waves {version}: превью патча".strip()
    if "maintenance" in title_l:
        return f"Wuthering Waves {version}: техработы".strip()
    if version:
        return f"Wuthering Waves {version}: что нового"
    return article["title"][:80]


def fallback_body(article: dict) -> str:
    maint = first(r"Maintenance Time:\s*([^\n]+)", article["text"])
    comp = first(r"Maintenance Compensation:\s*([^\n]+)", article["text"])
    lines = [fallback_title(article), ""]
    if maint:
        lines.append(f"Окно техработ: {maint}")
    if comp:
        lines.append(f"Компенсация: {comp}")
    lines.extend(["", f"источник — {article['url']}"])
    return "\n".join(lines)


def rewrite(article: dict) -> dict[str, str]:
    key = env("GEMINI_API_KEY")
    if not key:
        return {"title": fallback_title(article), "body": fallback_body(article)}

    body = article["text"][:7000]
    user = (
        f"Заголовок: {article['title']}\n"
        f"Дата: {article.get('created_at') or 'не указана'}\n"
        f"Ссылка: {article['url']}\n\n"
        f"Официальный текст:\n{body}"
    )
    base = env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    model = env("GEMINI_MODEL", "gemini-3.6-flash") or "gemini-3.6-flash"
    url = (
        f"{base}/models/{model}:generateContent"
        if base.endswith("/v1beta") or base.endswith("/v1")
        else f"{base}/v1beta/models/{model}:generateContent"
    )
    payload = {
        "system_instruction": {"parts": [{"text": REWRITE_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": 0.75,
            "maxOutputTokens": 2048,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }
    try:
        with httpx.Client(timeout=60) as client:
            response = client.post(
                url,
                headers={"Content-Type": "application/json", "x-goog-api-key": key},
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Gemini HTTP {response.status_code}: {response.text[:300]}")
        parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
        raw = "".join(part.get("text", "") for part in parts).strip()
        raw = re.sub(r"^```(?:\w+)?\n|\n```$", "", raw).strip()
        title = first(r"^TITLE:\s*(.+)$", raw) or fallback_title(article)
        body_match = re.search(r"^BODY:\s*(.*)$", raw, re.I | re.S | re.M)
        text = (body_match.group(1).strip() if body_match else raw).strip()
        text = re.sub(r"^(TITLE|TAGS|BODY):.*$", "", text, flags=re.I | re.M).strip()
        if not text:
            text = fallback_body(article)
        if article["url"] not in text:
            text = f"{text}\n\nисточник — {article['url']}"
        return {"title": title[:80], "body": text}
    except Exception as exc:
        print(f"rewrite fallback: {exc}", file=sys.stderr)
        return {"title": fallback_title(article), "body": fallback_body(article)}


def with_tg(text: str) -> str:
    cleaned = re.sub(
        r"(?:актуальн[^\n]*telegram:[^\n]*)\s*$",
        "",
        text.strip(),
        flags=re.I,
    )
    if TG_CHANNEL not in cleaned and "t.me/WuwaNewss" not in cleaned:
        cleaned = f"{cleaned.rstrip()}\n\n{TG_LINE}"
    return cleaned


def to_html(text: str) -> str:
    chunks = [part.strip() for part in re.split(r"\n\s*\n", text.strip()) if part.strip()]
    if not chunks:
        chunks = [text.strip()]
    parts = []
    for chunk in chunks:
        escaped = html.escape(chunk).replace("\n", "<br>")
        escaped = re.sub(
            r"(https://t\.me/WuwaNewss)",
            r'<a href="\1">https://t.me/WuwaNewss</a>',
            escaped,
        )
        escaped = re.sub(
            r"(https://wutheringwaves\.kurogames\.com[^\s<]+)",
            r'<a href="\1">официальный сайт</a>',
            escaped,
        )
        parts.append(f"<p>{escaped}</p>")
    return "".join(parts)


def dtf_headers(token: str) -> dict[str, str]:
    return {
        "X-Device-Token": token,
        "User-agent": USER_AGENT,
        "Accept": "application/json",
    }


def publish(title: str, body: str) -> str:
    token = env("DTF_TOKEN")
    if not token:
        raise RuntimeError("нет DTF_TOKEN. Профиль DTF → Инструменты разработчика")
    user_id = int(env("DTF_USER_ID", DEFAULT_USER_ID) or DEFAULT_USER_ID)
    html_body = to_html(body)
    headers = dtf_headers(token)

    with httpx.Client(timeout=45) as client:
        simple = client.post(
            "https://api.dtf.ru/v2.31/entry/create",
            headers=headers,
            data={
                "title": title,
                "text": html_body,
                "subsite_id": str(user_id),
            },
        )
        if simple.status_code == 404:
            simple = client.post(
                "https://api.dtf.ru/v1.9/entry/create",
                headers=headers,
                data={
                    "title": title,
                    "text": html_body,
                    "subsite_id": str(user_id),
                },
            )
        if simple.is_success:
            data = simple.json()
            url = (
                (data.get("result") or {}).get("url")
                or (data.get("data") or {}).get("url")
                or ""
            )
            if url:
                return url

        entry = {
            "user_id": user_id,
            "type": 1,
            "subsite_id": user_id,
            "title": title,
            "is_published": True,
            "entry": {
                "blocks": [
                    {
                        "type": "text",
                        "cover": True,
                        "hidden": False,
                        "anchor": "",
                        "data": {"text": html_body},
                    }
                ]
            },
        }
        editor = client.post(
            "https://api.dtf.ru/v2.1/editor",
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
            data={"entry": json.dumps(entry, ensure_ascii=False)},
        )
        if not editor.is_success and not simple.is_success:
            raise RuntimeError(
                f"DTF create failed: {simple.status_code} {simple.text[:300]} | "
                f"editor {editor.status_code} {editor.text[:300]}"
            )
        data = editor.json() if editor.is_success else simple.json()

    return (
        (data.get("result") or {}).get("url")
        or (data.get("data") or {}).get("url")
        or json.dumps(data, ensure_ascii=False)[:300]
    )


def posted_id_from_worker(payload: dict) -> str | None:
    for line in payload.get("log") or []:
        match = re.search(r"^posted\s+(\d+)\b", str(line))
        if match:
            return match.group(1)
    if payload.get("posted_id"):
        return str(payload["posted_id"])
    return None


def publish_article(article_id: str, *, dry_run: bool = False) -> int:
    article = fetch_article(article_id)
    draft = rewrite(article)
    body = with_tg(draft["body"])
    print(f"TITLE: {draft['title']}")
    print(body)
    if dry_run:
        return 0
    url = publish(draft["title"], body)
    print(f"DTF: {url}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a DTF post after Telegram")
    parser.add_argument("--from-worker", help="JSON ответ Cloudflare /run")
    parser.add_argument("--article-id", help="опубликовать конкретную официальную статью")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.from_worker:
        raw = Path(args.from_worker).read_text(encoding="utf-8")
        payload = json.loads(raw)
        article_id = posted_id_from_worker(payload)
        if not article_id:
            print("в Telegram нового поста нет, DTF пропускаю")
            return 0
        if not env("DTF_TOKEN") and not args.dry_run:
            print("DTF_TOKEN нет, пост в Telegram уже ушёл, DTF пропускаю")
            return 0
        return publish_article(article_id, dry_run=args.dry_run)

    if args.article_id:
        return publish_article(args.article_id, dry_run=args.dry_run)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

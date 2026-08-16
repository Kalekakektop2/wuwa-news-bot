from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger("pikabu-bot")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()

TG_CHANNEL = "https://t.me/WuwaNewss"
DISCORD_INVITE = "https://discord.gg/RvBpRACXAE"
FOOTER = (
    f"Актуальная информация всегда у нас в Telegram: {TG_CHANNEL}\n"
    f"Discord: {DISCORD_INVITE}"
)

KEEP = re.compile(
    r"(record|showcase|collection|collect|meta|tier|hologram|tower|"
    r"whimpering|clear|cleared|roster|gallery|speedrun|first clear|"
    r"full team|all resonator|all character|c6|r6|s6|100%|completion|"
    r"union level|ul\s*\d+|whale|title|achievement|"
    r"рекорд|коллекц|мет[аы]|башн|голограмм|витрина|собрал|прошёл|прошел)",
    re.I,
)
SKIP = re.compile(
    r"(megathread|giveaway|leak|nsfw|porn|code redeem|looking for|"
    r"who should i pull|should i pull|wutheringwavesmod|selling|account)",
    re.I,
)
YES = re.compile(r"^\s*(да+|давай|выкладывай|публикуй|ок|okay|yes)\b", re.I)
NO = re.compile(r"^\s*(нет|не\s+надо|skip|дальше|no)\b", re.I)
APPROVE_SECONDS = 3600

REWRITE = """
Ты админ русскоязычного фан-канала по Wuthering Waves (вува).
Нужен короткий пост про то, что сделал игрок. Это НЕ официалка Kuro.

Тон: живой, спокойный, как человек. Без кринжа, без «брооо», без канцелярита.
Ничего не выдумывай: цифры, ники, рекорды — только если они есть во входе.
Имена героев можно оставить как есть.

Формат:
- 1–2 предложения сути
- затем «Коротко:» и 3–6 пунктов с дефисом
- в конце: источник — URL
- строка: это не официалка Kuro
- не пиши про Telegram и Discord

Верни только текст поста.
""".strip()

FEEDS = [
    "https://www.reddit.com/r/WutheringWaves/search.rss?q=record+OR+showcase+OR+collection+OR+hologram+OR+speedrun+OR+cleared+OR+tower&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/WutheringWaves/top/.rss?t=day",
    "https://www.reddit.com/r/gachagaming/search.rss?q=Wuthering+Waves+record+OR+showcase&restrict_sr=1&sort=new",
]


def html_to_text(raw: str) -> str:
    text = str(raw or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_reddit_rss(xml_text: str) -> list[dict]:
    posts: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return posts
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        link_el = entry.find("a:link", ns)
        href = (link_el.get("href") if link_el is not None else "") or ""
        author = entry.findtext("a:author/a:name", default="", namespaces=ns) or ""
        raw_html = entry.findtext("a:content", default="", namespaces=ns) or ""
        summary = html_to_text(raw_html)
        image = None
        match = re.search(
            r'src=["\'](https?://[^"\']+\.(?:jpg|jpeg|png|webp))', raw_html, re.I
        )
        if match:
            image = match.group(1)
        if title and href:
            posts.append(
                {
                    "id": href.split("?")[0],
                    "title": title,
                    "url": href.split("?")[0],
                    "author": author.replace("/u/", "").replace("u/", ""),
                    "summary": summary[:500],
                    "image": image,
                    "source": "Reddit",
                }
            )
    return posts


def fetch_posts(client: httpx.Client) -> list[dict]:
    headers = {
        "user-agent": "wuwa-community-bot/1.0 (fan monitor; +https://github.com/Kalekakektop2/wuwa-news-bot)"
    }
    posts: list[dict] = []
    seen: set[str] = set()
    for url in FEEDS:
        try:
            response = client.get(url, headers=headers, timeout=20)
            if response.status_code >= 400 or not response.text.strip():
                continue
            for post in parse_reddit_rss(response.text):
                if post["id"] in seen:
                    continue
                seen.add(post["id"])
                posts.append(post)
        except Exception as exc:
            logger.warning("лента сообщества: %s", exc)
    return posts


def is_interesting(post: dict) -> bool:
    blob = f"{post.get('title', '')}\n{post.get('summary', '')}"
    if SKIP.search(blob):
        return False
    return bool(KEEP.search(blob))


def fallback_text(post: dict) -> str:
    author = post.get("author") or "игрок"
    return (
        f"с сообщества\n\n"
        f"на {post.get('source') or 'форуме'} {author} выложил: {post['title']}\n"
        "цифры и скрины лучше смотреть в исходнике — сами ничего не придумываем.\n\n"
        f"источник — {post['url']}\n"
        "это не официалка Kuro"
    )


def rewrite(post: dict) -> str:
    key = env("GEMINI_API_KEY")
    if not key:
        return fallback_text(post)
    user = (
        f"Заголовок: {post['title']}\n"
        f"Автор: {post.get('author') or 'неизвестен'}\n"
        f"Площадка: {post.get('source') or 'форум'}\n"
        f"Ссылка: {post['url']}\n"
        f"Обрывок: {post.get('summary') or 'нет'}"
    )
    base = env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    model = env("GEMINI_MODEL", "gemini-3.6-flash") or "gemini-3.6-flash"
    url = (
        f"{base}/models/{model}:generateContent"
        if base.endswith("/v1beta") or base.endswith("/v1")
        else f"{base}/v1beta/models/{model}:generateContent"
    )
    try:
        with httpx.Client(timeout=45) as client:
            response = client.post(
                url,
                headers={"Content-Type": "application/json", "x-goog-api-key": key},
                json={
                    "system_instruction": {"parts": [{"text": REWRITE}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {
                        "temperature": 0.5,
                        "maxOutputTokens": 1024,
                        "thinkingConfig": {"thinkingLevel": "minimal"},
                    },
                },
            )
        if response.status_code >= 400:
            raise RuntimeError(response.text[:200])
        parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts).strip()
        text = re.sub(r"^```(?:\w+)?\n|\n```$", "", text).strip()
        if not text:
            raise RuntimeError("empty")
        if post["url"] not in text:
            text = f"{text}\n\nисточник — {post['url']}"
        if "официал" not in text.lower() and "kuro" not in text.lower():
            text = f"{text}\nэто не официалка Kuro"
        return text
    except Exception:
        logger.exception("рерайт сообщества не вышел")
        return fallback_text(post)


def with_footer(text: str) -> str:
    return f"{text.rstrip()}\n\n{FOOTER}"


class CommunityState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = {
            "ids": [],
            "community_ids": [],
            "update_offset": 0,
            "pending": None,
        }
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.data.update(raw)

    def save(self) -> None:
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def official_ids(self) -> set[str]:
        return {str(item) for item in self.data.get("ids") or []}

    def mark_official(self, article_id: str) -> None:
        ids = self.official_ids()
        ids.add(article_id)
        self.data["ids"] = sorted(ids)
        self.save()

    def mark_official_many(self, article_ids: list[str]) -> None:
        ids = self.official_ids()
        ids.update(article_ids)
        self.data["ids"] = sorted(ids)
        self.save()

    def community_seen(self, post_id: str) -> bool:
        return post_id in {str(item) for item in self.data.get("community_ids") or []}

    def mark_community(self, post_id: str) -> None:
        ids = {str(item) for item in self.data.get("community_ids") or []}
        ids.add(post_id)
        self.data["community_ids"] = sorted(ids)[-400:]
        self.save()

    def mark_community_many(self, post_ids: list[str]) -> None:
        ids = {str(item) for item in self.data.get("community_ids") or []}
        ids.update(post_ids)
        self.data["community_ids"] = sorted(ids)[-400:]
        self.save()

    def pending(self) -> dict | None:
        return self.data.get("pending")

    def set_pending(self, payload: dict | None) -> None:
        self.data["pending"] = payload
        self.save()


def tg_api(token: str, method: str, payload: dict | None = None, files: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    with httpx.Client(timeout=45) as client:
        if files:
            response = client.post(url, data=payload or {}, files=files)
        else:
            response = client.post(url, json=payload or {})
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data}")
    return data


def send_dm(token: str, admin_id: str, text: str) -> None:
    tg_api(
        token,
        "sendMessage",
        {
            "chat_id": admin_id,
            "text": text[:3900],
            "disable_web_page_preview": False,
        },
    )


def process_replies(token: str, admin_id: str, state: CommunityState) -> str | None:
    offset = int(state.data.get("update_offset") or 0)
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    with httpx.Client(timeout=20) as client:
        data = client.get(
            url,
            params={"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])},
        ).json()
    if not data.get("ok"):
        logger.warning("getUpdates: %s", data)
        return None
    decision = None
    for update in data.get("result") or []:
        state.data["update_offset"] = int(update["update_id"]) + 1
        message = update.get("message") or {}
        sender = str((message.get("from") or {}).get("id") or "")
        if sender != str(admin_id):
            continue
        text = (message.get("text") or "").strip()
        if YES.match(text):
            decision = "yes"
        elif NO.match(text):
            decision = "no"
    state.save()
    return decision


def pick_art(image_url: str | None = None) -> str | None:
    reddit_preview = bool(image_url and "redd.it" in image_url)
    if image_url and not reddit_preview:
        return image_url
    try:
        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from src.art import pick_fallback_art

        picked = pick_fallback_art(image_url)
        if picked:
            return picked[0]
    except Exception:
        logger.info("src.art недоступен, беру обложку с официалки")
    try:
        with httpx.Client(timeout=20, headers={"user-agent": "wuwa-community-bot/1.0"}) as client:
            menu = client.get(
                "https://hw-media-cdn-mingchao.kurogame.com/akiwebsite/website2.0/json/G152/en/ArticleMenu.json"
            ).json()
        visual = re.compile(
            r"wallpaper|version preview|profile reveal|resonator reveal|update content|anthropocene",
            re.I,
        )
        skip = re.compile(r"maintenance notice|faq|fan creation", re.I)
        covers = []
        for item in menu:
            title = str(item.get("articleTitle") or "")
            if skip.search(title) or not visual.search(title):
                continue
            cover = str(item.get("suggestCover") or "")
            if cover.startswith("http"):
                covers.append(cover)
        if covers:
            import random

            return random.choice(covers[:12])
    except Exception:
        logger.exception("обложку с официалки не взял")
    return image_url


def post_telegram(text: str, image_url: str | None = None) -> None:
    token = env("TELEGRAM_BOT_TOKEN")
    chat = env("TELEGRAM_CHANNEL_ID")
    if not token or not chat:
        raise RuntimeError("нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
    caption = text[:1000]
    image_url = pick_art(image_url)
    if image_url:
        try:
            tg_api(
                token,
                "sendPhoto",
                {"chat_id": chat, "photo": image_url, "caption": caption},
            )
            return
        except Exception:
            logger.exception("картинку по ссылке не отправил, пробую скачать")
        try:
            root = Path(__file__).resolve().parent.parent
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from src.art import prepare_photo

            photo = prepare_photo(image_url)
            if photo:
                tg_api(
                    token,
                    "sendPhoto",
                    {"chat_id": chat, "caption": caption},
                    files={"photo": ("art.jpg", photo, "image/jpeg")},
                )
                return
        except Exception:
            logger.exception("файл арта тоже не ушёл")
    tg_api(token, "sendMessage", {"chat_id": chat, "text": text[:3900]})


def post_discord(text: str, image_url: str | None = None) -> None:
    hook = env("DISCORD_WEBHOOK_URL")
    if not hook:
        logger.warning("нет DISCORD_WEBHOOK_URL, Discord пропускаю")
        return
    payload = {
        "username": "Wuwa News",
        "content": text[:1900],
        "allowed_mentions": {"parse": []},
    }
    image_url = pick_art(image_url)
    if image_url:
        payload["embeds"] = [{"image": {"url": image_url}}]
    with httpx.Client(timeout=30) as client:
        response = client.post(f"{hook}?wait=true", json=payload)
        if response.status_code >= 400 and image_url:
            payload.pop("embeds", None)
            response = client.post(f"{hook}?wait=true", json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"Discord webhook: {response.status_code} {response.text[:200]}")


def offer_post(token: str, admin_id: str, state: CommunityState, post: dict) -> None:
    body = rewrite(post)
    public = with_footer(body)
    art = pick_art(post.get("image"))
    state.mark_community(post["id"])
    state.set_pending(
        {
            "id": post["id"],
            "text": public,
            "image": art,
            "url": post["url"],
            "title": post["title"],
            "offered_at": datetime.now(timezone.utc).isoformat(),
            "kind": "community",
        }
    )
    send_dm(
        token,
        admin_id,
        "С сообщества. Выкладываем?\n\n"
        f"{post.get('source') or 'форум'}: {post['title']}\n"
        f"{post['url']}\n\n"
        "«да» — в Telegram и Discord.\n"
        "«нет» — ищу другой.\n"
        "Если не ответишь час — выложу сам.",
    )
    send_dm(token, admin_id, public)
    logger.info("предложил сообщество: %s", post["title"])


def find_next(client: httpx.Client, state: CommunityState) -> dict | None:
    posts = fetch_posts(client)
    if not state.data.get("community_ids"):
        interesting = [post for post in posts if is_interesting(post)]
        state.mark_community_many([post["id"] for post in posts])
        logger.info("первый запуск сообщества: запомнил %s постов", len(posts))
        return interesting[0] if interesting else None
    for post in posts:
        if state.community_seen(post["id"]):
            continue
        if not is_interesting(post):
            state.mark_community(post["id"])
            continue
        return post
    return None


def pending_expired(pending: dict) -> bool:
    raw = pending.get("offered_at")
    if not raw:
        return False
    try:
        offered = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if offered.tzinfo is None:
        offered = offered.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - offered >= timedelta(seconds=APPROVE_SECONDS)


def publish_pending(token: str, admin_id: str, pending: dict, note: str) -> bool:
    try:
        post_telegram(pending["text"], pending.get("image"))
        post_discord(pending["text"], pending.get("image"))
        send_dm(token, admin_id, note)
        logger.info("опубликовал: %s", pending.get("title"))
        return True
    except Exception:
        logger.exception("не смог выложить")
        send_dm(token, admin_id, "не смог выложить. напиши «да» ещё раз или «нет».")
        return False


def offer_ready_draft(
    token: str,
    admin_id: str,
    state: CommunityState,
    *,
    title: str,
    text: str,
    image: str | None = None,
    kind: str = "daily",
) -> None:
    public = text if "t.me/WuwaNewss" in text else with_footer(text)
    art = pick_art(image)
    state.set_pending(
        {
            "id": f"{kind}:{datetime.now(timezone.utc).isoformat()}",
            "text": public,
            "image": art,
            "title": title,
            "offered_at": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
        }
    )
    send_dm(
        token,
        admin_id,
        f"{title}\n\n"
        "Выкладываем?\n"
        "«да» — в Telegram и Discord.\n"
        "«нет» — пришлю другой.\n"
        "Если не ответишь час — выложу сам.",
    )
    send_dm(token, admin_id, public)
    logger.info("предложил дневной пост: %s", title)


def run_community(token: str, admin_id: str, state: CommunityState, *, offer_new: bool = False) -> None:
    decision = process_replies(token, admin_id, state)
    pending = state.pending()
    if pending and not pending.get("offered_at"):
        pending["offered_at"] = datetime.now(timezone.utc).isoformat()
        state.set_pending(pending)
    if pending and not decision and pending_expired(pending):
        if publish_pending(
            token,
            admin_id,
            pending,
            "час прошёл, ответа не было — выложил сам в Telegram и Discord.",
        ):
            state.set_pending(None)
            pending = None
        else:
            return

    if decision == "yes" and pending:
        if publish_pending(token, admin_id, pending, "выложил в Telegram и Discord."):
            state.set_pending(None)
            pending = None
        else:
            return
    elif decision == "no":
        state.set_pending(None)
        pending = None
        send_dm(token, admin_id, "ок, ищу другой.")
        offer_new = True
    elif pending:
        logger.info("ждём ответа по: %s", pending.get("title"))
        return

    if not offer_new:
        return

    with httpx.Client() as client:
        nxt = find_next(client, state)
    if not nxt:
        if decision == "no":
            send_dm(token, admin_id, "свежего интересного пока нет.")
        else:
            logger.info("интересного с форумов нет")
        return
    offer_post(token, admin_id, state, nxt)

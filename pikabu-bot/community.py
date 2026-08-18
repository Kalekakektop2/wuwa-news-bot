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
    f"Discord — {DISCORD_INVITE}"
)

KEEP = re.compile(
    r"(record|showcase|collection|collect|meta|tier|hologram|tower|"
    r"whimpering|clear|cleared|roster|gallery|speedrun|first clear|"
    r"full team|all resonator|all character|c6|r6|s6|100%|completion|"
    r"union level|ul\s*\d+|whale|title|achievement|\btoa\b|whiwa|"
    r"fan\s*art|fanart|illustration|drew|drawing|cosplay|lore|theory|"
    r"build|rotation|guide|echo set|music|cover|ost|animation|animated|"
    r"edit|clip|exploration|puzzle|hidden|boss|weekly|gacha|pull|"
    r"got (her|him|them)|won 50|lost 50|cinematic|comic|oc\b|"
    r"рекорд|коллекц|мет[аы]|башн|голограмм|витрина|собрал|прошёл|прошел|"
    r"фан.?арт|косплей|лор|сборк|гайд|музыка|анимац|клип|эхо|босс|"
    r"крутк|выбил|нашёл|нашел|исследование)",
    re.I,
)
SKIP = re.compile(
    r"(megathread|giveaway|leak|nsfw|porn|code redeem|looking for|"
    r"who should i pull|should i pull|wutheringwavesmod|selling|account|"
    r"broke up|girlfriend|boyfriend|\bgf\b|\bbf\b|drama|"
    r"daily questions|weekly questions|rate my|tier list discussion)",
    re.I,
)
YES = re.compile(r"^\s*(да+|давай|выкладывай|публикуй|ок|okay|yes)\b", re.I)
NO = re.compile(r"^\s*(нет|не\s+надо|skip|дальше|no)\b", re.I)
APPROVE_SECONDS = 3600
# после одного такого поста не спамим тем же жанром подряд
COOLDOWN_TOPICS = {"toa", "whiwa"}
TOA_WINDOW = 4  # не больше 1 ToA среди последних N community-предложек

REWRITE = """
Ты админ русскоязычного фан-канала по Wuthering Waves (вува).
Нужен короткий пост про интересное с фан-форума. Это НЕ официалка Kuro.

Темы могут быть разными: прохождение, витрина, фан-арт, косплей, сборка/ротация,
удачный крут, лор/теория, клип/анимация, находка в мире, босс, музыка — не только башня.

Тон: живой, спокойный, как человек. Без кринжа, без «брооо», без канцелярита.
Ничего не выдумывай: цифры, ники, факты — только если они есть во входе.
Имена героев можно оставить как есть.

Если во входе нет одновременно: кто (ник/автор) и что интересного — верни только слово SKIP.

Формат:
- в первом абзаце обязательно автор и суть
- затем «Коротко:» и 3–6 пунктов с дефисом
- в конце один раз: источник — URL
- строка: это не официалка Kuro
- не пиши про Telegram и Discord

Верни только текст поста.
""".strip()

FEEDS = [
    "https://www.reddit.com/r/WutheringWaves/new/.rss",
    "https://www.reddit.com/r/WutheringWaves/top/.rss?t=week",
    "https://www.reddit.com/r/WutheringWaves/search.rss?q=fanart+OR+cosplay+OR+showcase+OR+lore+OR+build&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/WutheringWaves/search.rss?q=hologram+OR+collection+OR+music+OR+animation+OR+guide&restrict_sr=1&sort=new",
    "https://www.reddit.com/r/WutheringWaves/search.rss?q=record+OR+cleared+OR+toa+OR+whiwa&restrict_sr=1&sort=new",
]
JSON_FEEDS = [
    "https://old.reddit.com/r/WutheringWaves/new.json?limit=40",
    "https://old.reddit.com/r/WutheringWaves/top.json?t=week&limit=25",
    "https://old.reddit.com/r/WutheringWaves/search.json?q=fanart+OR+cosplay+OR+showcase+OR+lore&restrict_sr=1&sort=new&limit=25",
]
HEADERS = {
    "user-agent": "Mozilla/5.0 (compatible; wuwa-news-bot/1.2; +https://github.com/Kalekakektop2/wuwa-news-bot)"
}


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
            url = href.split("?")[0].rstrip("/")
            posts.append(
                {
                    "id": reddit_id(url),
                    "title": title,
                    "url": url,
                    "author": author.replace("/u/", "").replace("u/", ""),
                    "summary": summary[:800],
                    "image": image,
                    "source": "Reddit",
                    "topics": topic_keys(title, summary),
                }
            )
    return posts


def reddit_id(url: str) -> str:
    match = re.search(r"/comments/([a-z0-9]+)", url, re.I)
    return match.group(1).lower() if match else url.rstrip("/").lower()


def topic_keys(title: str, summary: str = "") -> list[str]:
    blob = f"{title} {summary}".lower()
    keys: list[str] = []
    # одна семья на всю башню — иначе вечный спам разными этажами
    if re.search(r"\btoa\b|tower of adversity|башн", blob):
        keys.append("toa")
    if re.search(r"whiwa|whimpering", blob):
        keys.append("whiwa")
    if re.search(r"hologram|голограмм", blob):
        keys.append("holo")
    if re.search(r"endstate matrix", blob):
        keys.append("endstate")
    if re.search(r"100%|коллекц|all resonator|all character|completion", blob):
        keys.append("collection")
    if re.search(r"fan\s*art|fanart|illustration|drew|drawing|фан.?арт", blob):
        keys.append("fanart")
    if re.search(r"cosplay|косплей", blob):
        keys.append("cosplay")
    if re.search(r"\blore\b|theory|story discussion|лор", blob):
        keys.append("lore")
    if re.search(r"\bbuild\b|rotation|echo set|guide|сборк|гайд|ротац", blob):
        keys.append("build")
    if re.search(r"gacha|pull|won 50|lost 50|c6|s6|выбил|крутк", blob):
        keys.append("gacha")
    if re.search(r"music|cover|ost|soundtrack|музыка", blob):
        keys.append("music")
    if re.search(r"animation|animated|clip|edit|анимац|клип", blob):
        keys.append("animation")
    if re.search(r"exploration|puzzle|hidden|chest|map|нашёл|нашел|исследован", blob):
        keys.append("explore")
    if re.search(r"\bboss\b|weekly challenge|босс", blob) and "toa" not in keys:
        keys.append("boss")
    if re.search(r"showcase|витрин", blob) and "toa" not in keys:
        keys.append("showcase")
    return keys


def primary_topic(post: dict) -> str:
    topics = post.get("topics") or []
    for preferred in (
        "fanart",
        "cosplay",
        "lore",
        "animation",
        "music",
        "build",
        "collection",
        "gacha",
        "holo",
        "explore",
        "boss",
        "showcase",
        "endstate",
        "whiwa",
        "toa",
    ):
        if preferred in topics:
            return preferred
    return topics[0] if topics else "other"


def is_useful(post: dict) -> bool:
    author = (post.get("author") or "").strip()
    if not author or author.lower() in {"automoderator", "[deleted]", "deleted"}:
        return False
    blob = f"{post.get('title', '')}\n{post.get('summary', '')}"
    if SKIP.search(blob) or not KEEP.search(blob):
        return False
    if re.search(r"\b(who should|should i|help me|need help|looking for)\b", blob, re.I):
        return False
    if len((post.get("title") or "").split()) < 3:
        return False
    has_what = bool(
        re.search(
            r"(clear|cleared|record|speedrun|hologram|tower|toa|showcase|collection|"
            r"fan\s*art|fanart|cosplay|lore|build|guide|music|animation|clip|edit|"
            r"exploration|boss|gacha|pull|drew|illustration|"
            r"собрал|прошёл|прошел|рекорд|башн|голограмм|витрин|фан.?арт|косплей|"
            r"лор|сборк|гайд|музыка|анимац|клип|эхо|босс|выбил|нашёл|нашел)",
            blob,
            re.I,
        )
    )
    has_detail = bool(
        re.search(
            r"(\b[A-Z][a-z]{2,}\b|\b\d+\b|mid\s*[1-4]|floor|этаж|"
            r"iuno|qingxiao|jingran|fanart|cosplay|screenshot|art)",
            blob,
        )
    )
    return has_what and has_detail and len(blob.strip()) >= 20


def enrich_post(client: httpx.Client, post: dict) -> dict:
    rid = reddit_id(post["url"])
    try:
        response = client.get(
            f"https://www.reddit.com/comments/{rid}.json",
            headers={"user-agent": "wuwa-community-bot/1.0"},
            timeout=20,
        )
        if response.status_code >= 400:
            return post
        child = response.json()[0]["data"]["children"][0]["data"]
        post["author"] = child.get("author") or post.get("author") or ""
        body = (child.get("selftext") or "").strip()
        if body:
            post["summary"] = body[:800]
        preview = ((child.get("preview") or {}).get("images") or [{}])[0]
        src = ((preview.get("source") or {}).get("url") or "").replace("&amp;", "&")
        if src.startswith("http"):
            post["image"] = src
        elif str(child.get("url") or "").lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            post["image"] = child["url"]
        post["topics"] = topic_keys(post["title"], post.get("summary") or "")
    except Exception:
        logger.info("не смог дочитать reddit %s", rid)
    return post


def parse_reddit_json(payload: dict) -> list[dict]:
    posts: list[dict] = []
    children = ((payload.get("data") or {}).get("children")) or []
    for child in children:
        data = child.get("data") or {}
        title = str(data.get("title") or "").strip()
        permalink = str(data.get("permalink") or "")
        if not title or not permalink:
            continue
        url = "https://www.reddit.com" + permalink.split("?")[0].rstrip("/")
        body = str(data.get("selftext") or "")
        image = None
        preview = ((data.get("preview") or {}).get("images") or [{}])[0]
        src = ((preview.get("source") or {}).get("url") or "").replace("&amp;", "&")
        if src.startswith("http"):
            image = src
        posts.append(
            {
                "id": reddit_id(url),
                "title": title,
                "url": url,
                "author": str(data.get("author") or ""),
                "summary": body[:800],
                "image": image,
                "source": "Reddit",
                "topics": topic_keys(title, body),
            }
        )
    return posts


def fetch_posts(client: httpx.Client) -> list[dict]:
    posts: list[dict] = []
    seen: set[str] = set()
    for url in FEEDS:
        try:
            response = client.get(url, headers=HEADERS, timeout=20)
            if response.status_code >= 400 or not response.text.strip():
                continue
            for post in parse_reddit_rss(response.text):
                if post["id"] in seen:
                    continue
                seen.add(post["id"])
                posts.append(post)
        except Exception as exc:
            logger.warning("лента сообщества: %s", exc)
    # JSON тоже всегда — иначе поиск залипает на одной RSS-выборке
    for url in JSON_FEEDS:
        try:
            response = client.get(url, headers=HEADERS, timeout=20)
            if response.status_code >= 400 or not response.text.strip():
                logger.warning("json лента %s: %s", url, response.status_code)
                continue
            for post in parse_reddit_json(response.json()):
                if post["id"] in seen:
                    continue
                seen.add(post["id"])
                posts.append(post)
        except Exception as exc:
            logger.warning("лента сообщества json: %s", exc)
    return posts


def is_interesting(post: dict) -> bool:
    return is_useful(post)


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
        if not text or text.strip().upper() == "SKIP":
            raise RuntimeError("skip")
        if not draft_is_concrete(text, post):
            raise RuntimeError("слишком пустой рерайт")
        if post["url"] not in text:
            text = f"{text}\n\nисточник — {post['url']}"
        if "официал" not in text.lower() and "kuro" not in text.lower():
            text = f"{text}\nэто не официалка Kuro"
        return text
    except Exception:
        logger.exception("рерайт сообщества не вышел")
        return ""


def draft_is_concrete(text: str, post: dict) -> bool:
    low = text.lower()
    author = (post.get("author") or "").lower()
    has_who = bool(author and author in low) or "игрок" in low or bool(post.get("author"))
    has_what = bool(
        re.search(
            r"(башн|toa|голограмм|рекорд|собрал|прошёл|прошел|этаж|коллекц|витрин|закрыл|"
            r"арт|косплей|сборк|гайд|крут|гача|лор|клип|анимац|музык|эхо|босс|"
            r"нашёл|нашел|выложил|показал|нарисова|фан)",
            low,
        )
    )
    return has_who and has_what and len(text) > 80


def with_footer(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(
        r"\n*(актуальная информация всегда у нас в telegram:[^\n]*)",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"\n*(наш discord[^\n]*|discord\s*[—:-][^\n]*)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return f"{cleaned}\n\n{FOOTER}"


class CommunityState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = {
            "ids": [],
            "community_ids": [],
            "offered_ids": [],
            "skip_ids": [],
            "topic_keys": [],
            "recent_topics": [],
            "update_offset": 0,
            "pending": None,
            "queue": [],
        }
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.data.update(raw)
        self.data.setdefault("recent_topics", [])

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

    def all_seen_ids(self) -> set[str]:
        raw = {str(item) for item in self.data.get("community_ids") or []}
        extra = {reddit_id(item) for item in raw}
        return raw | extra

    def offered_ids(self) -> set[str]:
        raw = {str(item) for item in self.data.get("offered_ids") or []}
        return raw | {reddit_id(item) for item in raw}

    def already_offered(self, post: dict) -> bool:
        ids = self.offered_ids()
        return post.get("id") in ids or reddit_id(post.get("url") or "") in ids

    def topic_overused(self, post: dict) -> bool:
        """Не даём башне/whiwa забить всю ленту: максимум 1 ToA среди последних N."""
        topic = primary_topic(post)
        recent = [str(item) for item in (self.data.get("recent_topics") or [])]
        if topic == "toa":
            window = recent[-TOA_WINDOW:]
            if sum(1 for item in window if item == "toa") >= 1:
                return True
        if topic == "whiwa" and "whiwa" in recent[-TOA_WINDOW:]:
            return True
        return False

    def skip_seen(self, post: dict) -> bool:
        skips = {reddit_id(str(item)) for item in self.data.get("skip_ids") or []}
        return reddit_id(post.get("url") or post.get("id") or "") in skips

    def mark_skip(self, post: dict) -> None:
        skips = {str(item) for item in self.data.get("skip_ids") or []}
        skips.add(reddit_id(post.get("url") or post.get("id") or ""))
        self.data["skip_ids"] = sorted(item for item in skips if item)[-400:]
        self.save()

    def community_seen(self, post: dict | str) -> bool:
        if isinstance(post, str):
            return post in self.offered_ids() or reddit_id(post) in self.offered_ids()
        return self.already_offered(post) or self.skip_seen(post)

    def mark_offered(self, post: dict) -> None:
        ids = self.offered_ids()
        ids.add(str(post.get("id") or ""))
        ids.add(reddit_id(post.get("url") or ""))
        self.data["offered_ids"] = sorted(item for item in ids if item)[-300:]
        recent = [str(item) for item in (self.data.get("recent_topics") or [])]
        recent.append(primary_topic(post))
        self.data["recent_topics"] = recent[-20:]
        self.mark_community(post)

    def mark_community(self, post: dict | str) -> None:
        ids = self.all_seen_ids()
        topics = {str(item) for item in self.data.get("topic_keys") or []}
        if isinstance(post, str):
            ids.add(post)
            ids.add(reddit_id(post))
        else:
            ids.add(str(post.get("id") or ""))
            ids.add(reddit_id(post.get("url") or ""))
            # в topic_keys кладём только cooldown-семьи, чтобы не блокировать весь фан-арт
            topics.update(key for key in (post.get("topics") or []) if key in COOLDOWN_TOPICS)
        self.data["community_ids"] = sorted(item for item in ids if item)[-500:]
        self.data["topic_keys"] = sorted(topics)[-200:]
        self.save()

    def mark_community_many(self, posts: list) -> None:
        for item in posts:
            self.mark_community(item)

    def pending(self) -> dict | None:
        return self.data.get("pending")

    def set_pending(self, payload: dict | None) -> None:
        self.data["pending"] = payload
        self.save()

    def enqueue(self, payload: dict) -> None:
        queue = list(self.data.get("queue") or [])
        queue.append(payload)
        self.data["queue"] = queue[-10:]
        self.save()

    def pop_queue(self) -> dict | None:
        queue = list(self.data.get("queue") or [])
        if not queue:
            return None
        item = queue.pop(0)
        self.data["queue"] = queue
        self.save()
        return item


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


ART_MENU = (
    "https://hw-media-cdn-mingchao.kurogame.com/akiwebsite/website2.0/json/G152/en/"
    "ArticleMenu.json"
)
ART_ARTICLE = (
    "https://hw-media-cdn-mingchao.kurogame.com/akiwebsite/website2.0/json/G152/en/"
    "article/{id}.json"
)
ART_IMG = re.compile(r"src=[\"'](https?://[^\"']+\.(?:jpg|jpeg|png|webp))[\"']", re.I)
ART_VISUAL = re.compile(
    r"wallpaper|version preview|profile reveal|resonator reveal|update content|"
    r"upcoming events|anthropocene|fan creation",
    re.I,
)
ART_SKIP = re.compile(r"maintenance notice|faq|convene details|premium model set", re.I)
ART_HASH_NEAR = 10


def art_key(url: str) -> str:
    return url.split("?", 1)[0].rstrip("/").lower().rsplit("/", 1)[-1]


def used_art_file() -> Path:
    return Path(__file__).resolve().parent.parent / "state" / "used_art.json"


def used_art_paths() -> list[Path]:
    root = Path(__file__).resolve().parent.parent
    return [
        root / "state" / "used_art.json",
        root / "state" / "pikabu.json",
        root / "state" / "daily.json",
        root / "data" / "art_state.json",
        root / "pikabu-bot" / "data" / "seen.json",
    ]


def load_used_blob() -> dict:
    urls: set[str] = set()
    hashes: set[str] = set()
    for path in used_art_paths():
        if not path.exists():
            continue
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        urls.update(str(item) for item in (blob.get("images") or []) if item)
        urls.update(str(item) for item in (blob.get("used_art") or []) if item)
        hashes.update(str(item) for item in (blob.get("hashes") or []) if item)
    return {"used_art": urls, "hashes": hashes}


def save_used_blob(blob: dict) -> None:
    path = used_art_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "used_art": sorted({str(item) for item in blob.get("used_art") or [] if item})[-400:],
                "hashes": sorted({str(item) for item in blob.get("hashes") or [] if item})[-400:],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load_used_art() -> set[str]:
    return set(load_used_blob().get("used_art") or [])


def remember_art(url: str, digest: str | None = None) -> None:
    if not url:
        return
    blob = load_used_blob()
    used = set(blob.get("used_art") or [])
    used.add(url)
    hashes = set(blob.get("hashes") or [])
    if digest:
        hashes.add(digest)
    blob["used_art"] = used
    blob["hashes"] = hashes
    save_used_blob(blob)


def _image_dhash(data: bytes) -> str | None:
    try:
        from io import BytesIO

        from PIL import Image

        image = Image.open(BytesIO(data)).convert("L").resize((17, 16), Image.Resampling.LANCZOS)
        pixels = list(image.getdata())
        bits: list[str] = []
        for row in range(16):
            start = row * 17
            for col in range(16):
                bits.append("1" if pixels[start + col] > pixels[start + col + 1] else "0")
        return hex(int("".join(bits), 2))[2:]
    except Exception:
        return None


def _hash_near(left: str, right: str, limit: int = ART_HASH_NEAR) -> bool:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count() <= limit
    except ValueError:
        return left == right


def art_is_used(url: str, blob: dict | None = None) -> bool:
    if not url:
        return True
    blob = blob or load_used_blob()
    used = {str(item) for item in blob.get("used_art") or []}
    if url in used or art_key(url) in {art_key(item) for item in used}:
        return True
    reddit = "redd.it" in url or "reddit.com" in url
    return reddit


def collect_art_candidates() -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    try:
        with httpx.Client(
            timeout=30,
            headers={"user-agent": "wuwa-community-bot/1.0"},
            follow_redirects=True,
        ) as client:
            menu = client.get(ART_MENU).json()
            articles = 0
            for item in menu:
                title = str(item.get("articleTitle") or "")
                if ART_SKIP.search(title) or not ART_VISUAL.search(title):
                    continue
                found: list[str] = []
                cover = str(item.get("suggestCover") or "")
                if cover.startswith("http"):
                    found.append(cover)
                try:
                    raw = client.get(ART_ARTICLE.format(id=item["articleId"])).json()
                    found.extend(ART_IMG.findall(raw.get("articleContent") or ""))
                except Exception:
                    pass
                taken = 0
                for url in found:
                    if "emoji" in url.lower() or "gif" in url.lower():
                        continue
                    key = art_key(url)
                    if key in seen:
                        continue
                    seen.add(key)
                    urls.append(url)
                    taken += 1
                    if taken >= 4:
                        break
                articles += 1
                if articles >= 40:
                    break
    except Exception:
        logger.exception("не собрал пул артов")
    return urls


def _download_art(url: str) -> bytes | None:
    try:
        raw = httpx.get(
            url,
            timeout=40,
            headers={"user-agent": "wuwa-news/1.0"},
            follow_redirects=True,
        )
        raw.raise_for_status()
        return raw.content
    except Exception:
        return None


def _prepare_photo(url: str) -> bytes | None:
    data = _download_art(url)
    if not data:
        return None
    try:
        from io import BytesIO

        from PIL import Image

        image = Image.open(BytesIO(data))
        if image.mode != "RGB":
            image = image.convert("RGB")
        width, height = image.size
        if width < 200 or height < 200:
            return None
        scale = 1.0
        longest = max(width, height)
        if longest > 2560:
            scale = min(scale, 2560 / longest)
        if width + height > 10000:
            scale = min(scale, 10000 / (width + height))
        if scale < 1:
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS,
            )
        out = BytesIO()
        image.save(out, format="JPEG", quality=92)
        return out.getvalue()
    except Exception:
        return data if data[:3] == b"\xff\xd8" else None


def pick_fresh_art(blob: dict | None = None, *, commit: bool = True) -> str | None:
    blob = blob or load_used_blob()
    used_keys = {art_key(str(item)) for item in blob.get("used_art") or []}
    hashes = {str(item) for item in blob.get("hashes") or []}
    import random

    choices = [url for url in collect_art_candidates() if art_key(url) not in used_keys]
    random.shuffle(choices)
    for url in choices:
        data = _download_art(url)
        if not data:
            continue
        digest = _image_dhash(data)
        if digest and any(_hash_near(digest, known) for known in hashes):
            # визуальный дубль — навсегда в used, чтобы не крутить снова
            remember_art(url, digest)
            used_keys.add(art_key(url))
            hashes.add(digest)
            logger.info("арт визуально уже был, пропускаю %s", art_key(url))
            continue
        if commit:
            remember_art(url, digest)
        return url
    return None


def pick_art(image_url: str | None = None, *, commit: bool = True) -> str | None:
    blob = load_used_blob()
    if image_url and not art_is_used(image_url, blob):
        if commit:
            remember_art(image_url)
        return image_url
    return pick_fresh_art(blob, commit=commit)


def post_telegram(text: str, image_url: str | None = None, *, pick: bool = True) -> None:
    token = env("TELEGRAM_BOT_TOKEN")
    chat = env("TELEGRAM_CHANNEL_ID")
    if not token or not chat:
        raise RuntimeError("нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
    caption = text[:1000]
    if pick:
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
        photo = _prepare_photo(image_url)
        if photo:
            try:
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


def post_discord(text: str, image_url: str | None = None, *, pick: bool = True) -> None:
    hook = env("DISCORD_WEBHOOK_URL")
    if not hook:
        logger.warning("нет DISCORD_WEBHOOK_URL, Discord пропускаю")
        return
    if pick:
        image_url = pick_art(image_url)
    payload = {
        "username": "Wuwa News",
        "content": text[:1900],
        "allowed_mentions": {"parse": []},
    }
    if image_url:
        payload["embeds"] = [{"image": {"url": image_url}}]
    with httpx.Client(timeout=30) as client:
        response = client.post(f"{hook}?wait=true", json=payload)
        if response.status_code >= 400 and image_url:
            payload.pop("embeds", None)
            response = client.post(f"{hook}?wait=true", json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"Discord webhook: {response.status_code} {response.text[:200]}")


def offer_post(token: str, admin_id: str, state: CommunityState, post: dict) -> bool:
    if state.pending():
        logger.info("ждём ответа, новый пост не предлагаю")
        return False
    body = rewrite(post)
    if not body or not draft_is_concrete(body, post):
        state.mark_skip(post)
        logger.info("черновик пустой, пропускаю: %s", post.get("title"))
        return False
    public = with_footer(body)
    art = pick_art(post.get("image"), commit=False)
    state.mark_offered(post)
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
    return True


def diversity_score(post: dict, state: CommunityState) -> int:
    topic = primary_topic(post)
    recent = [str(item) for item in (state.data.get("recent_topics") or [])]
    score = 0
    # сильнее любим то, чего давно не было
    if topic not in recent[-6:]:
        score += 30
    if topic not in {"toa", "whiwa"}:
        score += 20
    if topic in {"fanart", "cosplay", "lore", "animation", "music", "build", "collection"}:
        score += 15
    if topic == "toa":
        score -= 25
    if post.get("image"):
        score += 5
    return score


def find_next(client: httpx.Client, state: CommunityState) -> dict | None:
    posts = fetch_posts(client)
    candidates: list[dict] = []
    for post in posts:
        if state.already_offered(post) or state.skip_seen(post):
            continue
        post = enrich_post(client, post)
        if state.already_offered(post) or state.topic_overused(post):
            continue
        if not is_useful(post):
            state.mark_skip(post)
            logger.info("слабо: %s", post.get("title"))
            continue
        candidates.append(post)
    if not candidates:
        return None
    candidates.sort(key=lambda item: diversity_score(item, state), reverse=True)
    chosen = candidates[0]
    logger.info(
        "выбрал фан-пост [%s] score=%s: %s",
        primary_topic(chosen),
        diversity_score(chosen, state),
        chosen.get("title"),
    )
    return chosen


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
        # один арт на оба канала; если он уже был в группе — берём новый
        image = pick_art(pending.get("image"))
        post_telegram(pending["text"], image, pick=False)
        post_discord(pending["text"], image, pick=False)
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
) -> bool:
    if state.pending():
        state.enqueue(
            {
                "title": title,
                "text": text,
                "image": image,
                "kind": kind,
            }
        )
        logger.info("очередь: висит прошлый черновик, этот подождёт (%s)", title)
        return False
    public = with_footer(text)
    art = pick_art(image, commit=False)
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
    return True


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

    queued = state.pop_queue()
    if queued:
        offer_ready_draft(
            token,
            admin_id,
            state,
            title=queued.get("title") or "Черновик из очереди. Выкладываем?",
            text=queued.get("text") or "",
            image=queued.get("image"),
            kind=queued.get("kind") or "daily",
        )
        return

    if not offer_new:
        return

    with httpx.Client() as client:
        for _ in range(8):
            nxt = find_next(client, state)
            if not nxt:
                break
            if offer_post(token, admin_id, state, nxt):
                return
        if decision == "no":
            send_dm(token, admin_id, "свежего интересного пока нет.")
        else:
            logger.info("интересного с форумов нет")

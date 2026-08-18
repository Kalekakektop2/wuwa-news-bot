from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import sys
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from PIL import Image
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
load_dotenv(ROOT / ".env")
load_dotenv(REPO / ".env")
load_dotenv(REPO / "pikabu-bot" / ".env")

UTC8 = timezone(timedelta(hours=8))
OFFICIAL_BASE = (
    "https://hw-media-cdn-mingchao.kurogame.com/akiwebsite/website2.0/json/G152/en"
)
GITHUB_RUS = "https://github.com/Kalekakektop2/Wuwa3.5"
CODE_HINT = re.compile(
    r"(redeem|redemption|exchange code|gift code|promo code|cdkey|промокод)",
    re.I,
)
CODE_TOKEN = re.compile(r"\[([A-Z0-9]{6,20})\]|\b([A-Z0-9]{8,20})\b")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def now_utc8() -> datetime:
    return datetime.now(UTC8)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_day(raw: str) -> date:
    return date.fromisoformat(raw[:10])


def parse_dt(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def days_left(end: date, today: date) -> int:
    return (end - today).days


def ru_days(n: int) -> str:
    n_abs = abs(n)
    if n_abs % 10 == 1 and n_abs % 100 != 11:
        word = "день"
    elif n_abs % 10 in {2, 3, 4} and n_abs % 100 not in {12, 13, 14}:
        word = "дня"
    else:
        word = "дней"
    return f"{n} {word}"


class State:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = {
            "codes": [],
            "images": [],
            "cards": [],
            "community": [],
            "last": {},
        }
        if path.exists():
            self.data.update(json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def extract_codes(text: str) -> list[str]:
    codes: list[str] = []
    for line in (text or "").splitlines():
        if not CODE_HINT.search(line) and "[" not in line:
            continue
        if not CODE_HINT.search(line) and not re.search(r"\[[A-Z0-9]{6,20}\]", line):
            continue
        for match in CODE_TOKEN.finditer(line):
            item = match.group(1) or match.group(2)
            if not item or item.isdigit() or item in codes:
                continue
            if re.search(r"[A-Z]", item) and re.search(r"\d", item):
                codes.append(item)
            elif match.group(1) and re.search(r"[A-Z]", item):
                codes.append(item)
    return codes[:8]


def fetch_official_codes(client: httpx.Client) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    menu = client.get(f"{OFFICIAL_BASE}/ArticleMenu.json?t={int(now_utc8().timestamp())}")
    menu.raise_for_status()
    for item in menu.json()[:20]:
        title = str(item.get("articleTitle") or "")
        article_id = item.get("articleId")
        if not article_id:
            continue
        blob = title
        if re.search(r"redeem|redemption|gift code|exchange code", title, re.I):
            raw = client.get(f"{OFFICIAL_BASE}/article/{article_id}.json").json()
            blob = f"{title}\n{html_to_text(raw.get('articleContent') or '')}"
        for code in extract_codes(blob):
            found.append((code, "официальный сайт"))
    return found


def fetch_x_codes(client: httpx.Client) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    urls = [
        "https://rsshub.app/twitter/user/Wuthering_Waves",
        "https://nitter.poast.org/Wuthering_Waves/rss",
    ]
    for url in urls:
        try:
            response = client.get(url, timeout=20)
            if response.status_code >= 400:
                continue
            text = html_to_text(response.text)
            if not CODE_HINT.search(text) and "[" not in text:
                continue
            for code in extract_codes(text):
                found.append((code, "официальный X"))
            if found:
                return found
        except Exception:
            continue
    return found


def html_to_text(raw: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", str(raw or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


COMMUNITY_KEEP = re.compile(
    r"(record|showcase|collection|collect|meta|hologram|tower|"
    r"whimpering|clear|cleared|roster|gallery|speedrun|first clear|"
    r"full team|all resonator|all character|c6|s6|100%|completion|"
    r"build|guide|clip|gameplay|exploration|boss|gacha|pull|"
    r"рекорд|коллекц|мет[аы]|башн|голограмм|витрина|собрал|"
    r"сборк|гайд|клип|геймплей|скрин)",
    re.I,
)
COMMUNITY_SKIP = re.compile(
    r"(megathread|giveaway|leak|nsfw|porn|code redeem|looking for|"
    r"who should i pull|should i pull|wutheringwavesmod|"
    r"daily questions|weekly questions|"
    r"cosplay|fan\s*art|fanart|illustration|drew|drawing|convention|"
    r"косплей|фан.?арт)",
    re.I,
)


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
        summary = html_to_text(entry.findtext("a:content", default="", namespaces=ns) or "")
        if title and href:
            posts.append(
                {
                    "title": title,
                    "url": href.split("?")[0],
                    "author": author.replace("/u/", "").replace("u/", ""),
                    "summary": summary[:400],
                }
            )
    return posts


def fetch_community(client: httpx.Client, state: State) -> dict | None:
    """Берём разнообразие из общего community-поисковика, а не только ToA."""
    used = set(state.data.get("community") or [])
    try:
        sys.path.insert(0, str(REPO / "pikabu-bot"))
        from community import CommunityState, find_next

        pikabu_path = Path(env("PIKABU_STATE") or str(REPO / "state" / "pikabu.json"))
        community_state = CommunityState(pikabu_path)
        post = find_next(client, community_state)
        if post and post.get("url") not in used:
            community_state.mark_offered(post)
            return post
    except Exception as exc:
        print(f"community shared finder: {exc}", file=sys.stderr)

    headers = {
        "user-agent": "wuwa-daily/1.0 (fan news; +https://github.com/Kalekakektop2/wuwa-news-bot)"
    }
    urls = [
        "https://www.reddit.com/r/WutheringWaves/search.rss?q=showcase+OR+build+OR+guide+OR+gameplay&restrict_sr=1&sort=new",
        "https://www.reddit.com/r/WutheringWaves/search.rss?q=collection+OR+hologram+OR+echo+OR+exploration&restrict_sr=1&sort=new",
        "https://www.reddit.com/r/WutheringWaves/top/.rss?t=week",
        "https://www.reddit.com/r/WutheringWaves/.rss",
    ]
    posts: list[dict] = []
    for url in urls:
        try:
            response = client.get(url, headers=headers, timeout=20)
            if response.status_code >= 400 or not response.text.strip():
                continue
            posts.extend(parse_reddit_rss(response.text))
        except Exception as exc:
            print(f"community rss: {exc}", file=sys.stderr)
    interesting: list[dict] = []
    for post in posts:
        if COMMUNITY_SKIP.search(post["title"]):
            continue
        if not COMMUNITY_KEEP.search(post["title"]):
            continue
        if post["url"] in used:
            continue
        title_l = post["title"].lower()
        # башню в fallback кладём в конец
        rank = 1 if re.search(r"\btoa\b|tower of adversity", title_l) else 0
        interesting.append((rank, post))
    interesting.sort(key=lambda item: item[0])
    return interesting[0][1] if interesting else None


def rewrite_community(post: dict) -> str:
    key = env("GEMINI_API_KEY")
    fallback = (
        f"с сообщества\n\n"
        f"на Reddit игрок {post['author'] or 'из вувы'} выложил: {post['title']}\n"
        "цифры и скрины лучше смотреть в исходнике — сами ничего не придумываем.\n\n"
        f"источник — {post['url']}\n"
        "это не официалка Kuro"
    )
    if not key:
        return fallback
    prompt = (
        "Перескажи коротко пост игрока по Wuthering Waves для русскоязычного фан-канала.\n"
        "Живой тон, 4–7 строк. Не выдумывай цифры, рекорды и имена, которых нет во входе.\n"
        "Не называй себя ботом. Не притворяйся Kuro.\n"
        "Не ставь футер про Telegram.\n"
        "Верни только текст поста."
    )
    user = (
        f"Заголовок: {post['title']}\n"
        f"Автор: {post['author'] or 'неизвестен'}\n"
        f"Ссылка: {post['url']}\n"
        f"Обрывок текста: {post.get('summary') or 'нет'}"
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
                    "system_instruction": {"parts": [{"text": prompt}]},
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
        if not text:
            raise RuntimeError("empty")
        if post["url"] not in text:
            text = f"{text}\n\nисточник — {post['url']}"
        if "официал" not in text.lower() and "kuro" not in text.lower():
            text = f"{text}\nэто не официалка Kuro"
        return text
    except Exception as exc:
        print(f"community rewrite: {exc}", file=sys.stderr)
        return fallback


def build_community_text(post: dict) -> str:
    return rewrite_community(post)


IMG_SRC = re.compile(r"src=[\"'](https?://[^\"']+\.(?:jpg|jpeg|png|webp))[\"']", re.I)
VISUAL_TITLES = re.compile(
    r"wallpaper|version preview|profile reveal|resonator reveal|update content|upcoming events",
    re.I,
)
SKIP_TITLES = (
    "fan creation event winners",
    "premium model set",
    "maintenance notice",
    "convene details",
    "faq",
)


ART_POOL_VERSION = 2


def collect_art_pool(client: httpx.Client, state: State) -> list[tuple[str, str]]:
    cached = state.data.get("art_pool") or []
    cached_at = state.data.get("art_pool_at")
    if (
        cached
        and cached_at
        and state.data.get("art_pool_version") == ART_POOL_VERSION
    ):
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(cached_at)
            if age < timedelta(hours=12) and len(cached) >= 6:
                return [(item["url"], item["title"]) for item in cached]
        except ValueError:
            pass

    menu = client.get(f"{OFFICIAL_BASE}/ArticleMenu.json?t={int(now_utc8().timestamp())}")
    menu.raise_for_status()
    candidates = []
    for item in menu.json():
        title = str(item.get("articleTitle") or "")
        low = title.lower()
        if any(word in low for word in SKIP_TITLES):
            continue
        if not VISUAL_TITLES.search(title):
            continue
        if item.get("articleId"):
            candidates.append(item)
        if len(candidates) >= 16:
            break

    pool: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        title = str(item.get("articleTitle") or "").strip()
        urls: list[str] = []
        cover = str(item.get("suggestCover") or "")
        if cover.startswith("http"):
            urls.append(cover)
        try:
            raw = client.get(
                f"{OFFICIAL_BASE}/article/{item['articleId']}.json?t={int(now_utc8().timestamp())}"
            ).json()
            html_body = raw.get("articleContent") or ""
            urls.extend(IMG_SRC.findall(html_body))
        except Exception:
            continue
        # Только первая широкая картинка статьи — как обложка у постов в канале.
        for url in urls:
            if url in seen or "emoji" in url.lower():
                continue
            seen.add(url)
            pool.append((url, title))
            break

    if pool:
        state.data["art_pool"] = [{"url": url, "title": title} for url, title in pool]
        state.data["art_pool_at"] = datetime.now(timezone.utc).isoformat()
        state.data["art_pool_version"] = ART_POOL_VERSION
    return pool


def _art_key(url: str) -> str:
    return url.split("?", 1)[0].rstrip("/").lower().rsplit("/", 1)[-1]


def _dhash(data: bytes) -> str | None:
    try:
        image = Image.open(BytesIO(data)).convert("L").resize((17, 16), Image.Resampling.LANCZOS)
        pixels = list(image.getdata())
        bits = []
        for row in range(16):
            start = row * 17
            for col in range(16):
                bits.append("1" if pixels[start + col] > pixels[start + col + 1] else "0")
        return hex(int("".join(bits), 2))[2:]
    except Exception:
        return None


def pick_random_art(client: httpx.Client, state: State) -> tuple[str, bytes] | None:
    pool = collect_art_pool(client, state)
    if not pool:
        return None
    recent = set(state.data.get("images") or [])
    hashes: set[str] = set()
    used_path = REPO / "state" / "used_art.json"
    if used_path.exists():
        try:
            blob = json.loads(used_path.read_text(encoding="utf-8"))
            recent.update(blob.get("used_art") or [])
            hashes.update(str(item) for item in (blob.get("hashes") or []) if item)
        except Exception:
            pass
    recent_keys = {_art_key(url) for url in recent}
    choices = [item for item in pool if _art_key(item[0]) not in recent_keys]
    random.shuffle(choices)
    for url, _title in choices:
        photo = prepare_photo(url)
        if not photo:
            continue
        digest = _dhash(photo)
        if digest and any(
            (int(digest, 16) ^ int(known, 16)).bit_count() <= 10 for known in hashes if known
        ):
            recent.add(url)
            recent_keys.add(_art_key(url))
            hashes.add(digest)
            continue
        used = state.data.setdefault("images", [])
        used.append(url)
        state.data["images"] = used[-200:]
        recent.add(url)
        if digest:
            hashes.add(digest)
        used_path.parent.mkdir(parents=True, exist_ok=True)
        used_path.write_text(
            json.dumps(
                {"used_art": sorted(recent)[-400:], "hashes": sorted(hashes)[-400:]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return url, photo
    return None


def active_banners(calendar: dict, today: date) -> list[dict]:
    out = []
    for item in calendar.get("banners") or []:
        start, end = parse_day(item["start"]), parse_day(item["end"])
        if start <= today <= end:
            out.append({**item, "left": days_left(end, today)})
    return out


def upcoming_banners(calendar: dict, today: date) -> list[dict]:
    out = []
    for item in calendar.get("banners") or []:
        start = parse_day(item["start"])
        if today < start <= today + timedelta(days=21):
            out.append({**item, "in": (start - today).days})
    return out


def build_calendar_text(calendar: dict, today: date, *, week: bool = False) -> str:
    lines: list[str] = []
    if week:
        lines.append("на неделе по вуве")
    else:
        lines.append("календарь на сегодня")

    banners = active_banners(calendar, today)
    if banners:
        lines.append("")
        soon = min(item["left"] for item in banners)
        if soon <= 0:
            lines.append("баннеры сегодня заканчиваются:")
        else:
            lines.append(f"текущие баннеры, осталось {ru_days(soon)}:")
        for item in banners:
            lines.append(f"— {item['name']}, {item['kind']}")
    else:
        lines.append("")
        lines.append("сейчас лимитки между фазами или уже 3.6 — смотри ниже")

    coming = upcoming_banners(calendar, today)
    if coming:
        lines.append("")
        lines.append("скоро:")
        for item in coming:
            when = "завтра" if item["in"] == 1 else f"через {ru_days(item['in'])}"
            lines.append(f"— {item['name']} ({item['phase']}), {when}")

    pred = calendar.get("predownload") or {}
    if pred.get("start"):
        start = parse_dt(pred["start"])
        if today <= start.date():
            lines.append("")
            if today == start.date():
                lines.append(f"сегодня {pred.get('note', 'предзагрузка')} с {start.strftime('%H:%M')} UTC+8")
            elif start.date() - today <= timedelta(days=3):
                lines.append(
                    f"{pred.get('note', 'предзагрузка')} {start.strftime('%d.%m')} "
                    f"в {start.strftime('%H:%M')} UTC+8"
                )

    if today.weekday() == 0:
        lines.append("")
        lines.append("понедельник: недельные боссы обновились в 04:00 UTC+8")

    lines.append("")
    lines.append("это календарь канала, не расписание Kuro")
    return "\n".join(lines).strip()


def build_codes_text(codes: list[tuple[str, str]]) -> str:
    uniq = []
    seen = set()
    for code, src in codes:
        if code in seen:
            continue
        seen.add(code)
        uniq.append((code, src))
    lines = ["свежие коды", ""]
    for code, src in uniq:
        lines.append(code)
    lines.append("")
    lines.append("настройки → другие → код подарка")
    lines.append("если не вводится — уже истёк, не бейте гонца")
    return "\n".join(lines)


def build_card_text(card: dict, kind: str) -> str:
    if kind == "character":
        return f"{card['name']}\n\n{card['line']}"
    return f"эхо: {card['name']}\n\n{card['line']}"


def build_rus_text(version: str) -> str:
    return (
        f"если после патча {version} куски текста снова на английском — это нормально.\n"
        "русификатор неофициальный, новые строки появляются только в следующей сборке.\n\n"
        f"актуальные файлы: {GITHUB_RUS}\n"
        "язык в игре оставляйте English"
    )


def maintenance_start(calendar: dict) -> tuple[dict, datetime] | None:
    maint = calendar.get("maintenance") or {}
    if not maint.get("start"):
        return None
    start = parse_dt(maint["start"])
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC8)
    return maint, start


def maintenance_due(calendar: dict, state: State, now: datetime | None = None) -> dict | None:
    parsed = maintenance_start(calendar)
    if not parsed:
        return None
    maint, start = parsed
    moment = now or now_utc8()
    key = f"{maint.get('version', '')}|{start.isoformat()}"
    if (state.data.get("last") or {}).get("maintenance") == key:
        return None
    delta = start - moment
    if timedelta(minutes=10) < delta <= timedelta(minutes=80):
        return {**maint, "start_dt": start, "key": key}
    return None


def build_maintenance_text(maint: dict) -> str:
    start: datetime = maint["start_dt"]
    end_raw = maint.get("end")
    window = start.strftime("%H:%M")
    if end_raw:
        end = parse_dt(end_raw)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC8)
        window = f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
    lines = [
        f"через час техработы {maint.get('version', '')}".strip(),
        "",
        f"окно: {window} UTC+8",
    ]
    if maint.get("compensation"):
        lines.append(f"компенсация: {maint['compensation']}")
    lines.extend(
        [
            "",
            "в игру не пустит, почту лучше проверить заранее",
        ]
    )
    return "\n".join(lines)


def prepare_photo(image_url: str) -> bytes | None:
    try:
        raw = httpx.get(
            image_url,
            timeout=40,
            headers={"user-agent": "wuwa-daily/1.0"},
            follow_redirects=True,
        )
        raw.raise_for_status()
        image = Image.open(BytesIO(raw.content))
        if image.mode != "RGB":
            image = image.convert("RGB")
        width, height = image.size
        if width < 200 or height < 200:
            return None
        # Не кадрируем: уменьшаем целиком, чтобы влезла вся картинка.
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
    except Exception as exc:
        print(f"art prepare failed: {exc}", file=sys.stderr)
        return None


DEFAULT_DISCORD_INVITE = "https://discord.gg/RvBpRACXAE"


def discord_invite() -> str:
    return env("DISCORD_INVITE") or DEFAULT_DISCORD_INVITE


def with_discord_invite(text: str) -> str:
    invite = discord_invite()
    if "discord.gg/" in text.lower():
        return text
    return f"{text.rstrip()}\n\nнаш Discord — {invite}"


def for_discord(text: str) -> str:
    invite = discord_invite()
    out = text.replace(f"\n\nнаш Discord — {invite}", "").replace(f"\nнаш Discord — {invite}", "")
    return out.strip()[:1900]


def send_discord(text: str, image_url: str | None = None) -> None:
    hook = env("DISCORD_WEBHOOK_URL")
    if not hook:
        print("DISCORD_WEBHOOK_URL нет, Discord пропускаю", file=sys.stderr)
        return
    payload = {
        "username": "Wuwa News",
        "content": for_discord(text),
        "allowed_mentions": {"parse": []},
    }
    if image_url:
        payload["embeds"] = [{"image": {"url": image_url}}]
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{hook}?wait=true", json=payload)
            if response.status_code >= 400 and image_url:
                payload.pop("embeds", None)
                response = client.post(f"{hook}?wait=true", json=payload)
            if response.status_code >= 400:
                print(f"discord webhook: {response.status_code} {response.text[:200]}", file=sys.stderr)
            else:
                print("discord sent")
    except Exception as exc:
        print(f"discord webhook: {exc}", file=sys.stderr)


def send_telegram(text: str, image_url: str | None = None, photo: bytes | None = None) -> None:
    token = env("TELEGRAM_BOT_TOKEN")
    chat = env("TELEGRAM_CHANNEL_ID")
    if not token or not chat:
        raise RuntimeError("нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
    api = f"https://api.telegram.org/bot{token}"
    caption = text[:1000]
    with httpx.Client(timeout=60) as client:
        payload = photo or (prepare_photo(image_url) if image_url else None)
        if payload:
            data = client.post(
                f"{api}/sendPhoto",
                data={"chat_id": chat, "caption": caption},
                files={"photo": ("art.jpg", payload, "image/jpeg")},
            ).json()
            if data.get("ok"):
                return
            print(f"sendPhoto failed: {data}", file=sys.stderr)
            data = client.post(
                f"{api}/sendDocument",
                data={"chat_id": chat, "caption": caption},
                files={"document": ("art.jpg", payload, "image/jpeg")},
            ).json()
            if data.get("ok"):
                return
            print(f"sendDocument failed: {data}", file=sys.stderr)
        data = client.post(
            f"{api}/sendMessage",
            json={"chat_id": chat, "text": text[:3900]},
        ).json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram: {data}")


def pick_card(roster: dict, state: State, kind: str) -> dict | None:
    used = set(state.data.get("cards") or [])
    pool = roster.get("characters" if kind == "character" else "echoes") or []
    for item in pool:
        key = f"{kind}:{item['id']}"
        if key not in used:
            return {**item, "key": key}
    if pool:
        item = pool[0]
        return {**item, "key": f"{kind}:{item['id']}"}
    return None


def decide(slot: str, calendar: dict, roster: dict, state: State) -> dict:
    today = now_utc8().date()
    last = state.data.setdefault("last", {})
    if last.get(slot) == today.isoformat() and slot != "force":
        return {"skip": True, "reason": f"{slot} уже был сегодня"}

    maint = calendar.get("maintenance") or {}
    maint_day = parse_dt(maint["start"]).date() if maint.get("start") else None

    if slot == "morning":
        return {"kind": "community", "slot": slot, "fallback": "codes_or_card"}

    if slot == "evening":
        if maint_day and today in {maint_day, maint_day + timedelta(days=1)}:
            if last.get("rus_patch") != maint.get("version"):
                return {"kind": "rus", "slot": slot, "version": maint.get("version", "")}
        return {"kind": "community", "slot": slot, "fallback": "character"}

    if slot == "maintenance":
        return {"kind": "maintenance", "slot": slot}
    if slot == "community":
        return {"kind": "community", "slot": slot}

    return {"kind": "calendar", "slot": slot}


def run_slot(slot: str, *, dry_run: bool) -> int:
    calendar = load_json(ROOT / "data" / "calendar.json")
    roster = load_json(ROOT / "data" / "roster.json")
    state = State(Path(env("DAILY_STATE") or str(REPO / "state" / "daily.json")))
    due = maintenance_due(calendar, state)
    if slot == "maintenance":
        if not due:
            print("до техработ ещё рано или уже писали")
            return 0
        plan = {"kind": "maintenance", "slot": "maintenance"}
    else:
        plan = decide(slot, calendar, roster, state)
        if plan.get("skip"):
            print(plan["reason"])
            return 0

    text = ""
    image = None
    photo = None
    kind = plan["kind"]
    headers = {"user-agent": "wuwa-daily/1.0"}

    with httpx.Client(timeout=25, headers=headers, follow_redirects=True) as client:
        art = pick_random_art(client, state)
        if art:
            image, photo = art
        if kind == "maintenance" and due:
            text = build_maintenance_text(due)
            state.data.setdefault("last", {})["maintenance"] = due["key"]
        elif kind == "codes_or_calendar":
            codes = []
            try:
                codes.extend(fetch_official_codes(client))
            except Exception as exc:
                print(f"official codes: {exc}", file=sys.stderr)
            try:
                codes.extend(fetch_x_codes(client))
            except Exception as exc:
                print(f"x codes: {exc}", file=sys.stderr)
            fresh = [(c, s) for c, s in codes if c not in set(state.data.get("codes") or [])]
            if fresh:
                kind = "codes"
                text = build_codes_text(fresh)
                state.data.setdefault("codes", []).extend(c for c, _ in fresh)
            else:
                kind = "calendar"
                text = build_calendar_text(calendar, now_utc8().date(), week=False)
        elif kind == "calendar":
            text = build_calendar_text(calendar, now_utc8().date(), week=bool(plan.get("week")))
        elif kind == "rus":
            text = build_rus_text(plan.get("version") or calendar.get("next_version") or "")
            state.data.setdefault("last", {})["rus_patch"] = plan.get("version")
        elif kind == "community":
            post = None
            try:
                post = fetch_community(client, state)
            except Exception as exc:
                print(f"community: {exc}", file=sys.stderr)
            if not post:
                fallback = plan.get("fallback") or "character"
                if fallback == "codes_or_card":
                    codes = []
                    try:
                        codes.extend(fetch_official_codes(client))
                    except Exception as exc:
                        print(f"official codes: {exc}", file=sys.stderr)
                    fresh = [(c, s) for c, s in codes if c not in set(state.data.get("codes") or [])]
                    if fresh:
                        kind = "codes"
                        text = build_codes_text(fresh)
                        state.data.setdefault("codes", []).extend(c for c, _ in fresh)
                    else:
                        fallback = "character"
                if fallback == "character" and not text:
                    card = pick_card(roster, state, "character")
                    kind = "character"
                    text = (
                        build_card_text(card, "character")
                        if card
                        else build_calendar_text(calendar, now_utc8().date())
                    )
                    if card:
                        state.data.setdefault("cards", []).append(card["key"])
            else:
                text = build_community_text(post)
                used = state.data.setdefault("community", [])
                used.append(post["url"])
                state.data["community"] = used[-80:]
        elif kind in {"character", "echo"}:
            card = pick_card(roster, state, kind)
            if not card:
                text = build_calendar_text(calendar, now_utc8().date())
                kind = "calendar"
            else:
                text = build_card_text(card, kind)
                used = state.data.setdefault("cards", [])
                used.append(card["key"])
                state.data["cards"] = used[-80:]

    print(f"slot={slot} kind={kind}\n{text}")
    if image:
        print(f"image={image}")
    if dry_run:
        return 0

    text = with_discord_invite(text)
    if slot != "maintenance":
        state.data.setdefault("last", {})[slot] = now_utc8().date().isoformat()
    state.save()
    if slot == "maintenance":
        send_telegram(text, image, photo)
        send_discord(text, image)
        print("sent")
        return 0
    offered = offer_daily_approval(text, image, kind, slot)
    print("offered" if offered else "offer failed")
    return 0


def offer_daily_approval(text: str, image: str | None, kind: str, slot: str) -> bool:
    token = env("PIKABU_BOT_TOKEN")
    admin_id = env("PIKABU_ADMIN_ID", "855159275")
    if not token:
        print("нет PIKABU_BOT_TOKEN, выкладываю сразу", file=sys.stderr)
        send_telegram(text, image)
        send_discord(text, image)
        return False
    sys.path.insert(0, str(REPO / "pikabu-bot"))
    from community import CommunityState, offer_ready_draft

    state_path = Path(env("PIKABU_STATE") or str(REPO / "state" / "pikabu.json"))
    if not state_path.exists():
        alt = REPO / "pikabu-bot" / "data" / "seen.json"
        if alt.exists():
            state_path = alt
    title = {
        "community": "Дневной пост с форума. Выкладываем?",
        "codes": "Дневной пост: коды. Выкладываем?",
        "character": "Дневной пост: персонаж. Выкладываем?",
        "echo": "Дневной пост: эхо. Выкладываем?",
        "rus": "Дневной пост: русификатор. Выкладываем?",
    }.get(kind, "Дневной пост. Выкладываем?")
    ok = offer_ready_draft(
        token,
        admin_id,
        CommunityState(state_path),
        title=f"{title} ({slot})",
        text=text,
        image=image,
        kind="daily",
    )
    if not ok:
        print("черновик в очереди: ждём ответа на прошлый")
    return ok


def infer_slot() -> str:
    hour = datetime.now(timezone.utc).hour
    return "morning" if hour < 12 else "evening"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Daily WuWa channel content")
    parser.add_argument(
        "--slot",
        choices=["morning", "evening", "maintenance", "community", "auto"],
        default="auto",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="игнорировать лимит 1 пост на слот")
    args = parser.parse_args()
    slot = infer_slot() if args.slot == "auto" else args.slot
    if args.force:
        state_path = Path(env("DAILY_STATE") or str(REPO / "state" / "daily.json"))
        if state_path.exists():
            state = State(state_path)
            state.data.setdefault("last", {}).pop(slot, None)
            state.save()
    return run_slot(slot, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

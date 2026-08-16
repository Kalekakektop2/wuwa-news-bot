from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / "daily" / ".env")
load_dotenv(ROOT.parent / "discord-bot" / ".env")

DEFAULT_INVITE = "https://discord.gg/RvBpRACXAE"


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def discord_invite() -> str:
    return env("DISCORD_INVITE") or DEFAULT_INVITE


def for_discord(text: str) -> str:
    invite = discord_invite()
    out = text.replace(f"\n\nнаш Discord — {invite}", "").replace(f"\nнаш Discord — {invite}", "")
    return out.strip()[:1900]


def posted_from_worker(payload: dict) -> tuple[str | None, str]:
    preview = str(payload.get("preview") or "").strip()
    for line in payload.get("log") or []:
        text = str(line)
        if text.startswith("posted "):
            article_id = text.split()[1]
            return article_id, preview
    return None, preview


OFFICIAL_BASE = (
    "https://hw-media-cdn-mingchao.kurogame.com/akiwebsite/website2.0/json/G152/en"
)


def article_image(article_id: str) -> str | None:
    try:
        with httpx.Client(timeout=20, headers={"user-agent": "wuwa-news/1.0"}) as client:
            raw = client.get(f"{OFFICIAL_BASE}/article/{article_id}.json").json()
            cover = str(raw.get("suggestCover") or "")
            if cover.startswith("http"):
                return cover
            html = str(raw.get("articleContent") or "")
            match = re.search(
                r'src=["\'](https?://[^"\']+\.(?:jpg|jpeg|png|webp))', html, re.I
            )
            if match:
                return match.group(1)
            menu = client.get(f"{OFFICIAL_BASE}/ArticleMenu.json").json()
        item = next((x for x in menu if str(x.get("articleId")) == str(article_id)), {})
        cover = str(item.get("suggestCover") or "")
        return cover if cover.startswith("http") else None
    except Exception:
        return None


def fallback_art(exclude: str | None = None) -> str | None:
    try:
        with httpx.Client(timeout=20, headers={"user-agent": "wuwa-news/1.0"}) as client:
            menu = client.get(f"{OFFICIAL_BASE}/ArticleMenu.json").json()
        visual = re.compile(
            r"wallpaper|version preview|profile reveal|resonator reveal|update content|anthropocene",
            re.I,
        )
        skip = re.compile(r"maintenance notice|convene details|faq|fan creation", re.I)
        for item in menu:
            title = str(item.get("articleTitle") or "")
            if skip.search(title) or not visual.search(title):
                continue
            cover = str(item.get("suggestCover") or "")
            if cover.startswith("http") and cover != exclude:
                return cover
    except Exception:
        return None
    return None


def send_discord(text: str, image_url: str | None = None) -> None:
    hook = env("DISCORD_WEBHOOK_URL")
    if not hook:
        raise RuntimeError("нет DISCORD_WEBHOOK_URL")
    payload = {
        "username": "Wuwa News",
        "content": for_discord(text),
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


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Post official news to Discord #новости")
    parser.add_argument("--from-worker", help="JSON ответ Cloudflare /run")
    parser.add_argument("--text", help="отправить готовый текст")
    args = parser.parse_args()
    if args.text:
        send_discord(args.text)
        print("discord sent")
        return 0
    if args.from_worker:
        payload = json.loads(Path(args.from_worker).read_text(encoding="utf-8"))
        article_id, preview = posted_from_worker(payload)
        if not article_id:
            print("в Telegram нового поста нет, Discord пропускаю")
            return 0
        if not preview:
            print(f"posted {article_id}, но текста нет — Discord пропускаю")
            return 0
        image = article_image(article_id) or fallback_art()
        send_discord(preview, image)
        print(f"discord sent {article_id}")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

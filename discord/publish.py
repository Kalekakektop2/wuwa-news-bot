from __future__ import annotations

import argparse
import json
import os
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
        send_discord(preview)
        print(f"discord sent {article_id}")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

API = "https://discord.com/api/v10"

STRUCTURE = [
    {
        "name": "📌 ИНФО",
        "channels": [
            {"name": "приветствие", "type": 0, "topic": "Правила, ссылки на Telegram и GitHub"},
            {"name": "новости", "type": 0, "topic": "Автопосты по вуве. Не спамить."},
            {"name": "правила", "type": 0, "topic": "Коротко: фан-сервер, не Kuro"},
        ],
    },
    {
        "name": "💬 ОБЩЕНИЕ",
        "channels": [
            {"name": "общий", "type": 0, "topic": "Болталка"},
            {"name": "билды", "type": 0, "topic": "Команды, тир, гайды"},
        ],
    },
    {
        "name": "🎮 ИГРА",
        "channels": [
            {"name": "русификатор", "type": 0, "topic": "Установка и баги перевода. .pak сюда не кидать."},
            {"name": "вопросы", "type": 0, "topic": "Вопросы по игре"},
        ],
    },
    {
        "name": "🔊 ГОЛОС",
        "channels": [
            {"name": "Общий", "type": 2},
            {"name": "Кооп", "type": 2},
            {"name": "AFK", "type": 2},
        ],
    },
]

ROLES = [
    {"name": "Админ", "color": 0xE74C3C, "hoist": True, "mentionable": False},
    {"name": "Модер", "color": 0x3498DB, "hoist": True, "mentionable": True},
    {"name": "Игрок", "color": 0x95A5A6, "hoist": False, "mentionable": False},
]

WELCOME = """фан-сервер по Wuthering Waves. К Kuro не относимся.

новости игры — https://t.me/WuwaNewss
русификатор — https://github.com/Kalekakektop2/Wuwa3.5

в текстовые каналы .pak не кидать, только ссылка на GitHub.
в #новости пишет бот, руками лучше не засорять."""


def env(name: str) -> str:
    return os.getenv(name, "").strip()


def client(token: str) -> httpx.Client:
    return httpx.Client(
        timeout=30,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "WuwaRUS-Setup (https://github.com/Kalekakektop2/wuwa-news-bot)",
        },
    )


def api(http: httpx.Client, method: str, path: str, **kwargs) -> dict | list:
    response = http.request(method, f"{API}{path}", **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"Discord {method} {path}: {response.status_code} {response.text[:400]}")
    if not response.content:
        return {}
    return response.json()


def first_guild(http: httpx.Client, guild_id: str) -> str:
    if guild_id:
        return guild_id
    guilds = api(http, "GET", "/users/@me/guilds")
    if not guilds:
        raise RuntimeError("бот ещё не на сервере. Пригласи его и запусти снова.")
    if len(guilds) > 1:
        names = ", ".join(f"{g['name']} ({g['id']})" for g in guilds)
        raise RuntimeError(f"бот на нескольких серверах, укажи DISCORD_GUILD_ID: {names}")
    return str(guilds[0]["id"])


def existing_names(http: httpx.Client, guild_id: str) -> set[str]:
    channels = api(http, "GET", f"/guilds/{guild_id}/channels")
    return {str(item.get("name") or "") for item in channels}


def setup(token: str, guild_id: str) -> None:
    with client(token) as http:
        me = api(http, "GET", "/users/@me")
        print(f"бот: {me.get('username')}#{me.get('discriminator')} ({me.get('id')})")
        guild_id = first_guild(http, guild_id)
        print(f"сервер: {guild_id}")
        have = existing_names(http, guild_id)
        news_id = None
        hello_id = None
        position = 0
        for category in STRUCTURE:
            if category["name"] in have:
                print(f"категория уже есть: {category['name']}")
                cats = api(http, "GET", f"/guilds/{guild_id}/channels")
                parent_id = next(
                    (c["id"] for c in cats if c.get("name") == category["name"] and c.get("type") == 4),
                    None,
                )
            else:
                created = api(
                    http,
                    "POST",
                    f"/guilds/{guild_id}/channels",
                    json={"name": category["name"], "type": 4, "position": position},
                )
                parent_id = created["id"]
                print(f"категория: {category['name']}")
            position += 1
            for channel in category["channels"]:
                if channel["name"] in have:
                    print(f"  уже есть #{channel['name']}")
                    cats = api(http, "GET", f"/guilds/{guild_id}/channels")
                    found = next((c for c in cats if c.get("name") == channel["name"]), None)
                    if found and channel["name"] == "новости":
                        news_id = found["id"]
                    if found and channel["name"] == "приветствие":
                        hello_id = found["id"]
                    continue
                payload = {
                    "name": channel["name"],
                    "type": channel["type"],
                    "parent_id": parent_id,
                    "position": position,
                }
                if channel.get("topic"):
                    payload["topic"] = channel["topic"]
                created = api(http, "POST", f"/guilds/{guild_id}/channels", json=payload)
                print(f"  канал: {channel['name']}")
                if channel["name"] == "новости":
                    news_id = created["id"]
                if channel["name"] == "приветствие":
                    hello_id = created["id"]
                position += 1

        roles = api(http, "GET", f"/guilds/{guild_id}/roles")
        have_roles = {str(role.get("name") or "") for role in roles}
        for role in ROLES:
            if role["name"] in have_roles:
                print(f"роль уже есть: {role['name']}")
                continue
            api(http, "POST", f"/guilds/{guild_id}/roles", json=role)
            print(f"роль: {role['name']}")

        if hello_id:
            api(
                http,
                "POST",
                f"/channels/{hello_id}/messages",
                json={"content": WELCOME},
            )
            print("написал в #приветствие")

        webhook_url = ""
        if news_id:
            hook = api(
                http,
                "POST",
                f"/channels/{news_id}/webhooks",
                json={"name": "Wuwa News"},
            )
            webhook_url = str(hook.get("url") or "")
            print(f"вебхук #новости: {webhook_url}")

        print("готово")
        if webhook_url:
            print("скинь этот вебхук — подключу автопосты")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    token = env("DISCORD_BOT_TOKEN")
    guild = env("DISCORD_GUILD_ID")
    if not token:
        print("нет DISCORD_BOT_TOKEN")
        print("1) https://discord.com/developers/applications → New Application")
        print("2) Bot → Add Bot → Reset Token")
        print("3) Bot → Privileged Gateway Intents можно не включать")
        print("4) OAuth2 → URL Generator: scopes = bot, permissions = Administrator")
        print("5) открой ссылку, выбери сервер")
        print("6) положи токен в discord-bot/.env как DISCORD_BOT_TOKEN=")
        return 1
    setup(token, guild)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

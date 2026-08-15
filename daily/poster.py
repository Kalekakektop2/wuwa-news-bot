from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import httpx
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


def fetch_official_art(client: httpx.Client, used: set[str]) -> tuple[str, str] | None:
    menu = client.get(f"{OFFICIAL_BASE}/ArticleMenu.json?t={int(now_utc8().timestamp())}")
    menu.raise_for_status()
    skip = ("fan creation event winners", "premium model set")
    for item in menu.json():
        title = str(item.get("articleTitle") or "")
        if any(word in title.lower() for word in skip):
            continue
        if not re.search(r"wallpaper|version preview|profile reveal|resonator reveal", title, re.I):
            continue
        cover = str(item.get("suggestCover") or "")
        if cover.startswith("http") and cover not in used:
            return cover, title
    for item in menu.json():
        cover = str(item.get("suggestCover") or "")
        title = str(item.get("articleTitle") or "")
        if cover.startswith("http") and cover not in used and title:
            return cover, title
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

    maint = calendar.get("maintenance") or {}
    if maint.get("start"):
        start = parse_dt(maint["start"])
        if today <= start.date():
            lines.append("")
            if today == start.date():
                lines.append(
                    f"сегодня техработы {maint.get('version', '')}: "
                    f"{start.strftime('%H:%M')}–{parse_dt(maint['end']).strftime('%H:%M')} UTC+8"
                )
            else:
                left = (start.date() - today).days
                lines.append(
                    f"техработы {maint.get('version', '')} — {start.strftime('%d.%m')}, "
                    f"через {ru_days(left)}, окно {start.strftime('%H:%M')} UTC+8"
                )
            if maint.get("compensation"):
                lines.append(f"компенсация: {maint['compensation']}")

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


def build_art_caption(title: str) -> str:
    return f"официальный арт\n{title}\n\nне наш рисунок, просто красиво"


def send_telegram(text: str, image_url: str | None = None) -> None:
    token = env("TELEGRAM_BOT_TOKEN")
    chat = env("TELEGRAM_CHANNEL_ID")
    if not token or not chat:
        raise RuntimeError("нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
    api = f"https://api.telegram.org/bot{token}"
    with httpx.Client(timeout=30) as client:
        if image_url:
            caption = html.escape(text)[:1000]
            data = client.post(
                f"{api}/sendPhoto",
                json={
                    "chat_id": chat,
                    "photo": image_url,
                    "caption": caption,
                },
            ).json()
            if data.get("ok"):
                return
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
        if today.weekday() == 0:
            return {"kind": "calendar", "week": True, "slot": slot}
        if any(item["left"] <= 2 for item in active_banners(calendar, today)):
            return {"kind": "calendar", "week": False, "slot": slot}
        return {"kind": "codes_or_calendar", "slot": slot}

    if slot == "evening":
        if maint_day and today in {maint_day, maint_day + timedelta(days=1)}:
            if last.get("rus_patch") != maint.get("version"):
                return {"kind": "rus", "slot": slot, "version": maint.get("version", "")}
        if today.weekday() == 6:
            return {"kind": "calendar", "week": True, "slot": slot}
        day_index = today.toordinal()
        rotate = ["character", "echo", "art", "character"]
        return {"kind": rotate[day_index % 4], "slot": slot}

    return {"kind": "calendar", "slot": slot}


def run_slot(slot: str, *, dry_run: bool) -> int:
    calendar = load_json(ROOT / "data" / "calendar.json")
    roster = load_json(ROOT / "data" / "roster.json")
    state = State(Path(env("DAILY_STATE") or str(REPO / "state" / "daily.json")))
    plan = decide(slot, calendar, roster, state)
    if plan.get("skip"):
        print(plan["reason"])
        return 0

    text = ""
    image = None
    kind = plan["kind"]
    headers = {"user-agent": "wuwa-daily/1.0"}

    with httpx.Client(timeout=25, headers=headers, follow_redirects=True) as client:
        if kind == "codes_or_calendar":
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
        elif kind == "art":
            art = fetch_official_art(client, set(state.data.get("images") or []))
            if not art:
                card = pick_card(roster, state, "character")
                text = build_card_text(card, "character") if card else build_calendar_text(calendar, now_utc8().date())
                if card:
                    state.data.setdefault("cards", []).append(card["key"])
            else:
                image, title = art
                text = build_art_caption(title)
                state.data.setdefault("images", []).append(image)

    print(f"slot={slot} kind={kind}\n{text}")
    if image:
        print(f"image={image}")
    if dry_run:
        return 0

    send_telegram(text, image)
    state.data.setdefault("last", {})[slot] = now_utc8().date().isoformat()
    state.save()
    print("sent")
    return 0


def infer_slot() -> str:
    hour = datetime.now(timezone.utc).hour
    return "morning" if hour < 12 else "evening"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Daily WuWa channel content")
    parser.add_argument("--slot", choices=["morning", "evening", "auto"], default="auto")
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

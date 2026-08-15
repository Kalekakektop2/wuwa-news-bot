from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TIERS = [
    ("S+", "#d4a017", [
        "Shorekeeper", "Cartethyia", "Phrolova", "Aemeath",
        "Yangyang: Xuanling", "Camellya", "Augusta", "Mornye",
        "Suisui", "Hiyuki", "Denia", "Lynae", "Luuk Herssen",
        "Chisa", "Iuno", "Sigrika",
    ]),
    ("S", "#e07b39", [
        "Jiyan", "Jinhsi", "Zani", "Carlotta", "Lucilla", "Lucy",
        "Phoebe", "Verina", "Lupa", "Sanhua", "Cantarella",
        "Qiuyuan", "Galbrena", "Brant", "Rebecca", "Changli",
        "Ciaccona",
    ]),
    ("A", "#3d9b6e", [
        "Xiangli Yao", "Roccia", "Zhezhi", "Encore",
        "Rover Havoc", "Rover Aero", "Buling", "Baizhi",
    ]),
    ("B", "#3a7ca5", [
        "Calcharo", "Jianxin", "Mortefi", "Rover Electro",
        "Yinlin", "Danjin", "Chixia", "Rover Spectro",
    ]),
    ("C", "#7a7a7a", [
        "Yuanwu", "Lingyang", "Aalto", "Youhu", "Yangyang",
        "Taoqi", "Lumi",
    ]),
    ("D", "#6b5344", []),
    ("E", "#4a4a4a", []),
]


def font(size: int, bold: bool = False):
    candidates = [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    if bold:
        candidates = [
            r"C:\Windows\Fonts\segoeuib.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ] + candidates
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def wrap(draw, text: str, fnt, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return ["—"]
    lines = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=fnt) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render() -> bytes:
    width = 1280
    pad = 36
    title_f = font(42, True)
    sub_f = font(22)
    tier_f = font(28, True)
    name_f = font(24)
    dummy = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(dummy)
    inner = width - pad * 2 - 90
    rows = []
    for letter, color, names in TIERS:
        body = ", ".join(names) if names else "пока никого"
        rows.append((letter, color, wrap(draw, body, name_f, inner)))
    height = 170
    for _, _, lines in rows:
        height += 28 + len(lines) * 32 + 22
    height += 50
    img = Image.new("RGB", (width, height), "#12141a")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width, 8), fill="#d4a017")
    draw.text((pad, 28), "Wuthering Waves — тир-лист", font=title_f, fill="#f4f1ea")
    draw.text((pad, 82), "август 2026  ·  ToA + WhiWa  ·  Wutheringlab", font=sub_f, fill="#b7b3aa")
    draw.text((pad, 112), "не официалка Kuro  ·  Qingxiao / Jingran ещё не в списке", font=sub_f, fill="#8d897f")
    y = 160
    for letter, color, lines in rows:
        box_h = 20 + len(lines) * 32
        draw.rounded_rectangle((pad, y, width - pad, y + box_h), radius=10, fill="#1c2028")
        draw.rounded_rectangle((pad, y, pad + 72, y + box_h), radius=10, fill=color)
        tw = draw.textlength(letter, font=tier_f)
        draw.text((pad + (72 - tw) / 2, y + box_h / 2 - 16), letter, font=tier_f, fill="#111")
        ty = y + 12
        for line in lines:
            draw.text((pad + 90, ty), line, font=name_f, fill="#efeae1")
            ty += 32
        y += box_h + 14
    out = BytesIO()
    img.save(out, format="JPEG", quality=93)
    return out.getvalue()


CAPTION = """тир-лист персонажей вувы, август 2026

взял актуальный лист Wutheringlab от 15.08 — туда уже добавили Suisui.
считают ToA и Whimpering Wastes вместе.
ранги у них SS–C, я сжал в S+ / S / A / B / C. D и E пустые — на этом листе ниже C никого нет.

это мнение гайдеров, не таблица Kuro.
в 3.6 Qingxiao и Jingran ещё не стоят — список перепишут после патча.

источник — https://wutheringlab.com/wuthering-waves-tier-list/"""


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    photo = render()
    (ROOT / "daily" / "_tier.jpg").write_bytes(photo)
    with httpx.Client(timeout=90) as client:
        data = client.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat, "caption": CAPTION[:1000]},
            files={"photo": ("tier.jpg", photo, "image/jpeg")},
        ).json()
    print(data.get("ok"), data if not data.get("ok") else data["result"]["message_id"])
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

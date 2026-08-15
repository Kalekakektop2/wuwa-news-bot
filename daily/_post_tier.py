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
        "Береговой сторож", "Картетия", "Фролова", "Эймит",
        "Янъян: Сюаньлин", "Камелия", "Августа", "Морни",
        "Суйсуй", "Хиюки", "Дения", "Линаи", "Луук Херссен",
        "Чиса", "Юно", "Сигрика",
    ]),
    ("S", "#e07b39", [
        "Цзиянь", "Цзиньси", "Зани", "Карлотта", "Луцилла", "Люси",
        "Фиби", "Верина", "Лупа", "Саньхуа", "Кантарелла",
        "Цююань", "Галбрена", "Брант", "Ребекка", "Чанли",
        "Чаккона",
    ]),
    ("A", "#3d9b6e", [
        "Сянли Яо", "Рочча", "Чжэчжи", "Энкор",
        "Ровер (Хавок)", "Ровер (Аэро)", "Булинь", "Байчжи",
    ]),
    ("B", "#3a7ca5", [
        "Калкаро", "Цзяньсинь", "Мортэфи", "Ровер (Электро)",
        "Иньлинь", "Даньцзинь", "Чися", "Ровер (Спектро)",
    ]),
    ("C", "#7a7a7a", [
        "Юаньу", "Линъян", "Аальто", "Юху", "Янъян",
        "Таоци", "Люми",
    ]),
    ("D", "#6b5344", []),
    ("E", "#4a4a4a", []),
]

ICONS = {
    "Береговой сторож": "https://cdn.prydwen.gg/images/ww/characters/icon_keeper.webp",
    "Картетия": "https://cdn.prydwen.gg/images/ww/characters/cart_icon.webp",
    "Фролова": "https://cdn.prydwen.gg/images/ww/characters/phr_icon.webp",
    "Эймит": "https://cdn.prydwen.gg/images/ww/characters/math_icon.webp",
    "Янъян: Сюаньлин": "https://cdn.prydwen.gg/images/wuthering-waves/characters/yangyang-xuanling_icon.webp",
    "Камелия": "https://cdn.prydwen.gg/images/ww/characters/icon_cam.webp",
    "Августа": "https://cdn.prydwen.gg/images/ww/characters/aug_icon.webp",
    "Морни": "https://cdn.prydwen.gg/images/ww/characters/46_icon.webp",
    "Суйсуй": "https://cdn.prydwen.gg/images/wuthering-waves/characters/suisui_icon.webp",
    "Хиюки": "https://cdn.prydwen.gg/images/ww/characters/hiyuki_sm.webp",
    "Дения": "https://cdn.prydwen.gg/images/ww/characters/denia_sm.webp",
    "Линаи": "https://cdn.prydwen.gg/images/ww/characters/48_icon.webp",
    "Луук Херссен": "https://cdn.prydwen.gg/images/ww/characters/45_icon.webp",
    "Чиса": "https://cdn.prydwen.gg/images/ww/characters/chisa_icon.webp",
    "Юно": "https://cdn.prydwen.gg/images/ww/characters/ioun_icon.webp",
    "Сигрика": "https://cdn.prydwen.gg/images/ww/characters/44_icon.webp",
    "Цзиянь": "https://cdn.prydwen.gg/images/ww/characters/jiyan_icon.webp",
    "Цзиньси": "https://cdn.prydwen.gg/images/ww/characters/jihni_icon.webp",
    "Зани": "https://cdn.prydwen.gg/images/ww/characters/icon_zani.webp",
    "Карлотта": "https://cdn.prydwen.gg/images/ww/characters/icon_carlotta.webp",
    "Луцилла": "https://cdn.prydwen.gg/images/ww/characters/47_icon.webp",
    "Люси": "https://cdn.prydwen.gg/images/wuthering-waves/characters/lucy_icon.webp",
    "Фиби": "https://cdn.prydwen.gg/images/ww/characters/icon_phoebe.webp",
    "Верина": "https://cdn.prydwen.gg/images/ww/characters/verina_icon.webp",
    "Лупа": "https://cdn.prydwen.gg/images/ww/characters/lupa_icon.webp",
    "Саньхуа": "https://cdn.prydwen.gg/images/ww/characters/senhua_icon.webp",
    "Кантарелла": "https://cdn.prydwen.gg/images/ww/characters/icon_canta.webp",
    "Цююань": "https://cdn.prydwen.gg/images/ww/characters/qiu_icon.webp",
    "Галбрена": "https://cdn.prydwen.gg/images/ww/characters/gal_icon.webp",
    "Брант": "https://cdn.prydwen.gg/images/ww/characters/icon_brant.webp",
    "Ребекка": "https://cdn.prydwen.gg/images/wuthering-waves/characters/rebecca_icon.webp",
    "Чанли": "https://cdn.prydwen.gg/images/ww/characters/changli_icon.webp",
    "Чаккона": "https://cdn.prydwen.gg/images/ww/characters/cia_icon.webp",
    "Сянли Яо": "https://cdn.prydwen.gg/images/ww/characters/icon_xiang.webp",
    "Рочча": "https://cdn.prydwen.gg/images/ww/characters/icon_roccia.webp",
    "Чжэчжи": "https://cdn.prydwen.gg/images/ww/characters/icon_zhe.webp",
    "Энкор": "https://cdn.prydwen.gg/images/ww/characters/encore_icon.webp",
    "Ровер (Хавок)": "https://cdn.prydwen.gg/images/ww/characters/rover_icon.webp",
    "Ровер (Аэро)": "https://cdn.prydwen.gg/images/ww/characters/rover_icon.webp",
    "Булинь": "https://cdn.prydwen.gg/images/wuthering-waves/characters/buling_icon.webp",
    "Байчжи": "https://cdn.prydwen.gg/images/ww/characters/baizhi_icon.webp",
    "Калкаро": "https://cdn.prydwen.gg/images/ww/characters/kakarot_icon.webp",
    "Цзяньсинь": "https://cdn.prydwen.gg/images/ww/characters/jianxin_icon.webp",
    "Мортэфи": "https://cdn.prydwen.gg/images/ww/characters/mortefi_icon.webp",
    "Ровер (Электро)": "https://cdn.prydwen.gg/images/ww/characters/rover_icon.webp",
    "Иньлинь": "https://cdn.prydwen.gg/images/ww/characters/yinglin_icon.webp",
    "Даньцзинь": "https://cdn.prydwen.gg/images/ww/characters/danjin_icon.webp",
    "Чися": "https://cdn.prydwen.gg/images/ww/characters/chixia_icon.webp",
    "Ровер (Спектро)": "https://cdn.prydwen.gg/images/ww/characters/rover_icon.webp",
    "Юаньу": "https://cdn.prydwen.gg/images/ww/characters/yuanwu_icon.webp",
    "Линъян": "https://cdn.prydwen.gg/images/ww/characters/ling_icon.webp",
    "Аальто": "https://cdn.prydwen.gg/images/ww/characters/aalto_icon.webp",
    "Юху": "https://cdn.prydwen.gg/images/ww/characters/youhu_icon.webp",
    "Янъян": "https://cdn.prydwen.gg/images/ww/characters/yangyang_icon.webp",
    "Таоци": "https://cdn.prydwen.gg/images/ww/characters/taoqi_icon.webp",
    "Люми": "https://cdn.prydwen.gg/images/ww/characters/lumi_icon.webp",
}


def font(size: int, bold: bool = False):
    candidates = [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    if bold:
        candidates = [
            r"C:\Windows\Fonts\segoeuib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ] + candidates
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def load_icon(session: httpx.Client, name: str, size: int) -> Image.Image:
    url = ICONS.get(name)
    if url:
        try:
            resp = session.get(url, timeout=20, follow_redirects=True)
            if resp.status_code == 200 and resp.content:
                icon = Image.open(BytesIO(resp.content)).convert("RGBA")
                icon = icon.resize((size, size), Image.Resampling.LANCZOS)
                mask = Image.new("L", (size, size), 0)
                ImageDraw.Draw(mask).ellipse((1, 1, size - 2, size - 2), fill=255)
                out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                out.paste(icon, (0, 0), mask)
                return out
        except Exception:
            pass
    img = Image.new("RGBA", (size, size), (40, 44, 52, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((1, 1, size - 2, size - 2), fill=(55, 60, 70, 255))
    letter = name[0]
    fnt = font(int(size * 0.42), True)
    tw = draw.textlength(letter, font=fnt)
    draw.text(((size - tw) / 2, size * 0.22), letter, font=fnt, fill="#efeae1")
    return img


def render() -> bytes:
    width = 1280
    pad = 28
    icon = 56
    gap = 5
    label_w = 78
    header = 120
    row_h = icon + 18
    height = header + row_h * len(TIERS) + pad
    img = Image.new("RGB", (width, height), "#12141a")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width, 8), fill="#d4a017")
    draw.text((pad, 24), "Wuthering Waves — тир-лист", font=font(40, True), fill="#f4f1ea")
    draw.text((pad, 76), "август 2026", font=font(22), fill="#b7b3aa")

    session = httpx.Client(headers={"user-agent": "wuwa-tier/1.0"}, timeout=20)
    y = header
    for letter, color, names in TIERS:
        draw.rounded_rectangle((pad, y, width - pad, y + row_h - 6), radius=12, fill="#1c2028")
        draw.rounded_rectangle((pad, y, pad + label_w, y + row_h - 6), radius=12, fill=color)
        tw = draw.textlength(letter, font=font(26, True))
        draw.text(
            (pad + (label_w - tw) / 2, y + row_h / 2 - 22),
            letter,
            font=font(26, True),
            fill="#111",
        )
        x = pad + label_w + 12
        if not names:
            draw.text((x, y + 22), "—", font=font(26), fill="#8d897f")
        else:
            for name in names:
                if x + icon > width - pad:
                    break
                avatar = load_icon(session, name, icon)
                img.paste(avatar, (int(x), y + 6), avatar)
                x += icon + gap
        y += row_h
    session.close()

    out = BytesIO()
    img.save(out, format="JPEG", quality=93)
    return out.getvalue()


def quoted(names: list[str]) -> str:
    return "никого" if not names else ", ".join(names)


def build_caption() -> str:
    lines = ["тир-лист персонажей вувы, август 2026", ""]
    for letter, _color, names in TIERS:
        lines.append(f'{letter}-"{quoted(names)}"')
    lines.extend(
        [
            "",
            "мета сейчас: Эймит + Дения + Чиса / Суйсуй",
            "запас: Эймит + Линаи + Морни / Береговой сторож",
            "",
            "источник — https://wutheringlab.com/wuthering-waves-tier-list/",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
    photo = render()
    caption = build_caption()
    (ROOT / "daily" / "_tier.jpg").write_bytes(photo)
    with httpx.Client(timeout=90) as client:
        data = client.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat, "caption": caption[:1024]},
            files={"photo": ("tier.jpg", photo, "image/jpeg")},
        ).json()
    print(data.get("ok"), data if not data.get("ok") else data["result"]["message_id"])
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

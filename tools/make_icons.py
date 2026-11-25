#!/usr/bin/env python3
"""PWA 아이콘 생성 (192/512/apple-touch-icon 180).

디자인: 남색 라운드 사각형 + 흰색 버스 앞모습 + 노란색 정류장 번호 25159
실행: python3 builds/bus-stop-25159/tools/make_icons.py
"""
import pathlib

from PIL import Image, ImageDraw, ImageFont

OUT = pathlib.Path(__file__).resolve().parents[1] / "site"
FONT_BOLD = "/usr/share/fonts/pickle-hwp/NotoSansCJKkr-Regular.otf"

NAVY = (23, 47, 110, 255)
NAVY_DEEP = (12, 26, 66, 255)
WHITE = (255, 255, 255, 255)
GLASS = (96, 156, 232, 255)
YELLOW = (255, 205, 40, 255)


def rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def make(size: int, maskable: bool = False) -> Image.Image:
    s = size
    scale = 4  # 슈퍼샘플링
    S = s * scale
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = 0 if maskable else int(S * 0.02)
    radius = int(S * 0.24) if not maskable else int(S * 0.5)
    if maskable:
        d.ellipse([pad, pad, S - pad, S - pad], fill=NAVY)
    else:
        rounded(d, [pad, pad, S - pad, S - pad], radius, NAVY)

    # 버스 앞모습 (중앙 위쪽)
    bw = S * 0.44
    bh = S * 0.36
    bx = (S - bw) / 2
    by = S * 0.165
    rounded(d, [bx, by, bx + bw, by + bh], int(S * 0.075), WHITE)
    # 앞유리
    gx0 = bx + bw * 0.10
    gx1 = bx + bw * 0.90
    gy0 = by + bh * 0.12
    gy1 = by + bh * 0.46
    rounded(d, [gx0, gy0, gx1, gy1], int(S * 0.035), GLASS)
    # 노선 표시판
    plate_w = bw * 0.42
    plate_h = bh * 0.16
    rounded(
        d,
        [bx + (bw - plate_w) / 2, by + bh * 0.56, bx + (bw + plate_w) / 2, by + bh * 0.56 + plate_h],
        int(S * 0.012),
        NAVY_DEEP,
    )
    # 헤드라이트
    lr = S * 0.018
    for cx in (bx + bw * 0.22, bx + bw * 0.78):
        cy = by + bh * 0.80
        d.ellipse([cx - lr, cy - lr, cx + lr, cy + lr], fill=YELLOW)
    # 바퀴
    wr = bw * 0.115
    for cx in (bx + bw * 0.24, bx + bw * 0.76):
        cy = by + bh
        d.ellipse([cx - wr, cy - wr, cx + wr, cy + wr], fill=NAVY_DEEP)

    # 정류장 번호
    text = "25159"
    fsize = int(S * 0.20)
    try:
        font = ImageFont.truetype(FONT_BOLD, fsize)
    except Exception:
        font = ImageFont.load_default()
    tb = d.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    d.text(((S - tw) / 2 - tb[0], S * 0.700 - tb[1]), text, font=font, fill=YELLOW)

    img = img.resize((s, s), Image.LANCZOS)
    if not maskable:
        # 모서리를 투명하게 (그대로 두어도 무방하지만 iOS가 마스킹한다)
        pass
    return img.convert("RGBA")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, maskable in (
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-512-maskable.png", 512, True),
        ("apple-touch-icon.png", 180, False),
    ):
        img = make(size, maskable)
        if name == "apple-touch-icon.png":
            bg = Image.new("RGB", img.size, NAVY[:3])
            bg.paste(img, (0, 0), img)
            img = bg
        img.save(OUT / name)
        print("wrote", OUT / name, img.size)


if __name__ == "__main__":
    main()

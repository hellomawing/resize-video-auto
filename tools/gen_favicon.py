# -*- coding: utf-8 -*-
"""生成网站 favicon / logo 的全套 PNG 与 ICO。

图标设计：被切开的播放按钮 —— 表达「视频无损分割」。
几何结构与 web/public/favicon.svg 保持一致：
  - 圆角底板（蓝紫渐变 #2563EB -> #4F46E5）
  - 播放三角左半（白色）
  - 播放三角右半（浅蓝 #93C5FD，向右上错位，表现"被切开"）
  - 中缝切线（琥珀色 #FBBF24）

用法：
    python tools/gen_favicon.py
输出：
    web/public/favicon-16x16.png
    web/public/favicon-32x32.png
    web/public/apple-touch-icon.png   (180x180，方形不透明底，iOS 专用)
    web/public/favicon-192.png
    web/public/favicon-512.png
    web/public/favicon.ico            (16/32/48)
"""
from PIL import Image, ImageDraw
import os

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "public")

# 设计基准（与 SVG viewBox 0 0 64 64 对应），超采样 16 倍抗锯齿
BASE = 64
SS = 16
SIZE = BASE * SS  # 1024

# 配色
C_BG_FROM = (37, 99, 235)     # #2563EB
C_BG_TO = (79, 70, 229)       # #4F46E5
C_TRIANGLE_MAIN = (255, 255, 255)
C_TRIANGLE_CUT = (147, 197, 253)  # #93C5FD
C_CUT_LINE = (251, 191, 36)   # #FBBF24

# 几何（基准 64 坐标系）
TILE = (2, 2, 62, 62)
TILE_RADIUS = 14
TRI_LEFT = [(20, 18), (38, 26.4), (38, 37.6), (20, 46)]
TRI_RIGHT = [(38, 26.4), (51, 32), (38, 37.6)]
TRI_RIGHT_OFFSET = (5, -3)
CUT_LINE = (39.75, 14, 42.25, 50)  # 位于两半之间 38~43 的中缝


def scale(pts, k):
    return [(x * k, y * k) for x, y in pts]


def make_gradient(size):
    """对角线线性渐变：左上 -> 右下。"""
    img = Image.new("RGB", (size, size))
    px = img.load()
    denom = 2 * (size - 1)
    for y in range(size):
        for x in range(size):
            t = (x + y) / denom
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(C_BG_FROM, C_BG_TO))
    return img


def render(square=False):
    """渲染 logo。square=True 时输出方形不透明底（apple-touch-icon 用）。"""
    k = SIZE / BASE
    bg = make_gradient(SIZE).convert("RGBA")
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    if square:
        canvas.paste(bg, (0, 0))
    else:
        mask = Image.new("L", (SIZE, SIZE), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [TILE[0] * k, TILE[1] * k, TILE[2] * k, TILE[3] * k],
            radius=TILE_RADIUS * k, fill=255,
        )
        canvas.paste(bg, (0, 0), mask)

    draw = ImageDraw.Draw(canvas)
    draw.polygon(scale(TRI_LEFT, k), fill=C_TRIANGLE_MAIN)
    right = [(x + TRI_RIGHT_OFFSET[0], y + TRI_RIGHT_OFFSET[1]) for x, y in TRI_RIGHT]
    draw.polygon(scale(right, k), fill=C_TRIANGLE_CUT)
    draw.rounded_rectangle(
        [CUT_LINE[0] * k, CUT_LINE[1] * k, CUT_LINE[2] * k, CUT_LINE[3] * k],
        radius=1.25 * k, fill=C_CUT_LINE,
    )

    if square:
        return canvas.convert("RGB")
    return canvas


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    big = render(square=False)
    big_sq = render(square=True)

    for size, name in [(16, "favicon-16x16.png"), (32, "favicon-32x32.png"),
                       (192, "favicon-192.png"), (512, "favicon-512.png")]:
        big.resize((size, size), Image.LANCZOS).save(os.path.join(OUT_DIR, name))
        print("written:", name)

    big_sq.resize((180, 180), Image.LANCZOS).save(os.path.join(OUT_DIR, "apple-touch-icon.png"))
    print("written: apple-touch-icon.png")

    big.save(os.path.join(OUT_DIR, "favicon.ico"),
             sizes=[(16, 16), (32, 32), (48, 48)])
    print("written: favicon.ico")


if __name__ == "__main__":
    main()

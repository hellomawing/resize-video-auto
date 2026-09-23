# -*- coding: utf-8 -*-
"""生成网站 favicon / 图标的全套 PNG 与 ICO。

图标唯一源图是 web/public/logo.png（透明底 PNG，胶片被剪刀切开的主题）。
本脚本把它裁剪到内容包围盒、补成方形，再缩出各尺寸：

    web/public/favicon-16x16.png
    web/public/favicon-32x32.png
    web/public/apple-touch-icon.png   (180x180，白底不透明，iOS 专用)
    web/public/favicon-192.png
    web/public/favicon-512.png
    web/public/favicon.ico            (16/32/48)

用法：
    python tools/gen_favicon.py

换 logo 时只替换 web/public/logo.png 后重跑本脚本。
注意：PNG 没法生成矢量 favicon.svg，站点 favicon 全走 PNG/ICO，
favicon.svg 已随旧版手绘图标一并移除。
"""
from PIL import Image
import os

PUB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "public")
SOURCE = os.path.join(PUB_DIR, "logo.png")

# 内容外围留白（占内容边长的比例），太小会让图标贴着圆角/边缘
MARGIN_RATIO = 0.06
SS = 1024  # 中间渲染尺寸，向下缩用 LANCZOS 抗锯齿


def load_square() -> Image.Image:
    """裁掉透明边、补成正方形（内容居中），返回 RGBA 大图。"""
    im = Image.open(SOURCE).convert("RGBA")
    bbox = im.getchannel("A").getbbox()
    if bbox:
        im = im.crop(bbox)
    w, h = im.size
    side = max(w, h)
    margin = round(side * MARGIN_RATIO)
    canvas_side = side + margin * 2
    canvas = Image.new("RGBA", (canvas_side, canvas_side), (0, 0, 0, 0))
    canvas.paste(im, ((canvas_side - w) // 2, (canvas_side - h) // 2))
    return canvas


def main() -> None:
    big = load_square().resize((SS, SS), Image.LANCZOS)

    for size, name in [(16, "favicon-16x16.png"), (32, "favicon-32x32.png"),
                       (192, "favicon-192.png"), (512, "favicon-512.png")]:
        big.resize((size, size), Image.LANCZOS).save(os.path.join(PUB_DIR, name))
        print("written:", name)

    # iOS 桌面图标：系统会自己加圆角，且不会保留透明度背后的样式，
    # 透明底会被衬成黑色 —— 铺一层白底
    white = Image.new("RGBA", (SS, SS), (255, 255, 255, 255))
    white.alpha_composite(big)
    white.convert("RGB").resize((180, 180), Image.LANCZOS).save(
        os.path.join(PUB_DIR, "apple-touch-icon.png"))
    print("written: apple-touch-icon.png")

    big.save(os.path.join(PUB_DIR, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])
    print("written: favicon.ico")


if __name__ == "__main__":
    main()

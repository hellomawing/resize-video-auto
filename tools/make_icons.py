#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/make_icons.py —— 生成飞牛 fpk 应用图标

应用包的图标要跟网页 favicon / 导航 logo 保持一致，所以这里也从
web/public/logo.png（唯一源图）生成，复用 gen_favicon 的「裁内容包围盒 +
补方形 + 超采样 LANCZOS 缩放下采样」逻辑，保证四类图标视觉同源。

用法：
    python tools/make_icons.py

产出（4 个文件，全部来自 logo.png）：
    fnos/ICON.PNG                  64x64   应用包小图标
    fnos/ICON_256.PNG              256x256 应用包大图标
    fnos/app/ui/images/icon_64.png  64x64   桌面图标（小，下划线命名）
    fnos/app/ui/images/icon_256.png 256x256 桌面图标（大）

    （桌面图标用下划线：飞牛约定 app/ui/config 里写 images/icon_{0}.png，
      {0} 会被替换成尺寸，所以要跟上面对应的文件名保持一致）

依赖 Pillow（与 tools/gen_favicon.py 相同）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from gen_favicon import SOURCE, load_square

PROJECT_DIR = Path(__file__).resolve().parent.parent
SS = 512  # 中间渲染尺寸，向下缩用 LANCZOS 抗锯齿


def main() -> None:
    big = load_square().resize((SS, SS), Image.LANCZOS)

    targets = [
        (PROJECT_DIR / "fnos" / "ICON.PNG", 64),
        (PROJECT_DIR / "fnos" / "ICON_256.PNG", 256),
        (PROJECT_DIR / "fnos" / "app" / "ui" / "images" / "icon_64.png", 64),
        (PROJECT_DIR / "fnos" / "app" / "ui" / "images" / "icon_256.png", 256),
    ]
    for path, size in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        big.resize((size, size), Image.LANCZOS).save(path)
        print("已生成 %s（%dx%d，源 %s）"
              % (path.relative_to(PROJECT_DIR), size, size, SOURCE))


if __name__ == "__main__":
    main()
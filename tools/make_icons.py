#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/make_icons.py —— 生成应用图标

用纯标准库手写 PNG（zlib + struct），不引入 Pillow：
这个项目从引擎到部署都尽量零第三方依赖，没必要为了两个图标破例。

图案含义：左边一整块 = 原始视频，右边上下两块 = 切分后的片段，
中间的空隙就是那道「切口」。

用法：
    python tools/make_icons.py

产出（4 个文件）：
    fnos/ICON.PNG                  64x64   应用包小图标
    fnos/ICON_256.PNG              256x256 应用包大图标
    fnos/app/ui/images/icon-64.png  64x64   桌面图标（小）
    fnos/app/ui/images/icon-256.png 256x256 桌面图标（大）
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

BG = (24, 95, 165)          # #185FA5
FG = (255, 255, 255)

# 每个输出像素取 4x4 个采样点做抗锯齿，边缘不会有锯齿感
SUPERSAMPLE = 4


def _rounded_rect_coverage(px: float, py: float, rect, radius: float) -> bool:
    """点是否落在圆角矩形内（用符号距离函数判断）。"""
    x0, y0, x1, y1 = rect
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    hw = (x1 - x0) / 2.0 - radius
    hh = (y1 - y0) / 2.0 - radius
    dx = abs(px - cx) - hw
    dy = abs(py - cy) - hh
    ax, ay = max(dx, 0.0), max(dy, 0.0)
    return (ax * ax + ay * ay) ** 0.5 + min(max(dx, dy), 0.0) - radius <= 0.0


def render_rgba(size: int) -> list:
    """渲染成 RGBA 行数据。"""
    n = float(size)

    # 归一化坐标下的图形定义
    bg_rect = (0.0, 0.0, n, n)
    bg_radius = n * 0.22
    left_rect = (n * 0.17, n * 0.25, n * 0.455, n * 0.75)
    left_radius = n * 0.05
    right_top = (n * 0.545, n * 0.25, n * 0.83, n * 0.475)
    right_bottom = (n * 0.545, n * 0.525, n * 0.83, n * 0.75)
    right_radius = n * 0.04

    rows = []
    step = 1.0 / SUPERSAMPLE
    total = SUPERSAMPLE * SUPERSAMPLE

    for y in range(size):
        row = bytearray()
        for x in range(size):
            bg_hits = 0
            fg_hits = 0
            for sy in range(SUPERSAMPLE):
                py = y + (sy + 0.5) * step
                for sx in range(SUPERSAMPLE):
                    px = x + (sx + 0.5) * step
                    if not _rounded_rect_coverage(px, py, bg_rect, bg_radius):
                        continue
                    bg_hits += 1
                    if (_rounded_rect_coverage(px, py, left_rect, left_radius)
                            or _rounded_rect_coverage(px, py, right_top, right_radius)
                            or _rounded_rect_coverage(px, py, right_bottom, right_radius)):
                        fg_hits += 1
            if bg_hits == 0:
                row += bytes((0, 0, 0, 0))
                continue
            alpha = int(round(255 * bg_hits / total))
            ratio = fg_hits / bg_hits
            color = tuple(int(round(BG[i] + (FG[i] - BG[i]) * ratio)) for i in range(3))
            row += bytes((color[0], color[1], color[2], alpha))
        rows.append(bytes(row))
    return rows


def write_png(path: Path, size: int, rows: list) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    payload = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", header)
               + chunk(b"IDAT", zlib.compress(raw, 9))
               + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def main() -> None:
    targets = [
        (PROJECT_DIR / "fnos" / "ICON.PNG", 64),
        (PROJECT_DIR / "fnos" / "ICON_256.PNG", 256),
        (PROJECT_DIR / "fnos" / "app" / "ui" / "images" / "icon-64.png", 64),
        (PROJECT_DIR / "fnos" / "app" / "ui" / "images" / "icon-256.png", 256),
    ]
    cache: dict[int, list] = {}
    for path, size in targets:
        if size not in cache:
            cache[size] = render_rgba(size)
        write_png(path, size, cache[size])
        print("已生成 %s（%dx%d，%d 字节）"
              % (path.relative_to(PROJECT_DIR), size, size, path.stat().st_size))


if __name__ == "__main__":
    main()

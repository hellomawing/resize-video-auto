#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 fnos/ 打成飞牛应用中心能安装的 fpk 包。

用法：
    python tools/build_fpk.py                  # 打包，产物留在 fnos/video-splitter.fpk
    python tools/build_fpk.py --check          # 只做前置校验，不打包
    python tools/build_fpk.py --out dist/      # 产物挪到 dist/ 目录
    python tools/build_fpk.py --name video-splitter-1.0.0.fpk
    python tools/build_fpk.py --skip-check     # 跳过校验直接打包

⚠️ 先搞清楚 fpk 里装的是什么，否则会掉进「重装了还是旧版本」的坑：

    fpk **不带任何应用源码，也没有镜像层**。它只是个壳子，里面只有：
        · 编排文件 app/docker/docker-compose.yaml —— 写死了 `image: <仓库>:<tag>`
        · 生命周期钩子 cmd/* —— 安装 / 升级 / 卸载 / 配置回调
        · 安装向导 wizard/*、桌面入口 app/ui/config、图标 ICON*.PNG
    真正跑起来的代码，来自编排文件里那个 **镜像 tag**。

    所以：**只要那个 tag 指向的镜像没换，重装多少次 fpk 都还是旧版本。**
    2026-09-26 就踩过——fpk 装完把刚部署好的新镜像盖回 09-23 的旧 tag，
    线上退回 emoji 版导航。
    换镜像只有两条路：① 重建并重打同一个 tag；② 改编排文件里这一行再打包。

前置校验（--skip-check 可跳过）：
    1. fnpack.exe 存在（可用环境变量 FNPACK_BIN 覆盖路径）
    2. manifest / 图标 / wizard / cmd / app/ui / config 必需文件齐全
    3. JSON 配置能被 json 解析、manifest 关键字段都有值
    4. 要在 NAS 上执行的脚本是 LF 换行——带 CR 会在飞牛上报
       "/bin/sh^M: bad interpreter"（.gitattributes 已固定，这里只做兜底检查）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FNOS = ROOT / "fnos"
MANIFEST = FNOS / "manifest"
COMPOSE = FNOS / "app" / "docker" / "docker-compose.yaml"

# fnpack.exe：默认在 fnpack 自己的临时目录里，可用 FNPACK_BIN 覆盖
FNPACK_BIN = os.environ.get(
    "FNPACK_BIN", r"C:/Users/hello/AppData/Local/Temp/fnpack/fnpack.exe")

# 打包必需的条目：缺一个，fnpack 要么报错，要么出一个装不上的包
REQUIRED = (
    "manifest",
    "ICON.PNG",
    "ICON_256.PNG",
    "config/privilege",
    "config/resource",
    "app/ui/config",
    "app/ui/images/icon_64.png",
    "app/ui/images/icon_256.png",
    "app/docker/docker-compose.yaml",
    "wizard/install",
    "wizard/uninstall",
    "cmd/main",
    "cmd/install_init",
    "cmd/install_callback",
    "cmd/upgrade_init",
    "cmd/upgrade_callback",
    "cmd/uninstall_init",
    "cmd/uninstall_callback",
    "cmd/config_init",
    "cmd/config_callback",
)

# 必须能被 json 解析的配置
JSON_FILES = ("config/privilege", "config/resource", "app/ui/config",
              "wizard/install", "wizard/uninstall")

# 要在 NAS / 容器里执行或读取的文本文件，必须 LF
LF_FILES = ("manifest", "app/ui/config")
LF_DIRS = ("cmd", "wizard")

# manifest 里必须有值的字段
MANIFEST_KEYS = ("appname", "version", "display_name", "service_port",
                 "desktop_applaunchname", "platform")


def log(msg=""):
    print(msg, flush=True)


def find_fnpack():
    """返回 fnpack 路径；找不到返回 None。"""
    p = Path(FNPACK_BIN)
    return p if p.is_file() else None


def read_manifest() -> dict:
    """读 fnos/manifest（朴素 KEY=VALUE 格式）成字典。"""
    data = {}
    if not MANIFEST.is_file():
        return data
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        data[key.strip()] = val.strip()
    return data


def compose_image() -> str:
    """取编排文件里的 image 值 —— 这才是 fpk 装完真正会跑的东西。"""
    if not COMPOSE.is_file():
        return ""
    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("image:"):
            return s.split(":", 1)[1].strip()
    return ""


def preflight(verbose: bool = True) -> list:
    """打包前的静态校验，返回问题列表；空列表 = 可以打包。"""
    problems = []

    if find_fnpack() is None:
        problems.append(
            "找不到 fnpack：%s\n     装好它，或用环境变量 FNPACK_BIN 指定路径。"
            % FNPACK_BIN)

    for rel in REQUIRED:
        if not (FNOS / rel).is_file():
            problems.append("缺文件：fnos/%s" % rel)

    for rel in JSON_FILES:
        p = FNOS / rel
        if not p.is_file():
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            problems.append("JSON 语法错误：fnos/%s —— %s" % (rel, exc))

    # 带 CR 的脚本到了 NAS 上会报 "/bin/sh^M: bad interpreter"
    bad_cr = []
    for rel in LF_FILES:
        p = FNOS / rel
        if p.is_file() and b"\r" in p.read_bytes():
            bad_cr.append("fnos/%s" % rel)
    for d in LF_DIRS:
        for p in sorted((FNOS / d).glob("*")):
            if p.is_file() and b"\r" in p.read_bytes():
                bad_cr.append("fnos/%s/%s" % (d, p.name))
    if bad_cr:
        problems.append(
            "这些文件带 CR，应为 LF：%s\n"
            "     修法：git add --renormalize . 后重新检出，或手动转成 LF。"
            % "、".join(bad_cr))

    mf = read_manifest()
    for key in MANIFEST_KEYS:
        if not mf.get(key):
            problems.append("manifest 缺字段或为空：%s" % key)

    img = compose_image()
    if not img:
        problems.append("编排文件里没写 image：fnos/app/docker/docker-compose.yaml")
    elif img.startswith("yourname/"):
        problems.append(
            "编排文件的镜像还是 yourname/... 占位（%s）；cmd/install_init 会直接"
            "拦下安装。改成自己的镜像地址再打包。" % img)

    if verbose:
        log("  manifest ：%s %s" % (mf.get("appname", "?"),
                                    mf.get("version", "?")))
        log("  镜像     ：%s" % (img or "(未读到)"))
        log("  服务端口 ：%s" % mf.get("service_port", "?"))
        log("  必需文件 ：%d 项" % len(REQUIRED))
    return problems


def hint_image():
    """把「fpk 只是壳、版本由镜像 tag 决定」这件事在打包时说出来。"""
    img = compose_image()
    log("\n提醒：fpk 不含源码，跑起来的是编排文件里的镜像 tag ——")
    log("      image: %s" % (img or "(未读到)"))
    log("      这个 tag 指向的镜像不换，重装多少次 fpk 都还是旧版本。")
    log("      要换：重建并重打同一个 tag，或改这一行后重新打包。")


def build(directory: Path = FNOS, logfn=None) -> Path:
    """调 fnpack 打包，成功返回 fpk 路径，失败返回 None。

    做成函数是为了让 tools/release.py 复用同一份逻辑（那边是菜单 2），
    免得两处打包实现各自漂移。
    """
    say = logfn or log
    fnpack = find_fnpack()
    if fnpack is None:
        say("!! 找不到 fnpack：%s" % FNPACK_BIN)
        say("   请先安装，或用环境变量 FNPACK_BIN 指定路径。")
        return None

    app = read_manifest().get("appname") or "app"
    # 记下打包前的产物时间，万一 fnpack 改了命名规则还能兜住
    before = {p: p.stat().st_mtime for p in directory.glob("*.fpk")}

    say("$ %s build -d ." % fnpack)
    proc = subprocess.run([str(fnpack), "build", "-d", "."],
                          cwd=str(directory), text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        say("!! fnpack 返回 %d，打包失败。" % proc.returncode)
        return None

    fpk = directory / ("%s.fpk" % app)
    if not fpk.is_file():
        cands = [p for p in directory.glob("*.fpk")
                 if before.get(p) != p.stat().st_mtime]
        if not cands:
            say("!! 打包命令成功，但没找到 fpk 产物。")
            return None
        fpk = max(cands, key=lambda p: p.stat().st_mtime)
    return fpk


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把 fnos/ 打成飞牛应用中心能安装的 fpk 包",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="注意：fpk 不含源码，装完跑的是编排文件里的镜像 tag。")
    ap.add_argument("-o", "--out", default=None, metavar="DIR",
                    help="产物输出目录，默认留在 fnos/ 里")
    ap.add_argument("-n", "--name", default=None, metavar="FILENAME",
                    help="产物文件名，默认 <appname>.fpk")
    ap.add_argument("--check", action="store_true",
                    help="只做前置校验并打印镜像信息，不打包")
    ap.add_argument("--skip-check", action="store_true",
                    help="跳过前置校验，直接打包")
    args = ap.parse_args()

    mf = read_manifest()
    log("=" * 58)
    log("打包 fpk：%s %s" % (mf.get("display_name", "?"),
                            mf.get("version", "")))
    log("=" * 58)

    if not args.skip_check:
        log("\n[1/2] 前置校验")
        problems = preflight()
        if problems:
            for p in problems:
                log("  ✘ " + p)
            log("\n校验未通过，已中止。修好上面的问题，或用 --skip-check 强行打包。")
            return 1
        log("  ✔ 全部通过")
    else:
        log("\n[1/2] 已跳过前置校验")

    hint_image()

    if args.check:
        log("\n--check：只校验，未打包。")
        return 0

    log("\n[2/2] fnpack 打包")
    fpk = build()
    if fpk is None:
        return 1
    log("  ✔ 产物：%s（%.0f KB）" % (fpk, fpk.stat().st_size / 1024))

    # 需要改名或挪位置时再动，默认原地不动
    if args.out or args.name:
        out_dir = Path(args.out) if args.out else fpk.parent
        if not out_dir.is_absolute():
            out_dir = ROOT / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / (args.name or fpk.name)
        if target.resolve() != fpk.resolve():
            shutil.move(str(fpk), str(target))
            log("  ✔ 已移动到：%s" % target)

    log("\n装到 NAS 上（在 NAS 本机执行）：")
    log("    appcenter-cli install-fpk %s" % (args.name or Path(fpk.name).name))
    log("  或走 tools/release.py 的菜单 2（会自动上传并安装）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

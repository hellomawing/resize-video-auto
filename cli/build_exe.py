#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_exe.py —— 把 cli 下的脚本打包成「免安装 Python」的可执行文件

打包出来的东西：
    video_splitter(.exe)   视频无损分割工具（交互向导 + 命令行参数）
    undo_split(.exe)       撤销分割（把切片删掉、原片改回原名）

目标机器上**不需要装 Python**，双击即可运行；与本目录下的 .py 是同一份代码，
行为完全一致（打包只是把解释器和标准库一起塞进去）。

为什么要分平台各打一次：
    PyInstaller 不支持交叉编译 —— Windows 的 .exe 只能在 Windows 上打，
    macOS 的二进制只能在 macOS 上打（且要区分 Intel / Apple Silicon）。
    所以这个脚本要在每个目标平台上各跑一遍。

用法：
    python cli/build_exe.py                  # 打包全部（默认 onefile 单文件）
    python cli/build_exe.py --target splitter
    python cli/build_exe.py --onedir         # 出目录而不是单文件（启动更快）
    python cli/build_exe.py --icon cli/icon.ico
    python cli/build_exe.py --clean          # 先清掉上次的构建产物

onefile 还是 onedir？
    onefile 只有一个文件，拷来拷去最省事，但**每次运行都要先把自己解压到临时
    目录，退出时再删掉**。在开了实时防护的 Windows 上，删几百个小文件要好几秒
    甚至几十秒，表现为「输出打完了、窗口却卡住一会儿才关」。

    onedir 没有这个开销（解压在构建时就做完了），启动基本是瞬时的，代价是产物
    变成一个目录、要整个拷走。介意启动速度就用 --onedir。
    另外自定义 hook 已经把 Tcl/Tk 的数据文件从 900 多个裁到 170 个左右，
    onefile 的解压/清理开销因此小了约 4 倍。

产物落在 cli/dist/，构建中间文件在 cli/build/（两者都已被 .gitignore 忽略）。
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST_DIR = HERE / "dist"
BUILD_DIR = HERE / "build"

# 自定义 hook 目录。目前只有一个 hook-_tkinter.py：把 Tcl/Tk 里用不上的
# 数据文件（时区库、多语言消息、示例程序）裁掉，详见该文件里的说明。
HOOKS_DIR = HERE / "pyinstaller-hooks"

IS_WINDOWS = os.name == "nt"
IS_MAC = platform.system() == "Darwin"

# 要打包的入口脚本：目标名 -> 源文件
TARGETS = {
    "splitter": ("video_splitter", HERE / "video_splitter.py"),
    "undo": ("undo_split", HERE / "undo_split.py"),
}

# 明确排除用不到的重型第三方库。这个脚本只用标准库，但用户环境里往往装了
# numpy / torch 之类；不排掉的话 PyInstaller 会把它们一起收进来，产物能从
# 十几 MB 涨到几百 MB。排掉之后如果哪天真用上了，删掉对应那一行即可。
EXCLUDES = (
    "numpy", "pandas", "matplotlib", "scipy", "PIL", "cv2",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "IPython", "notebook", "jupyter", "nbformat",
    "pytest", "sqlalchemy", "lxml", "bs4",
)

# 构建期依赖，不进 requirements.txt（运行服务不需要它）
BUILD_REQUIREMENTS = HERE / "build-requirements.txt"


def log(msg: str = "") -> None:
    print(msg, flush=True)


def human_size(num: float) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < step or unit == "GB":
            return "%.1f %s" % (num, unit)
        num /= step
    return "%.1f GB" % num


def ensure_pyinstaller(auto_install: bool = True) -> bool:
    """确保当前解释器里能用 PyInstaller。"""
    try:
        import PyInstaller                                     # noqa: F401
        return True
    except ImportError:
        pass

    log("未检测到 PyInstaller（打包用的工具，不装它没法生成可执行文件）。")
    if not auto_install:
        log("请先执行：%s -m pip install -r %s" % (sys.executable, BUILD_REQUIREMENTS))
        return False

    log("现在安装到当前解释器：%s" % sys.executable)
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
           "-r", str(BUILD_REQUIREMENTS)]
    log("  " + " ".join(cmd))
    try:
        if subprocess.run(cmd).returncode != 0:
            log("安装失败。可以手动执行上面这条命令后重试。")
            return False
    except OSError as exc:
        log("安装失败：%s" % exc)
        return False

    try:
        import PyInstaller                                     # noqa: F401
        return True
    except ImportError:
        log("装完了仍然导入不到 PyInstaller，请检查上面的输出。")
        return False


def pick_icon(explicit: str = None) -> Path | None:
    """挑一个图标文件：优先命令行指定，其次脚本目录下按平台约定的名字。"""
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_file():
            log("警告：图标文件不存在，已忽略：%s" % p)
            return None
        return p
    for name in (("icon.ico",) if IS_WINDOWS else ("icon.icns", "icon.ico")):
        cand = HERE / name
        if cand.is_file():
            return cand
    return None


def build_one(name: str, script: Path, onefile: bool, icon: Path | None,
              fresh: bool = False) -> Path | None:
    """打包一个脚本，成功返回产物路径。

    fresh=True 时把 --clean 传给 PyInstaller：它会清掉全局缓存与上次的中间
    产物，**换了解释器（比如为了补上 tkinter）之后必须用它**，否则可能复用
    旧的依赖分析结果。代价是慢不少，所以默认不开。
    """
    if not script.is_file():
        log("找不到源文件：%s" % script)
        return None

    cmd = [sys.executable, "-m", "PyInstaller",
           "--noconfirm",
           "--console",                    # 交互式向导要控制台，绝不能 --windowed
           "--onefile" if onefile else "--onedir",
           "--name", name,
           "--distpath", str(DIST_DIR),
           "--workpath", str(BUILD_DIR / name),
           "--specpath", str(BUILD_DIR),
           "--noupx",                      # UPX 压缩常被杀软误报，得不偿失
           ]
    if fresh:
        cmd.append("--clean")
    if HOOKS_DIR.is_dir():
        # 同名 hook 会覆盖 PyInstaller 内置的那份
        cmd += ["--additional-hooks-dir", str(HOOKS_DIR)]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    if icon is not None:
        cmd += ["--icon", str(icon)]
    cmd.append(str(script))

    log("")
    log("-" * 68)
    log("打包 %s  <-  %s" % (name, script.name))
    log("-" * 68)

    started = time.time()
    try:
        code = subprocess.run(cmd).returncode
    except OSError as exc:
        log("调用 PyInstaller 失败：%s" % exc)
        return None

    if code != 0:
        log("打包失败（PyInstaller 退出码 %d）。完整报错见上面的输出。" % code)
        return None

    exe_name = name + (".exe" if IS_WINDOWS else "")
    out = DIST_DIR / (exe_name if onefile else os.path.join(name, exe_name))
    if not out.is_file():
        log("PyInstaller 返回成功，但没找到产物：%s" % out)
        return None

    log("完成：%s（%s，用时 %.1f 秒）"
        % (out, human_size(out.stat().st_size), time.time() - started))
    return out


def adhoc_sign(binary: Path) -> None:
    """macOS：给产物做一个临时签名，否则双击时会被 Gatekeeper 直接拦下。

    这是**本地自用**的签名（--sign -），不解决「分发给别人时提示未验证开发者」
    的问题 —— 那个需要 Apple 开发者账号做正式签名与公证。
    """
    if not IS_MAC:
        return
    if not shutil.which("codesign"):
        log("（未找到 codesign，跳过签名；若双击被拦，见下方提示）")
        return
    try:
        r = subprocess.run(["codesign", "--force", "--sign", "-", str(binary)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            log("已做临时签名（ad-hoc），本机双击可直接运行。")
        else:
            log("临时签名失败（不影响从终端运行）：%s"
                % " ".join((r.stderr or "").split())[:200])
    except Exception as exc:                                   # noqa: BLE001
        log("临时签名失败（不影响从终端运行）：%s" % exc)


def check_tkinter() -> bool:
    """打包前确认当前解释器带 tkinter。

    向导第 1 步「直接回车 -> 弹出图形化选择框」靠的就是它。少了不会崩
    （脚本会退回「手动粘贴路径」），但用户会觉得按钮坏了 —— 而且打包时
    完全静默，事后再查很难想到是这个原因，所以这里必须提前喊一声。
    注意：很多精简版 / 托管版 Python 是不带 tkinter 的。
    """
    try:
        import tkinter                                          # noqa: F401
        return True
    except ImportError:
        log("")
        log("!" * 68)
        log("警告：当前 Python 没有 tkinter。")
        log("      打出来的可执行文件将**不带图形化文件夹选择框** ——")
        log("      向导第 1 步直接回车时弹不出窗口，只能手动粘贴路径。")
        log("")
        log("      想带上它，请换一个自带 tkinter 的 Python 重新打包：")
        log("        Windows：python.org 官方安装包默认带（安装时保留 tcl/tk）")
        log("        macOS  ：brew install python-tk")
        log("!" * 68)
        return False


def clean_outputs() -> None:
    for path in (BUILD_DIR, DIST_DIR):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            log("已删除 %s" % path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_exe.py",
        description="把 cli 下的脚本打包成免安装 Python 的可执行文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python cli/build_exe.py                     # 打包全部，单文件模式
  python cli/build_exe.py --onedir            # 目录模式，启动更快
  python cli/build_exe.py --target undo       # 只打包撤销工具
  python cli/build_exe.py --no-install-deps   # 不自动安装 PyInstaller
  python cli/build_exe.py --clean             # 先清掉上次的产物
""")
    parser.add_argument("--target", choices=sorted(TARGETS) + ["all"], default="all",
                        help="要打包哪个（默认 all）")
    parser.add_argument("--onedir", action="store_true",
                        help="打成目录而不是单文件。启动更快，但要整个目录一起拷走")
    parser.add_argument("--icon", default=None,
                        help="图标文件（Windows 用 .ico，macOS 用 .icns）")
    parser.add_argument("--clean", action="store_true",
                        help="彻底重来：清掉 build/ 与 dist/，并让 PyInstaller "
                             "丢掉缓存重新分析依赖。换了解释器（比如为了补上 "
                             "tkinter）之后要用它；平时不必，增量构建快得多")
    parser.add_argument("--no-install-deps", action="store_true",
                        help="PyInstaller 缺失时不自动安装，只提示")
    args = parser.parse_args(argv)

    log("=" * 68)
    log("视频无损分割工具 · 打包")
    log("=" * 68)
    log("平台       ：%s %s（%s）"
        % (platform.system(), platform.release(), platform.machine()))
    log("Python     ：%s" % sys.version.split()[0])
    log("解释器     ：%s" % sys.executable)
    log("输出目录   ：%s" % DIST_DIR)
    if not IS_WINDOWS and not IS_MAC:
        log("")
        log("提示：当前是 Linux。打出来的二进制只能在同类 Linux 上跑，")
        log("      NAS 用户建议直接用 Docker 镜像，不必走这条路。")

    if args.clean:
        clean_outputs()

    if not ensure_pyinstaller(auto_install=not args.no_install_deps):
        return 1

    has_tk = check_tkinter()

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    icon = pick_icon(args.icon)
    if icon:
        log("图标       ：%s" % icon)

    wanted = sorted(TARGETS) if args.target == "all" else [args.target]
    built = []
    for key in wanted:
        name, script = TARGETS[key]
        out = build_one(name, script, not args.onedir, icon, fresh=args.clean)
        if out is None:
            return 1
        built.append(out)

    for out in built:
        adhoc_sign(out)

    log("")
    log("=" * 68)
    log("打包完成，共 %d 个产物：" % len(built))
    for out in built:
        log("  %s  （%s）" % (out, human_size(out.stat().st_size)))
    log("=" * 68)
    log("怎么用：")
    log("  * 把这个文件单独拷到任何一台同系统的电脑上，双击即可运行；")
    log("    目标机器**不需要装 Python**。")
    log("  * 也可以把文件夹拖到它图标上直接处理，等价于命令行给出路径。")
    log("  * 它会自动找 ffmpeg；找不到时向导里会问要不要自动下载安装，")
    log("    装好的 ffmpeg 就放在这个可执行文件旁边的 ffmpeg/ 目录里。")
    log("  * 运行日志写在可执行文件旁边：video_splitter_log.txt。")
    if not has_tk:
        log("")
        log("提醒：本次打包的解释器没有 tkinter，产物里没有图形化文件夹选择框；")
        log("      向导第 1 步只能手动粘贴路径。要补上请换带 tkinter 的 Python 重打。")
    if IS_MAC:
        log("")
        log("macOS 注意：")
        log("  * 从网上下载/拷进来后若双击没反应，先解除隔离属性：")
        log("      xattr -dr com.apple.quarantine %s" % DIST_DIR)
        log("  * 要分发给别人，需要 Apple 开发者账号做正式签名与公证，")
        log("    本地临时签名解决不了「未验证开发者」那个提示。")
        log("  * 换到 Apple Silicon / Intel 的另一类机器上要重新打包。")
    if args.onedir:
        log("")
        log("注意：这次是 --onedir 目录模式，分发时要把整个目录一起拷走，")
        log("      只拷里面的可执行文件会因为找不到依赖而启动失败。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("\n已中断。")
        sys.exit(130)

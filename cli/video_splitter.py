#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video_splitter.py —— 大视频无损分割工具（Windows / macOS / Linux 通用）

作用：
    扫描指定文件夹中的视频文件，把体积超过阈值的文件直接切成若干段，
    全程不重新编码（画质、音质零损失），并尽量保留原文件的
    创建时间 / 修改时间 / 位置信息 / 相机信息等元数据。

两种使用方式：
    交互模式：不带任何路径直接运行（或加 -i），会逐项询问文件夹、切分方式、
              处理哪些文件、过滤规则、输出位置、原文件处理方式等，共 8 步，
              每步回车即用推荐值。覆盖范围与网页版「设置 + 监控目录」一致。
    命令行模式：直接给出路径和参数，适合写进脚本或批处理重复执行。

两种定段方式（决定「每片切多长」）：
    按大小（默认，-s/--size）：把整条时间轴均分成 ceil(文件大小/阈值) 段，
            每段体积尽量接近、且都不超过阈值。适合「每片都必须小于 4G」。
    按时间（-t/--seconds N）：每片固定 N 秒，例如 300 就是每 5 分钟一段。
            片段时长整齐，适合按片段归档或上传。
            若因码率波动导致某片超过体积阈值，会自动缩小秒数重试，
            并在日志里写明实际用的秒数。

切割方式只有一种：
    FFmpeg 流拷贝（-c copy）：按关键帧重新封装，不重新编码。
    特点：每一段都能单独播放；画质音质零损失；需要本机已安装 ffmpeg。
    （历史版本的 bytes 纯字节切割与 auto 自动降级已删除——bytes 的产物
    第 2 段起播不了，auto 还会在用户不知情时静默降级，都已不再提供。）

关于 ffmpeg：
    脚本会先在 PATH 里找 ffmpeg，找不到再翻各平台常见安装位置
    （WinGet / Chocolatey / Scoop / Homebrew / /usr/local/bin 等），
    最后还会认脚本目录下自带的 ffmpeg/bin。
    全都没有时，交互模式会问一句「要不要自动下载安装」，
    也可以直接加 --install-ffmpeg。装的是官方下载页推荐的静态构建，
    只解压到脚本目录下的 ffmpeg/ 里，不写系统目录、不改 PATH。
    没有 ffmpeg 就切不了——不再有别的兜底模式。

关于大疆（DJI）等运动相机的 MP4（重要）：
    这类文件除了主视频和音频，还塞了 djmd / dbgi / tmcd 三个 data 流和
    一条 mjpeg 缩略图流。mp4 封装器写不了这些流，所以 copy 模式会自动
    跳过它们，只保留主视频+音频，否则 ffmpeg 会直接报
    "Could not find tag for codec none in stream #2" 而整体失败。
    这些附加流是相机自己的遥测数据（含拍摄定位/运动记录），
    跳过只影响这些私有遥测数据，视频和音频本体不受影响。

关于 copy 模式保留不了的东西（实话实说）：
    * 上面那类容器装不下的附加流会被跳过（只记警告，不算失败）。
    * 容器标签里的 encoder 会被 ffmpeg 强行写成 "Lavf x.x"（大疆的原值是
      "DJI OsmoAction4"）。这条实测无解：-metadata、-fflags +bitexact、
      原生标签名、换容器全试过，要么被覆盖要么直接消失。
    除此之外，creation_time / location / make / model 等标签都能带过去。

过滤规则（和网页版「监控目录 → 过滤规则」完全同一套口径）：
    只看 / 不看哪些文件，两个方向都支持「包含」和「正则」：

        仅限（--name-include）：只处理命中的文件；留空 = 不限
        排除（--name-exclude）：命中就不处理；留空 = 不排除

    比对的是**文件 / 文件夹的完整名字（含扩展名）**，以及该文件到所选目录
    之间**各级文件夹的名字**——刻意不含所选目录以上的路径，否则
    `D:\\Videos` 里的 `Videos` 会被当成命中内容，用户很难联想到是路径上游撞的。
    所以一条「包含 相机」就能同时覆盖：

        相机导入/2026/a.mp4    -> 父目录名命中 -> 处理
        其他/相机花絮.mp4      -> 文件名命中   -> 处理
        其他/a.mp4             -> 哪段都没命中 -> 不处理

    「包含」不区分大小写；「正则」按原样生效（要忽略大小写就写 (?i)）。
    命令行/向导里以 `re:` 开头表示正则，否则一律按包含：

        --name-include "相机"                 # 包含「相机」
        --name-exclude "_proxy,re:\\.bak$"     # 排除含 _proxy 的、以及以 .bak 结尾的

    **排除优先于仅限**：同一条同时命中两边时一律排除。这不是可配置的偏好——
    让「排除」输给「仅限」意味着用户明确说不要的东西还可能被切。

日志与调试：
    默认会把控制台输出同时写进脚本目录下的 video_splitter_log.txt，
    方便运行完再回头把手把复制（双击启动时控制台窗口容易被关掉）。
    不想要就加 --no-log；用 --log 文件名 可以换成别的名字。
    排查问题时加 --debug：会额外打印 ffmpeg/ffprobe 的路径与版本、
    源文件的流布局与容器标签、ffmpeg 完整命令与返回码、每段时长码率明细。

用法示例：
    python video_splitter.py D:\\Videos
    python video_splitter.py D:\\Videos --seconds 300
    python video_splitter.py D:\\Videos --seconds 300 --debug
    python video_splitter.py D:\\Videos --name-include "相机"          # 只切文件名/夹名含「相机」的
    python video_splitter.py D:\\Videos --name-exclude "_proxy,re:\\.bak$"
    python video_splitter.py D:\\Videos --min-size 200M --outdir D:\\切片

不想装 Python 的话：
    cli/build_exe.py 会把本脚本打包成一个免安装的可执行文件
    （Windows 是 video_splitter.exe，macOS 是 Unix 二进制），
    双击或拖文件夹上去即可运行，目标机器上不需要任何 Python 环境。
    打包出来的程序与本脚本行为完全一致（同一份代码）。

输出命名：
    原文件名#1.mp4、原文件名#2.mp4、原文件名#3.mp4 ...

原文件怎么处理（避免和切片混淆）：
    默认把原文件改名为 原文件名#origin.mp4，一眼就能认出哪条是原片；
    也可以用 --mark-source move 把原片移进单独的 origin/ 文件夹，
    或用 --mark-source none 保持原样。加 --delete-source 则直接删除原片。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import platform
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------- 基础常量

DEFAULT_THRESHOLD = "3.9G"          # 默认分割阈值（比 FAT32 的 4GiB 上限留约 100MB 余量）

# 原片归档子目录（--mark-source move 用）的默认名。
# 刻意与 app/config.py 的 DEFAULT_SOURCE_DIR 保持一致：两边是同一个工具的两个
# 入口，归档目录名各用各的话，用户「命令行切完、再去网页上扫」会看到
# origin/ 和 resize-video-origin-file/ 两套目录，很难理解哪个是干什么的。
DEFAULT_SOURCE_DIR = "resize-video-origin-file"

# 历史上用过的默认名。**扫描时必须一起排除**：move 归档过去的原片是保留
# 原文件名的（不像 rename 那样带 #origin 标记），一旦漏排除，换个归档目录名
# 之后它们看起来就是普通新视频，会被整个重切一遍。
# 与 app/config.py 的 LEGACY_SOURCE_DIRS 是同一份清单，改要一起改。
LEGACY_SOURCE_DIRS = ("origin",)

# 默认处理的视频扩展名：只认 ffmpeg 能无损流拷贝分段的 8 种容器。
# 旧配置/旧命令行里的其它后缀（avi / wmv / flv ...）会被直接忽略——
# 不是不认识它们，是它们没法在「每段都能独立播放」的前提下无损切分。
DEFAULT_EXTS = (
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".ts", ".m2ts", ".mts",
)

# 这些格式适合用 ffmpeg segment 流拷贝（按关键帧切分后每段仍可独立播放）
SEGMENT_FRIENDLY = {
    ".mp4": "mp4",
    ".m4v": "mp4",
    ".mov": "mov",
    ".mkv": "matroska",
    ".webm": "webm",
    ".ts": "mpegts",
    ".m2ts": "mpegts",
    ".mts": "mpegts",
}

# ffmpeg 会强行改写的容器标签，没法保留原值（实测 -metadata / -fflags +bitexact
# / 原生标签名 / 换容器 全试过：要么被写成 Lavf 版本号，要么直接消失）。
FFMPEG_LOCKED_TAGS = {"encoder"}

IS_WINDOWS = os.name == "nt"
IS_MAC = platform.system() == "Darwin"

# 脚本自身所在目录（自动安装的 ffmpeg 会放在这里的 ffmpeg/bin 下，
# 日志文件默认也写在这里）。
#
# 打包成可执行文件后（PyInstaller onefile）__file__ 指向**运行时解压出来的
# 临时目录**，每次启动都是新的一份、退出即删。照它取目录的话，日志会写到
# 一个马上就消失的地方，自动装好的 ffmpeg 下次启动又不见了 —— 所以冻结
# 状态下必须改用 sys.executable（真正那个可执行文件）所在目录。
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
LOCAL_FFMPEG_DIR = SCRIPT_DIR / "ffmpeg"


# ---------------------------------------------------------------- 控制台编码

def init_console() -> None:
    """让 Windows 控制台也能正常显示中文。

    三个流都要管，**包括 stdin**：Windows 上 sys.stdin 默认按系统 ANSI 代码页
    （简体中文机器是 cp936）解码，而本脚本已经把控制台代码页切到了 UTF-8，
    于是重定向/管道送进来的 UTF-8 中文会被按 GBK 解成乱码 ——
    表现是向导里输入的「相机」变成「鐩告満」，规则看着像写坏了。
    stdout/stderr 不设置的话同理，中文日志会在老终端里炸掉。
    """
    if IS_WINDOWS:
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if getattr(stream, "encoding", None) and \
                    stream.encoding.lower().replace("-", "") not in ("utf8", "utf_8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ---------------------------------------------------------------- 工具函数

def parse_size(text: str) -> int:
    """把 '3.9G' / '500M' / '1024' 这类写法解析成字节数（1G = 1024^3）。"""
    s = str(text).strip().upper().replace(" ", "")
    m = re.match(r"^([0-9]*\.?[0-9]+)([KMGTP]?)(IB|B)?$", s)
    if not m:
        raise ValueError("无法解析的大小写法：%s（示例：3.9G / 500M / 2G）" % text)
    num = float(m.group(1))
    unit = m.group(2)
    mult = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5}[unit]
    value = int(num * mult)
    if value <= 0:
        raise ValueError("分割大小必须大于 0")
    return value


def human_size(num: float) -> str:
    """字节数转人类可读字符串。"""
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < step or unit == "TB":
            return "%.2f %s" % (num, unit) if unit != "B" else "%d B" % int(num)
        num /= step
    return "%.2f TB" % num


LOG_HANDLE = None
LOG_PATH = None
DEBUG_MODE = False


def log(msg: str = "") -> None:
    """控制台 + 日志文件同时输出。"""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # 极老的终端可能编不出某些字符，降级成安全写法，别让程序崩掉
        print(msg.encode("utf-8", "replace").decode("utf-8", "replace"), flush=True)
    if LOG_HANDLE is not None:
        try:
            LOG_HANDLE.write(msg + "\n")
            LOG_HANDLE.flush()
        except Exception:
            pass


def dbg(msg: str = "") -> None:
    """单行调试信息：只有加了 --debug 才输出（同时进日志）。"""
    if DEBUG_MODE:
        log("   [调试] " + msg)


def dbg_block(title: str, lines) -> None:
    """成块的调试信息，方便一眼看完一段诊断内容。"""
    if not DEBUG_MODE:
        return
    log("   [调试] ┌─ %s" % title)
    for ln in lines:
        log("   [调试] │ %s" % ln)
    log("   [调试] └─")


def format_duration(seconds: float) -> str:
    """秒数 -> 12:34.5 / 1:23:45.6 这种可读写法。"""
    if not seconds or seconds <= 0:
        return "-"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h:
        return "%d:%02d:%04.1f" % (h, m, s)
    return "%d:%04.1f" % (m, s)


# ---------------------------------------------------------------- 时间戳 / 元数据

def get_source_times(src: Path):
    """返回 (创建时间, 修改时间, 访问时间)；取不到的返回 None。"""
    st = src.stat()
    ctime = getattr(st, "st_birthtime", None)   # macOS / BSD
    if ctime is None and IS_WINDOWS:
        ctime = st.st_ctime                     # Windows 上 st_ctime 即创建时间
    return ctime, st.st_mtime, st.st_atime


def _set_windows_creation_time(path: Path, ctime: float) -> bool:
    """Windows：用 Win32 API 直接写创建时间（无需第三方库）。"""
    if not IS_WINDOWS or ctime is None:
        return False

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_uint32),
                    ("dwHighDateTime", ctypes.c_uint32)]

    def to_ft(ts: float) -> FILETIME:
        total = int(float(ts) * 10_000_000) + 116444736000000000
        return FILETIME(total & 0xFFFFFFFF, total >> 32)

    kernel32 = ctypes.windll.kernel32
    FILE_WRITE_ATTRIBUTES = 0x0100
    SHARE_ALL = 0x00000007
    OPEN_EXISTING = 3

    # 显式声明签名，避免 64 位环境下句柄/指针被截断
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
                                     ctypes.c_uint32, ctypes.c_void_p,
                                     ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_void_p]
    kernel32.SetFileTime.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    handle = kernel32.CreateFileW(str(path), FILE_WRITE_ATTRIBUTES, SHARE_ALL,
                                  None, OPEN_EXISTING, 0, None)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return False
    try:
        ok = kernel32.SetFileTime(handle, ctypes.byref(to_ft(ctime)), None, None)
        return bool(ok)
    finally:
        kernel32.CloseHandle(handle)


def _set_mac_creation_time(path: Path, ctime: float) -> bool:
    """macOS：借用 Xcode 命令行工具里的 SetFile 写创建时间（没有就跳过）。"""
    if not IS_MAC or ctime is None:
        return False
    setfile = shutil.which("SetFile")
    if not setfile:
        return False
    stamp = time.strftime("%m/%d/%Y %H:%M:%S", time.localtime(float(ctime)))
    try:
        r = subprocess.run([setfile, "-d", stamp, str(path)],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def copy_timestamps(src: Path, dst: Path) -> str:
    """把源文件的创建/修改时间套用到切片上，返回说明文字。"""
    ctime, mtime, atime = get_source_times(src)
    notes = []

    # 修改时间 & 访问时间（跨平台可用）
    try:
        os.utime(dst, ns=(int(atime * 1e9), int(mtime * 1e9)))
        notes.append("修改时间")
    except Exception:
        pass

    # 创建时间（平台相关，尽力而为）
    ok = _set_windows_creation_time(dst, ctime) or _set_mac_creation_time(dst, ctime)
    if ok:
        notes.append("创建时间")
    elif IS_MAC and ctime is not None:
        notes.append("创建时间[未设置：需 Xcode 命令行工具]")

    # 权限位（macOS / Linux）
    if not IS_WINDOWS:
        try:
            shutil.copymode(src, dst)
        except Exception:
            pass

    return "+".join(notes) if notes else "未设置"


# ---------------------------------------------------------------- ffmpeg 探测

def candidate_dirs():
    """
    按优先级列出 ffmpeg 可能藏身的目录。

    很多机器上 ffmpeg 明明装了，却因为「没进 PATH」而找不到：
    比如 WinGet 会装到带哈希的深层包目录里，双击脚本启动时常常读不到。
    所以除了 PATH，这里再主动翻一遍各平台的常见安装位置。
    """
    dirs = [LOCAL_FFMPEG_DIR / "bin", SCRIPT_DIR / "bin", SCRIPT_DIR]

    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        home = os.environ.get("USERPROFILE", "")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        for raw in (
            os.path.join(local, "Microsoft", "WinGet", "Links"),
            r"C:\ProgramData\chocolatey\bin",
            os.path.join(home, "scoop", "shims"),
            os.path.join(home, "scoop", "apps", "ffmpeg", "current", "bin"),
            r"C:\ffmpeg\bin",
            os.path.join(pf, "ffmpeg", "bin"),
            os.path.join(pf86, "ffmpeg", "bin"),
        ):
            if raw:
                dirs.append(Path(raw))
        # WinGet 的包目录形如 Packages\Gyan.FFmpeg*\<build>\bin，只能靠通配去找
        winget_pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.is_dir():
            try:
                for pkg in sorted(winget_pkgs.glob("*FFmpeg*")):
                    dirs.extend(sorted(pkg.glob("*/bin"), reverse=True))
            except Exception:
                pass
    elif IS_MAC:
        dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin"),
                 Path("/opt/local/bin"), Path("/usr/bin")]
    else:
        dirs += [Path("/usr/local/bin"), Path("/usr/bin"),
                 Path("/snap/bin"), Path("/opt/ffmpeg/bin")]

    return dirs


def find_bin(name: str):
    """先查 PATH，再逐个翻常见安装目录；找不到返回 None。"""
    exe = f"{name}.exe" if IS_WINDOWS else name
    hit = shutil.which(exe) or shutil.which(name)
    if hit:
        return hit
    for d in candidate_dirs():
        for cand in (d / exe, d / name):
            try:
                if cand.is_file():
                    return str(cand)
            except OSError:
                continue
    return None


# ---------------------------------------------------------------- ffmpeg 自动安装

# 以下都是 ffmpeg.org 官网下载页列出的主流第三方静态构建，解压即用、无需编译。
# 只解压到脚本目录，不写系统目录、不改 PATH，删掉 ffmpeg 文件夹就彻底卸载。
FFMPEG_SOURCES = {
    "win": [
        ("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
         "gyan.dev（FFmpeg 官网推荐的 Windows 构建）"),
        ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
         "ffmpeg-master-latest-win64-gpl.zip",
         "BtbN/FFmpeg-Builds（GitHub 官方 Release）"),
    ],
    "mac": [
        ("https://evermeet.cx/ffmpeg/getrelease/zip",
         "evermeet.cx（macOS 静态构建）"),
    ],
    "linux": [
        ("https://johnvansickle.com/ffmpeg/releases/"
         "ffmpeg-release-amd64-static.tar.xz",
         "johnvansickle.com（Linux 静态构建）"),
    ],
}

MANUAL_HINT = {
    "win": "winget install Gyan.FFmpeg  或  choco install ffmpeg  "
           "或去 https://www.gyan.dev/ffmpeg/builds/ 下载后把 bin 目录加进 PATH",
    "mac": "brew install ffmpeg",
    "linux": "sudo apt install ffmpeg（或对应发行版的包管理器）",
}


def platform_key() -> str:
    if IS_WINDOWS:
        return "win"
    if IS_MAC:
        return "mac"
    return "linux"


def progress(msg: str) -> None:
    """只写控制台、可反复覆盖的进度行（不落日志，免得把日志刷爆）。"""
    try:
        sys.stdout.write("\r" + msg.ljust(70))
        sys.stdout.flush()
    except Exception:
        pass


# 单个来源的下载上限：总耗时与最低平均速度。
# 实测某些镜像会「连得上但一字节一字节地挤」，光靠 socket 超时抓不住，
# 会把整个安装卡住好几分钟，所以额外加一道总量和速度的闸。
DOWNLOAD_MAX_SECONDS = 420
DOWNLOAD_MIN_SPEED = 10 * 1024          # 字节/秒


def download_file(url: str, dest: Path, label: str,
                  max_seconds: int = DOWNLOAD_MAX_SECONDS) -> None:
    """标准库下载（不依赖 requests），带百分比进度、总量上限与最低速度保护。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": "video_splitter/1.0 (python-urllib)"})
    ctx = ssl.create_default_context()
    started = time.time()
    total = 0
    got = 0
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp, \
            open(dest, "wb") as fout:
        total = int(resp.headers.get("Content-Length") or 0)
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fout.write(chunk)
            got += len(chunk)
            elapsed = time.time() - started
            if total:
                progress("    正在下载 %s … %5.1f%%（%.1f / %.1f MB）"
                         % (label, got * 100.0 / total,
                            got / 1048576.0, total / 1048576.0))
            else:
                progress("    正在下载 %s … %.1f MB" % (label, got / 1048576.0))
            if elapsed > max_seconds:
                raise RuntimeError("下载超时：%d 秒只拿到 %.1f MB，换下一个来源"
                                   % (int(elapsed), got / 1048576.0))
            if elapsed > 15 and got / elapsed < DOWNLOAD_MIN_SPEED:
                raise RuntimeError("下载速度过慢（%.1f KB/s），换下一个来源"
                                   % (got / elapsed / 1024.0))
    progress("")
    sys.stdout.write("\n")
    if total and got < total:
        raise RuntimeError("下载不完整：只收到 %.1f MB / %.1f MB"
                           % (got / 1048576.0, total / 1048576.0))


def extract_archive(archive: Path, workdir: Path) -> Path:
    """解压 zip / tar.xz 到 workdir/extract，返回解压目录。"""
    out = workdir / "extract"
    out.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(out)
    else:
        with tarfile.open(archive) as tf:
            try:
                tf.extractall(out, filter="data")      # Python 3.12+ 推荐写法
            except TypeError:
                tf.extractall(out)
    return out


def find_binaries_in(root: Path) -> dict:
    """在解压出来的目录树里找 ffmpeg / ffprobe / ffplay。"""
    wanted = ("ffmpeg", "ffprobe", "ffplay")
    found = {}
    for p in root.rglob("*"):
        try:
            if p.is_file() and p.suffix.lower() in ("", ".exe") \
                    and p.stem.lower() in wanted:
                found.setdefault(p.stem.lower(), p)
        except OSError:
            continue
    return found


def install_ffmpeg(target_dir=None) -> str:
    """下载静态版 ffmpeg 安装到脚本目录下的 ffmpeg/bin/，成功返回 ffmpeg 路径。"""
    bin_dir = Path(target_dir or LOCAL_FFMPEG_DIR) / "bin"
    key = platform_key()

    log("")
    log("-" * 68)
    log("自动安装 ffmpeg")
    log("-" * 68)
    log("安装位置：%s" % bin_dir)
    log("说明    ：只解压到脚本目录，不需要管理员权限、不改动 PATH，")
    log("          以后想卸载直接删掉这个 ffmpeg 文件夹即可。")

    # macOS 上 Homebrew 是官方推荐的安装方式，优先走它
    if IS_MAC and shutil.which("brew"):
        log("")
        log("检测到 Homebrew，优先用官方推荐方式：brew install ffmpeg")
        try:
            if subprocess.run(["brew", "install", "ffmpeg"]).returncode == 0:
                hit = find_bin("ffmpeg")
                if hit:
                    log("安装成功：%s" % hit)
                    return hit
            log("Homebrew 安装未成功，改用静态构建下载。")
        except Exception as exc:              # noqa: BLE001
            log("Homebrew 安装失败（%s），改用静态构建下载。" % exc)

    errors = []
    tmp = Path(tempfile.mkdtemp(prefix="ffmpeg_dl_"))
    try:
        for idx, (url, label) in enumerate(FFMPEG_SOURCES[key], start=1):
            name = url.rstrip("/").split("/")[-1] or "ffmpeg-archive"
            archive = tmp / name
            log("")
            log("[来源 %d/%d] %s" % (idx, len(FFMPEG_SOURCES[key]), label))
            log("  %s" % url)
            try:
                download_file(url, archive, label)
                log("  下载完成：%s" % human_size(archive.stat().st_size))
                root = extract_archive(archive, tmp)
                found = find_binaries_in(root)
                if "ffmpeg" not in found:
                    raise RuntimeError("压缩包里没找到 ffmpeg 可执行文件")

                bin_dir.mkdir(parents=True, exist_ok=True)
                for tool, src_path in found.items():
                    dst = bin_dir / (tool + (".exe" if IS_WINDOWS else ""))
                    shutil.copy2(str(src_path), str(dst))
                    if not IS_WINDOWS:
                        os.chmod(str(dst), 0o755)

                exe = bin_dir / ("ffmpeg.exe" if IS_WINDOWS else "ffmpeg")
                chk = subprocess.run([str(exe), "-version"], capture_output=True,
                                     text=True, encoding="utf-8",
                                     errors="replace", timeout=120)
                if chk.returncode != 0:
                    raise RuntimeError("自检失败：%s"
                                       % " ".join((chk.stderr or "").split())[:200])
                version = (chk.stdout or "").splitlines()[0] if chk.stdout else "ffmpeg"
                log("  自检通过：%s" % version)
                log("")
                log("ffmpeg 安装成功，脚本会自动识别，无需手动配置。")
                return str(exe)
            except Exception as exc:          # noqa: BLE001
                errors.append("%s：%s" % (label, exc))
                log("  该来源失败：%s" % exc)
                continue
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    log("")
    log("自动安装失败。可以手动安装后重跑：")
    log("  %s" % MANUAL_HINT[key])
    for e in errors:
        log("  - %s" % e)
    return None


def ensure_ffmpeg(args, allow_prompt: bool = False):
    """
    返回 (ffmpeg, ffprobe)。缺失时按需自动下载安装。
    非交互场景（脚本调用、-y、--no-install-ffmpeg）绝不擅自联网下载。
    注意：本工具只有 ffmpeg 流拷贝一条路，缺了 ffmpeg 就什么也做不了。
    """
    ffmpeg, ffprobe = find_bin("ffmpeg"), find_bin("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe

    log("")
    log("环境检查  ：未检测到 ffmpeg。本工具只做 ffmpeg 无损流拷贝，")
    log("            没有 ffmpeg 就无法切割（旧版的字节切割兜底已删除）。")

    if getattr(args, "no_install_ffmpeg", False):
        return ffmpeg, ffprobe

    do_install = bool(getattr(args, "install_ffmpeg", False))
    if not do_install:
        if not allow_prompt or args.yes or not sys.stdin.isatty():
            log("            需要的话可以加 --install-ffmpeg 让脚本自动下载安装。")
            return ffmpeg, ffprobe
        try:
            answer = ask("            是否现在自动下载安装 ffmpeg？[Y/n]：", "y")
        except Exception:
            answer = "n"
        do_install = answer.strip().lower() not in ("n", "no")

    if not do_install:
        log("            已跳过安装，本次无法切割。")
        return ffmpeg, ffprobe

    if install_ffmpeg():
        return find_bin("ffmpeg"), find_bin("ffprobe")
    log("            安装未完成，本次无法切割。")
    return ffmpeg, ffprobe


def probe_duration(src: Path, ffprobe) -> float:
    """读取视频时长（秒），失败返回 0。"""
    if ffprobe:
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
                capture_output=True, text=True, timeout=180)
            return float((r.stdout or "").strip())
        except Exception:
            pass
    ffmpeg = find_bin("ffmpeg")
    if ffmpeg:
        try:
            r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(src)],
                               capture_output=True, text=True, timeout=180)
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r.stderr or "")
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        except Exception:
            pass
    return 0.0


def probe_streams(src: Path, ffprobe) -> dict:
    """
    读取源文件的流布局与容器标签，返回：
        {"streams": [{"index","codec_type","codec_name"}...],
         "tags": {标签名: 值},
         "unmuxable": bool}

    unmuxable 用来判断「有没有 mp4 装不下的流」——这是本项目踩过的坑：
    大疆（DJI）的 MP4 里有 hevc 主视频 + aac 音频之外，还塞了
    djmd / dbgi / tmcd 三个 data 流和一个 mjpeg 缩略图流。
    mp4 封装器写不了这些，于是 `-map 0` 会直接报
    "Could not find tag for codec none in stream #2" 而整体失败。
    """
    info = {"streams": [], "tags": {}, "unmuxable": False}
    if not ffprobe:
        return info
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", str(src)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180)
        data = json.loads(r.stdout or "{}")
    except Exception:
        return info

    n_video = n_audio = 0
    for s in data.get("streams", []):
        ctype = s.get("codec_type", "")
        info["streams"].append({"index": s.get("index"),
                                "codec_type": ctype,
                                "codec_name": s.get("codec_name", ""),
                                "codec_tag": s.get("codec_tag_string", ""),
                                "handler_name": (s.get("tags") or {}).get(
                                    "handler_name", "")})
        if ctype == "video":
            n_video += 1
        elif ctype == "audio":
            n_audio += 1

    # 有 data/字幕等 mp4 装不下的流，或有多条视频流（缩略图、封面），就得精选映射
    extra = [s for s in info["streams"] if s["codec_type"] not in ("video", "audio")]
    info["unmuxable"] = bool(extra) or n_video > 1
    info["n_video"] = n_video
    info["n_audio"] = n_audio
    info["extra"] = extra

    # major_brand 之类的不是真正的元数据标签，是 ffprobe 自己解析出来的，不能回写
    skip = {"major_brand", "minor_version", "compatible_brands"}
    for k, v in (data.get("format", {}).get("tags") or {}).items():
        if k.lower() not in skip:
            info["tags"][k] = v
    return info


# ---------------------------------------------------------------- 切割前检查

def precheck_targets(paths, overwrite: bool) -> None:
    """动手之前先确认目标文件不存在，避免只写了一半才报错。"""
    for p in paths:
        if p.exists() and not overwrite:
            raise RuntimeError("目标文件已存在：%s（加 --overwrite 覆盖，或先删除旧切片）" % p)


# ---------------------------------------------------------------- ffmpeg 流拷贝

class FatalSplitError(RuntimeError):
    """重试也不可能成功的错误（容器不支持、目标文件冲突等），直接往外抛。"""


def _cmd_line(cmd) -> str:
    """把命令数组拼成一行好复制、好粘贴的字符串（含空格的参数自动加引号）。"""
    return " ".join(('"%s"' % c if (" " in c and not c.startswith('"')) else c)
                    for c in cmd)


def split_by_ffmpeg(src: Path, threshold: int, outdir: Path, dry_run: bool,
                    overwrite: bool, ffmpeg: str, ffprobe, keep_meta: bool,
                    seg_seconds=None):
    """
    FFmpeg 流拷贝分割：-c copy，只换容器不重新编码，画质音质零损失，
    每一段都能独立双击播放。

    两种定段方式：
      * seg_seconds 有值 -> 按时间切：-segment_time 就是你要的秒数。
      * seg_seconds 为 None -> 按大小切：把整条时间轴均分成
        ceil(文件大小 / 阈值) 段，每段体积尽量接近且不超阈值。

    注意 ffmpeg 的切点必须落在关键帧上，所以每段实际时长会略长于目标值。
    无论哪种方式，只要切出来有片段超阈值，都会自动调整参数重试：
    按秒切时缩小秒数，按大小切时增加段数。
    """
    size = src.stat().st_size
    ext = src.suffix.lower()
    seg_format = SEGMENT_FRIENDLY.get(ext)
    if not seg_format:
        raise RuntimeError("该格式不支持无损切分：%s（支持 %s）"
                           % (ext or "?", " ".join(sorted(SEGMENT_FRIENDLY))))

    duration = probe_duration(src, ffprobe)
    if duration <= 0:
        raise RuntimeError("无法读取视频时长")

    stem, suffix = src.stem, src.suffix

    # ---- 流布局与容器标签：决定映射哪些流、回写哪些标签 ----
    # 普通文件 -map 0 全带上；像大疆那种塞了 data/缩略图流的文件必须先精选映射，
    # 否则 mp4 封装器写不了（"Could not find tag for codec none in stream #2"），
    # 整条命令直接失败。
    layout = probe_streams(src, ffprobe)
    restricted = ["-map", "0:v:0", "-map", "0:a?"]
    if layout["unmuxable"]:
        mapping = list(restricted)
        # 说清楚到底跳过了什么，别只笼统说「附加流」
        parts = [(s.get("codec_tag") or s.get("codec_name") or s["codec_type"])
                 for s in layout["extra"]]
        if layout["n_video"] > 1:
            parts.append("mjpeg 缩略图" if any(
                s.get("codec_name") == "mjpeg" for s in layout["streams"])
                else "附加视频流")
        desc = "、".join(sorted(set(p for p in parts if p))) or "附加流"
        log("       源文件含 mp4 装不下的附加流（%s），流拷贝会跳过它们。" % desc)
        log("       这类流一般是相机自带的遥测数据（大疆的 djmd 就含着拍摄")
        log("       定位/运动记录），以及视频缩略图；视频和音频本体不受影响。")
    else:
        mapping = ["-map", "0"]
    allow_mapping_fallback = mapping != restricted

    if DEBUG_MODE:
        lines = ["文件 %s，大小 %s，时长 %s"
                 % (src.name, human_size(size), format_duration(duration))]
        if duration > 0:
            lines.append("平均码率约 %.2f Mbps（%.0f kbps）"
                         % (size * 8 / duration / 1e6, size * 8 / duration / 1e3))
        lines.append("容器 %s -> 分段封装器 %s" % (ext or "?", seg_format))
        # 判断每条流到底保不保留：必须按实际使用的 mapping 来算，
        # 不能只看 codec_type——像大疆的 mjpeg 缩略图也是 video 流，
        # 但 -map 0:v:0 只取第一条视频流，它照样会被丢掉。
        all_map = mapping == ["-map", "0"]
        first_video_done = False
        for s in layout["streams"]:
            if all_map:
                keep = "保留"
            elif s["codec_type"] == "video":
                if not first_video_done:
                    keep = "保留（主视频）"
                    first_video_done = True
                else:
                    keep = "跳过（附加视频流，如缩略图/封面）"
            elif s["codec_type"] == "audio":
                keep = "保留（音频）"
            else:
                keep = "跳过（%s 流，容器装不下）" % s["codec_type"]
            lines.append("流 #%-3s %-6s %-10s %-6s %s"
                         % (s["index"], s["codec_type"],
                            s.get("codec_name") or "", s.get("codec_tag") or "",
                            keep))
        if layout["tags"]:
            for k, v in sorted(layout["tags"].items()):
                lock = "（ffmpeg 会强制改写，无法保留）" if k.lower() in FFMPEG_LOCKED_TAGS else ""
                lines.append("标签 %s = %s%s" % (k, v, lock))
        else:
            lines.append("标签 （源文件没有容器级标签）")
        if not keep_meta:
            lines.append("注意：本次带 --no-metadata，容器标签不会写入切片")
        dbg_block("源文件诊断", lines)

    # ---- 分段策略 ----
    if seg_seconds:
        n_expected = max(1, math.ceil(duration / seg_seconds))
        log("       按时间切分：目标每段 %.1f 秒，预计 %d 段"
            % (seg_seconds, n_expected))
    else:
        n_expected = max(1, math.ceil(size / threshold))
        log("       按大小切分：目标 %d 段，平均 %s/段"
            % (n_expected, human_size(math.ceil(size / n_expected))))

    if dry_run:
        if seg_seconds:
            eff = min(seg_seconds, duration)
            per = int(size * eff / duration) if duration else 0
        else:
            per = math.ceil(size / n_expected)
        return [(outdir / ("%s#%d%s" % (stem, i, suffix)), per)
                for i in range(1, n_expected + 1)], "copy"

    # 流拷贝会先落盘再改名，这里提前把冲突挡掉，避免留下半套切片。
    # 按秒切时段数由 ffmpeg 决定，关键帧对齐可能多切出一段，所以多预检 2 个名字。
    precheck_targets([outdir / ("%s#%d%s" % (stem, i, suffix))
                      for i in range(1, n_expected + 3)], overwrite)

    n_target = n_expected
    eff_seconds = seg_seconds
    last_error = ""
    for attempt in range(6):
        tmpdir = Path(tempfile.mkdtemp(prefix="vsplit_"))
        mapping_switched = False
        try:
            if seg_seconds:
                seg_time = max(eff_seconds, 0.5)
            else:
                # 按「目标段数」均分时间轴，而不是按「阈值/体积」估时长。
                # 后者会算出一个比 duration/n 略小的分段时长，于是必然多切出
                # 一段小尾巴（实测 6.07G 会切成 2.92G + 2.68G + 63M）。
                # 取 duration/n 的 1% 余量，保证正好 n 段且各段基本等大。
                seg_time = max(duration / n_target * 1.01, 1.0)

            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(src)]
            cmd += mapping
            cmd += ["-c", "copy",
                    "-f", "segment",
                    "-segment_time", "%.3f" % seg_time,
                    "-segment_format", seg_format,
                    "-reset_timestamps", "1"]
            if keep_meta:
                cmd += ["-map_metadata", "0"]
                # 再把源文件的容器标签显式回写一遍，防止某些格式在重新封装时丢标签
                # （拍摄位置 location、相机 make/model 都在这类标签里）。
                for tag, val in layout["tags"].items():
                    if tag.lower() in FFMPEG_LOCKED_TAGS:
                        continue
                    cmd += ["-metadata", "%s=%s" % (tag, val)]
            if seg_format in ("mp4", "mov"):
                cmd += ["-movflags", "+use_metadata_tags"]
            cmd += [str(tmpdir / ("part_%05d" + suffix))]

            dbg("第 %d 次尝试：segment_time = %.3f 秒" % (attempt + 1, seg_time))
            dbg("完整命令（可直接粘贴到终端复现）：")
            dbg("  " + _cmd_line(cmd))

            t_ff = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=60 * 60 * 6)
            dbg("ffmpeg 返回码 %d，耗时 %.2f 秒" % (r.returncode, time.time() - t_ff))

            if r.returncode != 0:
                detail = " ".join((r.stderr or "").split())[-400:]
                dbg("ffmpeg 报错原文：%s" % (detail or "(无输出)"))
                if allow_mapping_fallback:
                    # 猜错了容器能力，换成精选映射重试一次（不缩小时长，不算一次浪费）
                    allow_mapping_fallback = False
                    mapping = list(restricted)
                    mapping_switched = True
                    last_error = "容器装不下全部流，改为只保留主视频+音频重试"
                    log("       %s" % last_error)
                    continue
                # 非零退出基本是硬错误（编码/容器不支持），重试没意义，直接抛真实原因
                raise FatalSplitError("ffmpeg 执行失败：%s" % (detail or "无错误输出"))

            parts = sorted(tmpdir.glob("part_*" + suffix))
            if not parts:
                raise RuntimeError("ffmpeg 未产生任何片段")

            if DEBUG_MODE:
                lines = []
                tot = 0
                for i, p in enumerate(parts, 1):
                    sz = p.stat().st_size
                    tot += sz
                    d = probe_duration(p, ffprobe) if ffprobe else 0.0
                    lines.append("第 %-3d 段  %-10s  时长 %-11s  码率 %.2f Mbps"
                                 % (i, human_size(sz),
                                    format_duration(d) if d else "-",
                                    sz * 8 / d / 1e6 if d else 0))
                lines.append("合计 %s，占原文件 %.1f%%（差掉的是被跳过的附加流）"
                             % (human_size(tot), tot * 100.0 / size))
                dbg_block("本次切分结果明细", lines)

            # 校验大小：有任何一段超阈值就调整参数重来
            over = [p for p in parts if p.stat().st_size > threshold]
            if over:
                biggest = max(p.stat().st_size for p in over)
                if seg_seconds:
                    # 你要的是「按秒」，所以缩的是秒数，而不是改成按大小均分
                    eff_seconds = max(eff_seconds * (threshold / biggest) * 0.95, 1.0)
                    last_error = ("有片段 %s 超过阈值，按秒模式自动把每段时长"
                                  "缩到 %.1f 秒重试" % (human_size(biggest), eff_seconds))
                else:
                    n_target += 1
                    last_error = ("有片段 %s 超过阈值，改为切成 %d 段重试"
                                  % (human_size(biggest), n_target))
                log("       %s" % last_error)
                continue

            produced = []
            for idx, part in enumerate(parts, start=1):
                target = outdir / ("%s#%d%s" % (stem, idx, suffix))
                if target.exists():
                    if not overwrite:
                        raise RuntimeError("目标文件已存在：%s（加 --overwrite 覆盖）" % target)
                    target.unlink()
                shutil.move(str(part), str(target))
                produced.append((target, target.stat().st_size))
            return produced, "copy"

        except Exception as exc:      # noqa: BLE001
            last_error = str(exc) or last_error
            # 硬错误（容器不支持、目标文件冲突）重试也白搭，直接往外抛
            if isinstance(exc, FatalSplitError) or "目标文件已存在" in last_error:
                raise
            if mapping_switched:
                continue              # 只是换了映射方式，不算一次失败
            if seg_seconds:
                eff_seconds = max(eff_seconds * 0.8, 1.0)   # 兜底：把秒数再缩小点
            else:
                n_target += 1         # 兜底：再多切一段试试
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    raise RuntimeError("多次调整后仍无法满足阈值：%s" % last_error)


# ---------------------------------------------------------------- 原文件标记

def mark_source(src: Path, mode: str, source_dir_name: str) -> str:
    """
    切割完成后标记原文件，方便一眼区分「原片」和「切片」：
      rename -> 改名为 原名#origin.扩展名
      move   -> 移动到同级目录下的归档文件夹
      none   -> 原地不动
    """
    if mode == "none":
        return "原文件保持原位"

    if mode == "rename":
        target = src.with_name("%s#origin%s" % (src.stem, src.suffix))
        if target.exists():
            return "原文件未改名：%s 已存在，请先处理" % target.name
        try:
            src.rename(target)
        except OSError as exc:
            return "原文件改名失败（%s）" % exc
        return "原文件已标记为 %s" % target.name

    # mode == "move"
    dest_dir = src.parent / source_dir_name
    try:
        dest_dir.mkdir(exist_ok=True)
    except OSError as exc:
        return "原文件未移动：创建目录失败（%s）" % exc
    target = dest_dir / src.name
    if target.exists():
        return "原文件未移动：%s 已存在" % target
    try:
        shutil.move(str(src), str(target))
    except Exception as exc:                  # noqa: BLE001
        return "原文件移动失败（%s）" % exc
    return "原文件已移至 %s/" % source_dir_name


# ---------------------------------------------------------------- 过滤规则
#
# 与 app/services/filters.py 是同一套口径（这里内联一份，是为了让本脚本保持
# 「单文件、拷到哪都能跑、能打包成一个可执行文件」这个特性）。语义说明见
# 文件头的「过滤规则」一节，改动时两边要一起改。

# 规则值的正则前缀。网页版是每条规则配一个「包含 / 正则」下拉框，命令行
# 没有这个位置，用前缀最省事：`re:xxx` 是正则，其余一律按包含。
REGEX_PREFIX = "re:"


def parse_rule(text: str):
    """把一条规则文本解析成 {"mode","value"}；空串返回 None。"""
    s = str(text or "").strip()
    if not s:
        return None
    if s.lower().startswith(REGEX_PREFIX):
        value = s[len(REGEX_PREFIX):].strip()
        return {"mode": "regex", "value": value} if value else None
    return {"mode": "contains", "value": s}


def split_rule_text(text: str) -> list:
    """把一行输入按逗号拆成多条规则文本（中英文逗号都认）。

    正则里真要写逗号时用反斜杠转义：`re:^a\\,b$` -> 一条规则 `re:^a,b$`。
    这样「一行写多条」和「正则里含逗号」两个需求不会互相打架
    （正则里的 `\\,` 本来就是「字面逗号」的意思，转义后语义不变）。
    """
    raw = str(text or "").replace("，", ",")
    out, buf, i = [], [], 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw) and raw[i + 1] == ",":
            buf.append(",")
            i += 2
            continue
        if ch == ",":
            part = "".join(buf).strip()
            if part:
                out.append(part)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    part = "".join(buf).strip()
    if part:
        out.append(part)
    return out


def parse_rules(items) -> tuple:
    """解析一批规则文本，返回 (规则列表, 错误说明列表)。

    正则编不过时**当场报出来**，而不是留到扫描时静默跳过 —— 命令行和向导
    都是能立刻改的场合，说清楚比悄悄吞掉好。错误列表为空才算全部解析成功。
    """
    rules, errors = [], []
    for item in items or ():
        rule = parse_rule(item)
        if rule is None:
            continue
        if rule["mode"] == "regex":
            try:
                re.compile(rule["value"])
            except re.error as exc:
                errors.append("正则写错了「%s」：%s" % (rule["value"], exc))
                continue
        rules.append(rule)
    return rules, errors


def format_rules(rules) -> str:
    """把规则列表还原成给人看的写法（正则带 re: 前缀）。"""
    if not rules:
        return "（无）"
    return "、".join((REGEX_PREFIX + r["value"]) if r["mode"] == "regex" else r["value"]
                    for r in rules)


def _prepare_rules(rules) -> tuple:
    """contains 提前转小写、regex 提前编译，免得在扫描热路径上反复做。"""
    out = []
    for item in rules or ():
        value = str(item.get("value") or "")
        if not value:
            continue
        if item.get("mode") == "regex":
            try:
                out.append(("regex", re.compile(value), value))
            except re.error:
                continue            # 解析阶段已经拦过一道，这里是兜底
        else:
            out.append(("contains", value.lower(), value))
    return tuple(out)


def compile_filters(name_include, name_exclude, ext_exclude) -> dict | None:
    """预编译过滤规则；一条都没有时返回 None。

    返回 None（而不是「全空的编译结果」）很重要：调用方据此跳过逐文件的判断，
    无规则时的扫描路径与加这个功能之前一模一样，老用户耗时一字不变。
    """
    inc = _prepare_rules(name_include)
    exc = _prepare_rules(name_exclude)
    ext_exc = tuple(e.lower() for e in (ext_exclude or ()) if e)
    if not (inc or exc or ext_exc):
        return None
    return {"include": inc, "exclude": exc, "ext_exclude": ext_exc}


def _rule_hit(rule, parts, lowered) -> bool:
    """一条规则是否命中这些路径段中的任意一段。"""
    mode, payload, _raw = rule
    for text, lower in zip(parts, lowered):
        if mode == "regex":
            if payload.search(text):
                return True
        elif payload in lower:
            return True
    return False


def filter_reason(compiled: dict, path: Path, rel_parts) -> str | None:
    """文件是否被过滤规则挡下：通过返回 None，否则返回给人看的原因。

    原因会出现在最后的汇总里，所以要写清是哪一类规则挡的，用户才分得清
    「规则写错了」和「本来就没文件」。
    """
    parts = tuple(rel_parts) or (Path(path).name,)
    lowered = tuple(p.lower() for p in parts)
    ext = Path(path).suffix.lower()

    # ① 排除：先类型后名字
    if ext and ext in compiled["ext_exclude"]:
        return "命中排除的文件类型 %s" % ext
    for rule in compiled["exclude"]:
        if _rule_hit(rule, parts, lowered):
            return "命中排除规则「%s」" % rule[2]

    # ② 仅限：非空时要求命中任意一条
    for rule in compiled["include"]:
        if _rule_hit(rule, parts, lowered):
            return None
    return "不符合「仅限」规则" if compiled["include"] else None


def dir_pruned(compiled: dict, rel_parts) -> bool:
    """这个子目录是否整棵都不用进（只可能因为命中排除规则）。

    纯性能优化，不改变结果：被剪掉的目录里每个文件，其相对路径都包含这个
    目录名，逐个判断也会得出同样的结论 —— 只是要白白遍历一整棵子树
    （存储池上很可能是几十万个条目）。

    「仅限」规则**不剪枝**：子目录名没命中，不代表它下面没有命中的文件
    （规则可能是冲着文件名写的）。
    """
    if not rel_parts:
        return False
    parts = tuple(rel_parts)
    lowered = tuple(p.lower() for p in parts)
    return any(_rule_hit(rule, parts, lowered) for rule in compiled["exclude"])


# ---------------------------------------------------------------- 文件收集

def collect_files(folders, exts, recursive: bool, skip_dirs=(),
                  filters: dict = None, min_size: int = 0) -> tuple:
    """收集待处理的视频文件，返回 (文件列表, 跳过原因计数)。

    filters 是 compile_filters 的结果（None = 完全不过滤）。
    跳过计数里只记「被过滤规则挡下 / 小于最小体积」的数量，供最后汇总说明用 ——
    这正是网页版扫描结果里那块「跳过明细」的口径。
    """
    files, seen = [], set()
    skipped = Counter()

    for folder in folders:
        base = Path(folder)
        if not base.is_dir():
            log("  [跳过] 不是文件夹：%s" % base)
            continue

        # 自己递归而不是用 rglob：命中「排除」规则的子目录要能整棵剪掉。
        # followlinks=False 与 rglob 的默认行为一致（都不跟进符号链接目录）。
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            here = Path(dirpath)
            rel_dir = here.relative_to(base).parts

            if not recursive:
                dirnames[:] = []
            elif filters is not None:
                dirnames[:] = [n for n in dirnames
                               if not dir_pruned(filters, rel_dir + (n,))]

            for name in filenames:
                p = here / name
                try:
                    if not p.is_file() or p.suffix.lower() not in exts:
                        continue
                    # 跳过本工具自己产生的切片与已标记的原片，避免重复处理
                    if re.search(r"#(?:\d+|origin)$", p.stem, re.IGNORECASE):
                        continue
                    # 跳过归档目录（原片已移进去，不该再切一次）。
                    # 只比「相对于这个 folder 的中间段」：拿绝对路径的全部段去比的话，
                    # --source-dir 一旦取成 1000、vol1 这类路径上本来就有的段名，
                    # 整棵树都会被判成归档目录，表现是「一个视频都没找到」。
                    if any(part in skip_dirs for part in rel_dir):
                        continue
                    if filters is not None:
                        reason = filter_reason(filters, p, rel_dir + (name,))
                        if reason:
                            skipped[reason] += 1
                            continue
                    if min_size > 0:
                        try:
                            if p.stat().st_size < min_size:
                                skipped["小于最小体积 %s" % human_size(min_size)] += 1
                                continue
                        except OSError:
                            continue
                    rp = p.resolve()
                    if rp in seen:
                        continue
                    seen.add(rp)
                    files.append(p)
                except Exception:
                    continue
    return files, skipped


def pick_folder_gui():
    """弹一个文件夹选择框，返回 (路径, 失败原因)。

    把「用户点了取消」和「这台机器弹不出窗口」分开：前者什么都不用说，
    后者得说清楚为什么 —— 用一个不带 tkinter 的 Python 打包时，产物里
    就没有这个选择框，只回一句「没有选择文件夹」会让人以为按钮坏了。
    """
    try:
        import tkinter
        from tkinter import filedialog
    except Exception as exc:                                  # noqa: BLE001
        return None, ("这个版本没有带图形化选择框（tkinter 不可用：%s）" % exc)
    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.update()
        return (filedialog.askdirectory(title="选择包含视频的文件夹") or None), None
    except Exception as exc:                                  # noqa: BLE001
        return None, "弹出选择框失败（%s）" % exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


# ---------------------------------------------------------------- 主流程

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="video_splitter.py",
        description="把超过指定大小的视频无损切成多段（不转码，保留时间戳与元数据）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python video_splitter.py                         # 不带路径 = 进入交互问答模式
  python video_splitter.py -i                      # 强制进入交互问答模式
  python video_splitter.py D:\\Videos
  python video_splitter.py D:\\Videos --size 2G
  python video_splitter.py D:\\Videos --seconds 300     # 按时间切：每 5 分钟一片
  python video_splitter.py D:\\Videos --seconds 600     # 每 10 分钟一片
  python video_splitter.py D:\\Videos -t 300 --all      # 每 5 分钟一片，所有视频都切
  python video_splitter.py D:\\Videos -t 300 --debug    # 按秒切 + 详细调试信息
  python video_splitter.py D:\\Videos --dry-run         # 只预览，不实际切割
  python video_splitter.py D:\\Videos --mark-source move # 原片移到 origin/ 文件夹
  python video_splitter.py D:\\Videos --mark-source none # 原片原地不动
  python video_splitter.py D:\\Videos --name-include "相机"       # 只切名字含「相机」的
  python video_splitter.py D:\\Videos --name-exclude "_proxy,re:\\.bak$"
  python video_splitter.py D:\\Videos --min-size 200M --outdir D:\\切片
  python video_splitter.py D:\\Videos --no-log          # 不写日志文件
  python video_splitter.py --install-ffmpeg             # 只装 ffmpeg，不处理视频
  python video_splitter.py D:\\Videos --install-ffmpeg   # 缺 ffmpeg 就自动装好再用
""")
    p.add_argument("folders", nargs="*", help="要扫描的文件夹（可多个；留空则弹窗选择）")
    p.add_argument("-s", "--size", default=DEFAULT_THRESHOLD,
                   help="大小阈值，默认 %s（支持 3.9G / 500M / 2G）。"
                        "决定「哪些文件需要切」；不加 -t 时也用它均分每段的体积"
                        % DEFAULT_THRESHOLD)
    p.add_argument("-t", "--seconds", type=float, default=None, metavar="秒",
                   help="按时间切分：每片的目标秒数（300 = 每 5 分钟一片，"
                        "600 = 每 10 分钟一片）。给了它就按秒切，不再按大小均分。"
                        "若切出来有片段超过 --size，会自动把秒数按比例缩小后重试")
    p.add_argument("-m", "--mode", choices=("copy",), default="copy",
                   help=argparse.SUPPRESS)
    p.add_argument("-o", "--outdir", default=None,
                   help="切片输出目录，默认与源文件同目录")
    p.add_argument("--ext", default=",".join(DEFAULT_EXTS),
                   help="要处理的扩展名，逗号分隔（等价于网页版「设置 → 处理的扩展名」）")
    p.add_argument("--ext-exclude", default=None, action="append", metavar="扩展名",
                   help="排除这些扩展名，逗号分隔；可重复。与 --ext 的区别是"
                        "它属于「过滤规则」，会和 --name-* 一起出现在跳过明细里")
    p.add_argument("--name-include", default=None, action="append", metavar="规则",
                   help="「仅限」规则：只处理文件名/文件夹名命中的。逗号分隔、可重复；"
                        "以 re: 开头按正则，否则按包含（忽略大小写）。"
                        "例：--name-include \"相机,re:^DJI_\\\\d{4}\\\\.mp4$\"")
    p.add_argument("--name-exclude", default=None, action="append", metavar="规则",
                   help="「排除」规则：命中的一律不处理。写法同 --name-include，"
                        "且优先于「仅限」。例：--name-exclude \"_proxy,re:\\\\.bak$\"")
    p.add_argument("--min-size", default="0", metavar="体积",
                   help="小于该体积的文件直接忽略（如 100M）。默认 0 = 不限。"
                        "注意它与 --size 不同：--size 决定「多大以上才切」，"
                        "--min-size 决定「多小就不看」")
    p.add_argument("--no-recursive", action="store_true", help="只处理顶层目录，不进子目录")
    p.add_argument("--all", action="store_true",
                   help="不按大小筛选：扫描到的所有视频文件都切（默认只切超过阈值的）。"
                        "想「每 5 分钟切一段」而不受体积限制时用它")
    p.add_argument("--overwrite", action="store_true", help="目标切片已存在时直接覆盖")
    p.add_argument("--mark-source", choices=("rename", "move", "none"), default="rename",
                   help="切割后如何标记原文件，方便与切片区分："
                        "rename=改名 原名#origin.扩展名（默认）；"
                        "move=移动到单独的归档文件夹；none=原地不动")
    p.add_argument("--source-dir", default=DEFAULT_SOURCE_DIR,
                   help="--mark-source move 时的归档文件夹名，默认 %s"
                        % DEFAULT_SOURCE_DIR)
    p.add_argument("--delete-source", action="store_true",
                   help="切割成功后删除原文件（危险，会二次确认；与 --mark-source 互斥）")
    p.add_argument("--keep-metadata", action="store_true", default=True,
                   help="copy 模式保留元数据（默认开启）")
    p.add_argument("--no-metadata", dest="keep_metadata", action="store_false",
                   help="copy 模式不保留元数据")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="以问答方式逐项选择参数（不带路径时自动进入）")
    p.add_argument("-n", "--dry-run", action="store_true", help="只预览不写文件")
    p.add_argument("-y", "--yes", action="store_true", help="跳过所有交互确认")
    p.add_argument("--install-ffmpeg", action="store_true",
                   help="未检测到 ffmpeg 时自动下载静态版并安装到脚本目录")
    p.add_argument("--no-install-ffmpeg", action="store_true",
                   help="禁止自动下载安装 ffmpeg（缺了就直接退出，不做任何处理）")
    p.add_argument("--log", nargs="?", const="video_splitter_log.txt", default=None,
                   help="把输出同时写入日志文件（默认就开启，默认文件名 "
                        "video_splitter_log.txt；不想要就用 --no-log）")
    p.add_argument("--no-log", action="store_true",
                   help="不写日志文件，只在控制台输出")
    p.add_argument("--debug", action="store_true",
                   help="输出详细调试信息：ffmpeg/ffprobe 路径与版本、源文件流布局、"
                        "容器标签、ffmpeg 完整命令与返回码、每段时长码率明细")
    p.add_argument("--pause", dest="pause_mode", action="store_const", const="always",
                   help="结束时停住等按回车（双击启动时本来就会自动停，"
                        "从终端跑才需要显式加）")
    p.add_argument("--no-pause", dest="pause_mode", action="store_const", const="never",
                   help="结束时不停住（写进脚本 / 被别的程序调用时用）")
    p.set_defaults(pause_mode="auto")

    preset_group = p.add_argument_group(
        "预设", "一套可复用的设置（切分方式、格式、过滤规则、输出位置、原片处理…），"
                "存在程序旁边的 presets.json 里。交互向导最后会问你要不要存一个。")
    preset_group.add_argument("--preset", metavar="名字",
                              help="套用这个预设里的设置。命令行上另外显式给的参数"
                                   "优先于预设；配合 -i 可以只补一个文件夹路径")
    preset_group.add_argument("--save-preset", metavar="名字",
                              help="把本次生效的设置存成预设，存完即退出（不切割、"
                                   "不进向导）。想存向导里选好的设置，向导最后"
                                   "会问你要不要存。")
    preset_group.add_argument("--list-presets", action="store_true",
                              help="列出已保存的预设，然后退出")
    preset_group.add_argument("--delete-preset", metavar="名字",
                              help="删除一个预设，然后退出")
    return p


# ---------------------------------------------------------------- 执行主体

def _summarize_counts(counter, limit: int = 3) -> str:
    """把 Counter 写成「原因×N、原因×N …（另有 K 类）」这样一行。"""
    items = counter.most_common()
    if not items:
        return ""
    head = "、".join("%s×%d" % (k, v) for k, v in items[:limit])
    if len(items) > limit:
        head += "（另有 %d 类原因）" % (len(items) - limit)
    return head


def resolve_filters(args) -> tuple:
    """从命令行参数（或向导写回的值）解析出过滤规则，返回 (compiled, 明细 dict)。

    明细里的字段直接拿去打日志和汇总页，保证「界面显示的规则」和「真正生效的
    规则」是同一份数据，不会出现两边说法不一致。
    """
    inc_rules, inc_errs = parse_rules(
        [t for raw in (args.name_include or []) for t in split_rule_text(raw)])
    exc_rules, exc_errs = parse_rules(
        [t for raw in (args.name_exclude or []) for t in split_rule_text(raw)])
    for err in inc_errs + exc_errs:
        log("过滤规则 %s（该条已忽略）" % err)

    ext_exclude = []
    for raw in (args.ext_exclude or []):
        for text in split_rule_text(raw):
            ext = text.lower()
            if not ext.startswith("."):
                ext = "." + ext
            if ext not in ext_exclude:
                ext_exclude.append(ext)

    return (compile_filters(inc_rules, exc_rules, ext_exclude),
            {"include": inc_rules, "exclude": exc_rules, "extExclude": ext_exclude})


def run_processing(args, folders, threshold: int, ffmpeg, ffprobe) -> int:
    """执行「扫描 -> 切割 -> 标记原文件 -> 汇总」，返回退出码。"""
    exts = tuple(e.strip().lower() for e in args.ext.split(",") if e.strip())
    exts = tuple(e if e.startswith(".") else "." + e for e in exts)

    # 最小体积：与 --size 是两件事。--size 决定「多大以上才切」，
    # --min-size 决定「多小就不看」（网页版「监控 → 最小体积」那一项）。
    try:
        raw_min = str(args.min_size or "").strip()
        min_size = 0 if raw_min in ("", "0") else parse_size(raw_min)
    except ValueError as exc:
        log("参数错误：--min-size %s" % exc)
        return 2

    filters, filter_detail = resolve_filters(args)

    log("=" * 68)
    log("视频无损分割工具")
    log("=" * 68)
    log("扫描目录   ：%s" % "、".join(str(Path(f).resolve()) for f in folders))
    if args.seconds:
        log("切分方式   ：按时间切，每片 %.1f 秒（%.1f 分钟）"
            % (args.seconds, args.seconds / 60.0))
    else:
        log("切分方式   ：按大小切，每片不超过 %s" % args.size)
    if args.all:
        log("处理范围   ：全部视频文件（--all，不按大小筛选）")
    else:
        log("大小阈值   ：%s（%d 字节）—— 决定哪些文件需要切" % (args.size, threshold))
    log("处理格式   ：%s" % " ".join(exts))
    if filter_detail["extExclude"]:
        log("排除格式   ：%s" % " ".join(filter_detail["extExclude"]))
    if min_size:
        log("最小体积   ：%s（更小的文件直接忽略）" % human_size(min_size))
    if filter_detail["include"]:
        log("仅限规则   ：%s" % format_rules(filter_detail["include"]))
    if filter_detail["exclude"]:
        log("排除规则   ：%s" % format_rules(filter_detail["exclude"]))
    if filters is None:
        log("过滤规则   ：无（全部候选文件都会处理）")
    log("切割方式   ：ffmpeg 无损流拷贝（-c copy），每段可独立播放")
    log("ffmpeg     ：%s" % (ffmpeg or "未找到"))
    if ffprobe and DEBUG_MODE:
        log("ffprobe    ：%s" % ffprobe)
    log("子目录     ：%s" % ("否" if args.no_recursive else "是"))
    log("元数据     ：%s" % ("保留（容器标签 + 文件时间戳）"
                            if args.keep_metadata else "不保留容器标签"))
    if args.outdir:
        log("输出目录   ：%s" % Path(args.outdir).resolve())
    if args.delete_source:
        log("原文件处理 ：切割成功后删除")
    elif args.mark_source == "rename":
        log("原文件处理 ：改名为 原名#origin.扩展名")
    elif args.mark_source == "move":
        log("原文件处理 ：移动到 %s/ 文件夹" % args.source_dir)
    else:
        log("原文件处理 ：保持原位")
    if args.dry_run:
        log("预览模式   ：不会写入任何文件")
    if DEBUG_MODE:
        log("调试模式   ：开启（输出 ffmpeg 完整命令与流布局）")
    if not (ffmpeg and ffprobe):
        log("错误       ：未找到 ffmpeg/ffprobe，无法切割。")
        log("             先装 ffmpeg（可加 --install-ffmpeg），或重跑时去掉 --no-install-ffmpeg。")
        log("-" * 68)
        return 2
    log("-" * 68)

    if DEBUG_MODE:
        dbg("Python %s (%s)" % (platform.python_version(), sys.executable))
        for name, path_ in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
            if not path_:
                continue
            try:
                r = subprocess.run([path_, "-version"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=30)
                first = (r.stdout or "").splitlines()[:1]
                if first:
                    dbg("%s 版本：%s" % (name, first[0]))
            except Exception as exc:          # noqa: BLE001
                dbg("%s 版本读取失败：%s" % (name, exc))

    # 归档文件夹里的都是已经切过的原片，不再重复处理。
    # 要排除的是「当前归档目录 + 历史上用过的归档目录名」：早先用
    # --source-dir origin 归档过去的原片保留着原文件名，换个目录名之后
    # 它们看起来就是普通新视频，只排除当前那个会被整个重切一遍。
    archive_dirs = {args.source_dir} | set(LEGACY_SOURCE_DIRS)
    files, skipped = collect_files(folders, exts, not args.no_recursive,
                                   archive_dirs, filters, min_size)

    # 被过滤规则挡下的文件要说清楚挡在哪，否则用户只会看到「0 个视频」，
    # 然后开始怀疑扫描坏了（网页版的「跳过明细」就是干这个的）
    if skipped:
        log("按过滤规则跳过 %d 个文件（%s）"
            % (sum(skipped.values()), _summarize_counts(skipped)))
    if not files:
        log("没有找到任何视频文件。")
        return 0

    targets = []
    for f in files:
        try:
            sz = f.stat().st_size
        except Exception:
            continue
        if args.all or sz > threshold:
            targets.append((f, sz))

    if args.all:
        log("扫描到 %d 个视频文件，--all 已开启：全部处理，不按大小筛选。" % len(files))
    else:
        log("扫描到 %d 个视频文件，其中 %d 个超过阈值需要处理。"
            % (len(files), len(targets)))
    if not targets:
        log("没有需要处理的文件，结束。")
        if args.seconds and files:
            log("")
            log("提示：你用的是按时间切分，但没有任何文件超过大小阈值 %s。" % args.size)
            log("      若想让「每 %g 秒一段」对所有视频生效（不受体积限制），" % args.seconds)
            log("      加 --all 重跑即可。")
        return 0

    outdir_root = Path(args.outdir).resolve() if args.outdir else None
    if outdir_root:
        outdir_root.mkdir(parents=True, exist_ok=True)

    if args.delete_source and not args.yes:
        log("")
        log("!! 已开启 --delete-source：切割成功后会删除原文件。")
        ans = input("确认删除原文件？输入 yes 继续，其它任意键取消：").strip().lower()
        if ans != "yes":
            log("已取消。")
            return 0

    if not args.dry_run and not args.yes:
        log("")
        ans = input("确认开始切割？[Y/n]：").strip().lower()
        if ans == "n":
            log("已取消。")
            return 0

    log("")
    ok_count = fail_count = 0
    total_bytes = 0
    total_out_bytes = 0
    total_parts = 0

    for idx, (src, size) in enumerate(targets, start=1):
        outdir = outdir_root if outdir_root else src.parent
        log("[%d/%d] %s" % (idx, len(targets), src.name))
        log("       原大小：%s" % human_size(size))

        t0 = time.time()
        try:
            produced, _ = split_by_ffmpeg(
                src, threshold, outdir, args.dry_run,
                args.overwrite, ffmpeg, ffprobe, args.keep_metadata,
                seg_seconds=args.seconds)

            for path, w in produced:
                if not args.dry_run:
                    note = copy_timestamps(src, path)
                    dur = probe_duration(path, ffprobe) if ffprobe else 0.0
                    dur_txt = "  %s" % format_duration(dur) if dur else ""
                else:
                    note = "预览"
                    dur_txt = ""
                log("       -> %s  %s%s   [%s]"
                    % (path.name, human_size(w), dur_txt, note))
                total_bytes += w
                total_out_bytes += w
                total_parts += 1

            if not args.dry_run:
                # 核对「各切片时长之和 vs 原片时长」——确认时间轴上没丢内容。
                # 流拷贝按关键帧切分，每段末尾会多包一个 GOP，所以总和略大属正常。
                if ffprobe and len(produced) > 1:
                    d_src = probe_duration(src, ffprobe)
                    d_parts = [probe_duration(p, ffprobe) for p, _ in produced]
                    if d_src > 0 and all(d > 0 for d in d_parts):
                        gap = sum(d_parts) - d_src
                        ok = -1.0 <= gap <= 5.0 + 3.0 * len(d_parts)
                        log("       时长核对：切片合计 %s，原片 %s，差 %+.2f 秒%s"
                            % (format_duration(sum(d_parts)), format_duration(d_src),
                               gap, "（关键帧对齐的正常偏差）" if ok
                               else "  ⚠ 偏差偏大，请用 --debug 复核"))
                log("       完成：%d 段，用时 %.1f 秒" %
                    (len(produced), time.time() - t0))
                if args.delete_source:
                    try:
                        src.unlink()
                        log("       已删除原文件：%s" % src.name)
                    except Exception as del_exc:    # noqa: BLE001
                        log("       注意：切片已生成，但原文件删除失败（%s），"
                            "请手动删除。" % del_exc)
                else:
                    log("       %s" % mark_source(src, args.mark_source, args.source_dir))
            else:
                if args.delete_source:
                    log("       （预览）切割后原文件将被删除")
                elif args.mark_source == "rename":
                    log("       （预览）原文件将标记为 %s#origin%s" % (src.stem, src.suffix))
                elif args.mark_source == "move":
                    log("       （预览）原文件将移至 %s/" % args.source_dir)
            ok_count += 1

        except Exception as exc:              # noqa: BLE001
            fail_count += 1
            log("       失败：%s" % exc)
        log("")

    log("=" * 68)
    log("处理完成：成功 %d 个文件，失败 %d 个；共产生 %d 段，%s %s。"
        % (ok_count, fail_count, total_parts,
           "预计产生" if args.dry_run else "共产生", human_size(total_bytes)))
    if ok_count and not args.dry_run:
        log("切分方式   ：ffmpeg 流拷贝（-c copy），未重新编码，画质音质无损")
        log("每段可播放 ：是（可单独双击）")
    log("=" * 68)
    if LOG_PATH:
        log("")
        log("★ 以上完整输出已保存到日志文件，随时可打开复制：")
        log("  %s" % LOG_PATH)
        log("  （不想要日志文件，下次加 --no-log 即可）")
    return 0 if fail_count == 0 else 1


# ---------------------------------------------------------------- 预设
#
# 「预设」= 一套可复用的设置（切分方式、处理格式、过滤规则、输出位置、
# 原片处理…），存在程序旁边的 presets.json 里。
#
# 刻意**不含文件夹路径**：预设回答的是「这套参数怎么用」，而每次要处理的
# 目录几乎都不一样。把路径也存进去，换个目录就得再存一份，预设反而成了负担。
# 也**不含「先预览」**：那是每次运行临时的决定，不是一套固定的设置。

PRESETS_FILE = "presets.json"

# 存进预设的字段。以后加新的设置项，记得同步这里，否则它存不进预设。
PRESET_KEYS = (
    "threshold", "seconds", "all", "ext", "min_size",
    "name_include", "name_exclude", "ext_exclude",
    "outdir", "overwrite", "keep_metadata",
    "mark_source", "source_dir", "recursive", "debug",
)


def presets_path() -> Path:
    """预设文件就在程序旁边 —— 和日志、自装的 ffmpeg 同一套规则，
    整个工具始终是「一个目录，拷走就能用」。"""
    return SCRIPT_DIR / PRESETS_FILE


def load_presets() -> dict:
    """读全部预设。文件不存在或损坏都返回空表。

    它只是锦上添花，不该因为一个坏文件就把主流程拦住；坏文件留在原地供
    人工排查，不自动删也不自动覆盖。
    """
    try:
        with open(presets_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    items = data.get("presets") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return {}
    return {k: v for k, v in items.items() if isinstance(v, dict) and k}


def save_presets(items: dict) -> bool:
    """原子写盘：先写临时文件再 replace，中途失败不会把原文件写坏。"""
    path = presets_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "presets": items}, fh,
                      ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log("预设保存失败：%s" % exc)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def preset_from_config(cfg: dict) -> dict:
    """从向导配置里挑出该存进预设的字段。"""
    return {k: cfg[k] for k in PRESET_KEYS if k in cfg}


def describe_preset(item: dict) -> str:
    """把一套预设压成一行摘要，列表里好认。"""
    if item.get("seconds"):
        how = "每 %g 秒一段" % float(item["seconds"])
    else:
        how = "每片不超过 %s" % (item.get("threshold") or DEFAULT_THRESHOLD)
    bits = [how, describe_mark(item.get("mark_source") or "rename",
                               item.get("source_dir") or DEFAULT_SOURCE_DIR)]
    if item.get("outdir"):
        bits.append("输出到 %s" % item["outdir"])
    if item.get("name_include") or item.get("name_exclude"):
        bits.append("有过滤规则")
    if item.get("recursive") is False:
        bits.append("不含子目录")
    return "，".join(bits)


def apply_preset_to_args(args, item: dict) -> None:
    """把预设里的设置写回 args。

    **命令行上显式给的参数优先于预设**：预设是省事的默认值，不是枷锁 ——
    用户敲了 --size 2G 就该按 2G 走，不该被预设里的 3.9G 悄悄盖掉。
    判断「显式给过没有」的办法是跟一份全默认的解析结果比：argparse 不保留
    「这个值是默认值还是用户敲的」这个信息，只能这样反推。
    """
    base = build_parser().parse_args([])

    def untouched(name: str) -> bool:
        return getattr(args, name) == getattr(base, name)

    if untouched("size") and item.get("threshold"):
        args.size = item["threshold"]
    if untouched("seconds") and item.get("seconds") is not None:
        try:
            args.seconds = float(item["seconds"])
        except (TypeError, ValueError):
            pass
    if untouched("all"):
        args.all = bool(item.get("all"))
    if untouched("ext") and item.get("ext"):
        args.ext = item["ext"]
    if untouched("min_size") and item.get("min_size"):
        args.min_size = item["min_size"]
    if untouched("name_include") and item.get("name_include"):
        args.name_include = list(item["name_include"])
    if untouched("name_exclude") and item.get("name_exclude"):
        args.name_exclude = list(item["name_exclude"])
    if untouched("ext_exclude") and item.get("ext_exclude"):
        args.ext_exclude = list(item["ext_exclude"])
    if untouched("outdir") and item.get("outdir"):
        args.outdir = item["outdir"]
    if untouched("overwrite"):
        args.overwrite = bool(item.get("overwrite"))
    if untouched("keep_metadata"):
        args.keep_metadata = bool(item.get("keep_metadata", True))
    if untouched("no_recursive"):
        args.no_recursive = not bool(item.get("recursive", True))
    if untouched("debug"):
        args.debug = bool(item.get("debug"))
    if item.get("source_dir") and untouched("source_dir"):
        args.source_dir = item["source_dir"]

    mark = item.get("mark_source")
    if untouched("mark_source") and not args.delete_source:
        if mark == "delete":
            args.delete_source = True
            args.mark_source = "none"
        elif mark in ("rename", "move", "none"):
            args.mark_source = mark


def handle_preset_actions(args):
    """处理 --list-presets / --delete-preset / --preset。

    返回退出码 = 「这是个独立动作，做完就结束」；返回 None = 继续正常流程。
    """
    if args.list_presets:
        items = load_presets()
        if not items:
            log("还没有保存过任何预设。")
            log("跑一次交互向导，选完设置后它会问你要不要存成一个。")
        else:
            log("已保存的预设（%s）：" % presets_path())
            for name in sorted(items):
                log("  * %-18s %s" % (name, describe_preset(items[name])))
        return 0

    if args.delete_preset:
        items = load_presets()
        if args.delete_preset not in items:
            log("没有名为「%s」的预设。现有：%s"
                % (args.delete_preset, "、".join(sorted(items)) or "（无）"))
            return 1
        del items[args.delete_preset]
        if save_presets(items):
            log("已删除预设「%s」。" % args.delete_preset)
        return 0

    if args.preset:
        items = load_presets()
        if args.preset not in items:
            log("没有名为「%s」的预设。现有：%s"
                % (args.preset, "、".join(sorted(items)) or "（无）"))
            return 2
        apply_preset_to_args(args, items[args.preset])
        # 告诉向导「已经选过预设了」，别再问一遍用哪个
        args.preset_loaded = args.preset

    if args.save_preset:
        # 和 --list-presets / --delete-preset 一样是「独立动作，做完就退出」：
        # 用户只是想把这套设置记下来，没有要现在切视频。放在 --preset 之后，
        # 是为了让 `--preset A --save-preset B` 能存成 A 的内容。
        items = load_presets()
        existed = args.save_preset in items
        items[args.save_preset] = preset_from_config(blank_config(args))
        if save_presets(items):
            log("已%s预设「%s」到 %s"
                % ("更新" if existed else "保存",
                   args.save_preset, presets_path()))
            log("以后加 --preset %s 就能直接套用，不带参数跑则进向导挑选。"
                % args.save_preset)
        return 0
    return None


# ---------------------------------------------------------------- 交互式向导

class WizardCancelled(Exception):
    """问答向导里用户要求取消（输入 q，或输入流已经结束）。"""


# 向导运行期间置 True：让 ask() 能识别「q = 取消」，并在没有可交互终端时
# 及时收手，而不是反复弹文件夹选择框、或者死循环问下去。
WIZARD_ACTIVE = False


def ask(prompt: str, default: str = "") -> str:
    """读一行输入；遇到 EOF（管道/无终端）时返回默认值。"""
    try:
        raw = input(prompt)
    except EOFError:
        log("")
        if WIZARD_ACTIVE:
            raise WizardCancelled()
        return default
    raw = raw.strip()
    if WIZARD_ACTIVE and raw.lower() == "q":
        raise WizardCancelled()
    return raw if raw else default


def ask_menu(title: str, choices, default: str) -> str:
    """编号菜单。choices 为 [(键, 主文本, 说明)]，返回选中的键。"""
    keys = [str(c[0]) for c in choices]
    while True:
        log("")
        log(title)
        for key, label, note in choices:
            log("    %s) %s —— %s" % (key, label, note))
        raw = ask("    请选择 [%s]：" % default, default)
        if raw in keys:
            return raw
        log("    输入无效，可选：%s" % "/".join(keys))


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    """统一的 y/n 提问：直接回车用默认值。"""
    mark = "[Y/n]" if default else "[y/N]"
    raw = ask("%s %s：" % (prompt, mark), "y" if default else "n").strip().lower()
    return raw in ("y", "yes")


def stdin_is_interactive() -> bool:
    """标准输入是否连着真人。管道 / 重定向 / 无终端时返回 False。

    用来区分「用户敲了回车」和「输入流已经读完、ask() 拿默认值兜底」——
    后者绝不能当成用户同意。
    """
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:                                          # noqa: BLE001
        return False


def parse_paths(text: str):
    """把一行输入拆成多个路径，兼容拖拽产生的引号。"""
    try:
        parts = shlex.split(text, posix=False)
    except ValueError:
        parts = text.split()
    out = []
    for item in parts:
        item = item.strip().strip('"').strip("'").strip()
        if item:
            out.append(item)
    return out


def describe_mark(mark_source: str, source_dir_name: str) -> str:
    return {
        "rename": "改名为 原名#origin.扩展名",
        "move": "移动到 %s/ 文件夹" % source_dir_name,
        "none": "保持原位",
        "delete": "切割成功后删除",
    }.get(mark_source, mark_source)


def rules_from_args(values) -> list:
    """把命令行里的规则参数（可重复、每条可含逗号）摊平成规则列表。"""
    return parse_rules([t for raw in (values or []) for t in split_rule_text(raw)])[0]


def ask_rules(title: str, hint: str, current: str = "") -> list:
    """逐行收集一组过滤规则，返回**原始文本行**列表。

    一行里可以用逗号写多条（中英文逗号都认）；直接回车结束。
    返回原始行而不是拆开的结果：调用方（resolve_filters）还会再拆一次，
    这里若先拆、那边再拆，正则里的转义逗号会被拆坏两次。
    正则编不过会当场报错让你重写，而不是留到扫描时才静默失效。
    """
    log("")
    log(title)
    log(hint)
    if current:
        log("    当前：%s" % current)
    lines = []
    while True:
        raw = ask("    规则 %d（回车结束）：" % (len(lines) + 1))
        if not raw:
            break
        parsed, errors = parse_rules(split_rule_text(raw))
        if errors:
            log("    %s" % errors[0])
            continue
        if not parsed:
            continue
        lines.append(raw)
        log("    已加入：%s" % format_rules(parsed))
    return lines


def ask_ext_selection(default_text: str) -> str:
    """选要处理的格式，返回逗号分隔的扩展名文本（喂给 args.ext）。"""
    all_exts = list(DEFAULT_EXTS)
    current = tuple(e.strip().lower() for e in str(default_text or "").split(",")
                    if e.strip())
    log("")
    log("[3/8] 处理哪些格式")
    log("    本工具只能无损切分下面 %d 种容器（其它格式切了也播不了）：" % len(all_exts))
    for idx, ext in enumerate(all_exts, 1):
        log("      %d) %-7s%s" % (idx, ext, "  ← 当前已选" if ext in current else ""))
    log("    输入序号（逗号分隔，如 1,3）只保留这几种；直接回车 = 全部 %d 种。"
        % len(all_exts))
    while True:
        raw = ask("    序号：")
        if not raw:
            return ",".join(all_exts)
        picked, bad = [], []
        for token in split_rule_text(raw):
            if token.isdigit() and 1 <= int(token) <= len(all_exts):
                ext = all_exts[int(token) - 1]
                if ext not in picked:
                    picked.append(ext)
            else:
                bad.append(token)
        if bad:
            log("    无法识别的序号：%s" % "、".join(bad))
            continue
        if picked:
            return ",".join(picked)
        log("    至少选一种格式。")


def ask_source_dir(default: str) -> str:
    """问归档文件夹名，只接受单层目录名（语义就是「当前文件夹下的某个子文件夹」）。"""
    log("")
    log("    归档文件夹名：原片会移到这里面，扫描时会自动跳过它")
    while True:
        raw = ask("    文件夹名 [%s]：" % default, default).strip()
        name = raw.replace("\\", "/").strip("/")
        if "/" in name:
            name = name.rsplit("/", 1)[-1].strip()
        if name and name not in (".", ".."):
            return name
        log("    只能填单层文件夹名，请重填。")


def ask_outdir(default: str = "") -> str:
    """问一个输出目录；回车 = 返回空串（表示「和原片同目录」）。"""
    while True:
        raw = ask("    输出目录（直接回车 = 还是放原片旁边）：", default)
        if not raw:
            return ""
        p = Path(raw).expanduser()
        if p.is_dir():
            return str(p)
        if not ask_yes_no("    目录不存在，现在创建", default=True):
            continue
        try:
            p.mkdir(parents=True, exist_ok=True)
            return str(p)
        except OSError as exc:
            log("    创建失败：%s" % exc)


# ------------------------------------------------------- 向导的各步（拆开是为了
# 让「套用预设」能跳过它们 —— 预设已经有了这些答案，再问一遍就没意义了）

def blank_config(args) -> dict:
    """向导配置的初始值：全部来自命令行参数（没给就是各自的内置默认）。

    这样「命令行给一半、向导补一半」是自然成立的 —— 每一步回车就是沿用
    命令行上已经给的值；预设也是通过先把值写回 args 再走这条路进来的。
    """
    return {
        "folders": [],
        "threshold": args.size,
        "seconds": args.seconds,
        "all": bool(args.all),
        "ext": args.ext,
        "min_size": args.min_size or "0",
        "name_include": list(args.name_include or []),
        "name_exclude": list(args.name_exclude or []),
        "ext_exclude": list(args.ext_exclude or []),
        "outdir": args.outdir or "",
        "overwrite": bool(args.overwrite),
        "keep_metadata": bool(args.keep_metadata),
        "mark_source": "delete" if args.delete_source else args.mark_source,
        "source_dir": args.source_dir,
        "recursive": not args.no_recursive,
        "preview": bool(args.dry_run),
        "debug": bool(args.debug),
    }


def choose_preset() -> tuple:
    """向导开头问一句要不要套用预设。返回 (名字, 预设内容)；不用则 (None, None)。"""
    items = load_presets()
    if not items:
        return None, None

    names = sorted(items)
    log("")
    log("-" * 68)
    log("检测到 %d 个已保存的预设：" % len(names))
    for idx, name in enumerate(names, 1):
        log("    %d) %s —— %s" % (idx, name, describe_preset(items[name])))
    log("    直接回车 = 不用预设，从头逐项问")
    log("-" * 68)

    while True:
        raw = ask("    选择 [回车跳过]：").strip()
        if not raw:
            return None, None
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            name = names[int(raw) - 1]
            return name, items[name]
        for name in names:          # 也允许直接敲名字，省得回去数序号
            if raw == name:
                return name, items[name]
        log("    没有这个预设，请输入序号或预设名。")


def ask_folders() -> list:
    """第 1 步：要处理的文件夹。这一步**预设不代替**——每次要处理的目录几乎都不同。"""
    folders = []
    while not folders:
        log("")
        log("[1/8] 要处理的文件夹")
        log("    把文件夹拖进窗口，或粘贴路径（多个用空格分隔）")
        log("    直接回车 -> 弹出图形化选择框")
        raw = ask("    路径：")
        if not raw:
            picked, why = pick_folder_gui()
            if picked:
                folders = [picked]
                break
            if why:
                log("    %s" % why)
            log("    请手动输入路径（也可以把文件夹直接拖进窗口）。")
            continue
        cands = parse_paths(raw)
        valid = [c for c in cands if Path(c).is_dir()]
        bad = [c for c in cands if c not in valid]
        if bad:
            log("    以下路径不是文件夹，已忽略：%s" % "、".join(bad))
        folders = valid
    log("    已选择：%s" % "、".join(str(f) for f in folders))
    return folders


def ask_split_mode(cfg: dict) -> dict:
    """第 2 步：按大小切还是按时间切。"""
    log("")
    if cfg["seconds"]:
        log("（当前默认「按时间」，来自命令行或预设）")
    key = ask_menu("[2/8] 按什么切分", [
        ("1", "按大小", "每片体积不超过阈值（适合「每片都要小于 4G」这类硬限制）"),
        ("2", "按时间", "每片固定秒数，例如每 5 分钟一段（片段时长整齐）"),
    ], "2" if cfg["seconds"] else "1")

    preset_size = {"1": "3.9G", "2": "3.5G", "3": "2G"}
    patch = {"all": cfg["all"], "seconds": None}
    if key == "1":
        # 按大小：先选体积上限，脚本把整条时间轴均分成若干段
        size_default = "4"
        for pk, pv in preset_size.items():
            if parse_size(cfg["threshold"]) == parse_size(pv):
                size_default = pk
                break
        key2 = ask_menu("    每片最大体积", [
            ("1", "3.9G", "FAT32 安全值（推荐）"),
            ("2", "3.5G", "更保守，兼容多数网盘"),
            ("3", "2G", "微信、邮件更稳"),
            ("4", "自定义", "例如 1.5G、800M"),
        ], size_default)
        if key2 in preset_size:
            patch["threshold"] = preset_size[key2]
        else:
            while True:
                raw = ask("    请输入大小（如 1.5G / 800M）[%s]：" % cfg["threshold"],
                          cfg["threshold"])
                try:
                    parse_size(raw)
                    patch["threshold"] = raw
                    break
                except ValueError as exc:
                    log("    %s" % exc)
        return patch

    # 按时间：先选每片秒数。大小阈值仍保留，用来挑文件、并兜底防止
    # 某片因为码率波动而超出体积上限。
    preset_t = [("1", 60.0, "1 分钟"), ("2", 180.0, "3 分钟"),
                ("3", 300.0, "5 分钟"), ("4", 600.0, "10 分钟")]
    t_default = "3"
    if cfg["seconds"]:
        for k2, v2, _ in preset_t:
            if abs(cfg["seconds"] - v2) < 0.01:
                t_default = k2
                break
        else:
            t_default = "5"
    key2 = ask_menu("    每片多长", [
        ("1", "1 分钟", "60 秒"),
        ("2", "3 分钟", "180 秒"),
        ("3", "5 分钟", "300 秒（推荐）"),
        ("4", "10 分钟", "600 秒"),
        ("5", "自定义", "直接输入秒数"),
    ], t_default)
    if key2 in [k for k, _, _ in preset_t]:
        patch["seconds"] = dict((k, v) for k, v, _ in preset_t)[key2]
    else:
        while True:
            raw = ask("    请输入每片秒数（如 240）：",
                      "%g" % cfg["seconds"] if cfg["seconds"] else "")
            try:
                seconds = float(raw)
                if seconds <= 0:
                    raise ValueError("秒数必须大于 0")
                patch["seconds"] = seconds
                break
            except ValueError as exc:
                log("    %s" % exc)

    log("")
    log("    每片目标时长：%.0f 秒（%.1f 分钟）"
        % (patch["seconds"], patch["seconds"] / 60.0))
    log("    体积上限仍为 %s：切出来若有片段超过它，会自动缩小秒数重试。"
        % cfg["threshold"])
    log("")
    log("    默认只切「体积超过 %s」的视频。若想让每段固定时长对" % cfg["threshold"])
    log("    所有视频都生效（不管文件大小），请选 y。")
    patch["all"] = ask_yes_no("    对所有视频切分（不只看超大文件）", default=cfg["all"])
    return patch


def ask_formats(cfg: dict) -> dict:
    """第 3 步：处理哪些格式 + 最小体积。"""
    patch = {"ext": ask_ext_selection(cfg["ext"])}
    log("")
    log("    最小体积：比它小的文件一律不看（如 100M / 500K）。直接回车 = 不限。")
    while True:
        raw = ask("    最小体积 [%s]：" % (cfg["min_size"] or "0"),
                  cfg["min_size"] or "0").strip()
        if raw in ("", "0"):
            patch["min_size"] = "0"
            return patch
        try:
            parse_size(raw)
            patch["min_size"] = raw
            return patch
        except ValueError as exc:
            log("    %s" % exc)


def ask_filters(cfg: dict) -> dict:
    """第 4 步：过滤规则 —— 与网页版「监控目录 → 过滤规则」同一套口径。"""
    log("")
    log("[4/8] 过滤规则（按文件名 / 文件夹名）")
    log("    比对的是文件 / 文件夹的完整名字（含扩展名），以及该文件到所选目录")
    log("    之间各级文件夹的名字。「包含」不区分大小写；以 re: 开头按正则。")
    log("    **排除优先于仅限**：两边都命中时一律排除。留空 = 不设这类规则。")
    name_include = ask_rules(
        "    「仅限」：只处理命中的文件",
        "    例：相机,re:^DJI_\\d{4}\\.mp4$（正则里要写逗号就用 \\, 转义）",
        format_rules(rules_from_args(cfg["name_include"])))
    name_exclude = ask_rules(
        "    「排除」：命中的一律不处理",
        "    例：_proxy,re:\\.bak$",
        format_rules(rules_from_args(cfg["name_exclude"])))
    log("")
    log("    还可以排除某些文件类型（比如不想要 .ts）。直接回车 = 不排除。")
    ext_exclude = ask("    排除格式（如 ts,webm）：").strip()
    return {
        "name_include": name_include,
        "name_exclude": name_exclude,
        "ext_exclude": [ext_exclude] if ext_exclude else [],
    }


def ask_outdir_step(cfg: dict) -> dict:
    """第 5 步：切片放在哪。"""
    log("")
    mode = ask_menu("[5/8] 切片放在哪", [
        ("1", "和原片同目录", "切片落在原片旁边（默认）"),
        ("2", "指定一个目录", "所有切片集中放，原片留在原地"),
    ], "2" if cfg["outdir"] else "1")
    return {"outdir": ask_outdir(cfg["outdir"]) if mode == "2" else ""}


def ask_mark_step(cfg: dict) -> dict:
    """第 6 步：原文件怎么处理。"""
    default_key = {"rename": "1", "move": "2", "none": "3", "delete": "4"}.get(
        cfg["mark_source"], "1")
    key = ask_menu("[6/8] 切割完成后，原文件怎么处理", [
        ("1", "改名", "原名#origin.扩展名（推荐，一眼分得清原片和切片）"),
        ("2", "移动", "移到单独的归档文件夹"),
        ("3", "不动", "保持原样（⚠ 原片没被标记，下次扫描可能再切一遍）"),
        ("4", "删除", "切割成功后删除原文件（不可恢复）"),
    ], default_key)
    mark_source = {"1": "rename", "2": "move", "3": "none", "4": "delete"}[key]
    source_dir = (ask_source_dir(cfg["source_dir"]) if mark_source == "move"
                  else cfg["source_dir"])
    if mark_source == "delete":
        log("")
        log("    ！！切割成功后会删除原文件，此操作无法撤销。")
        if not ask_yes_no("    确认删除原文件", default=False):
            log("    已自动改为：改名（#origin）")
            mark_source = "rename"
    return {"mark_source": mark_source, "source_dir": source_dir}


def ask_misc(cfg: dict) -> dict:
    """第 7 步：其它选项。"""
    log("")
    log("[7/8] 其它选项")
    return {
        "recursive": ask_yes_no("    包含子文件夹", default=cfg["recursive"]),
        "overwrite": ask_yes_no("    目标切片已存在时直接覆盖",
                                default=cfg["overwrite"]),
        "keep_metadata": ask_yes_no("    保留元数据（容器标签 + 文件时间戳）",
                                    default=cfg["keep_metadata"]),
    }


def ask_preview_debug(cfg: dict) -> dict:
    """第 8 步：预览与调试。"""
    log("")
    preview = ask_yes_no("[8/8] 先预览一遍（不写任何文件）", default=cfg["preview"])
    log("")
    log("    是否输出详细调试信息？")
    log("    会额外打印 ffmpeg 路径与版本、源文件流布局、容器标签、")
    log("    ffmpeg 完整命令与返回码、每段时长码率明细。排查问题很有用。")
    debug = ask_yes_no("    输出详细调试信息", default=cfg["debug"])
    return {"preview": preview, "debug": debug}


def show_summary(cfg: dict) -> None:
    """把即将执行的全部设置打出来给用户过目。"""
    inc = rules_from_args(cfg["name_include"])
    exc = rules_from_args(cfg["name_exclude"])
    ext_exclude = [e if e.startswith(".") else "." + e
                   for e in (t.lower() for raw in cfg["ext_exclude"]
                             for t in split_rule_text(raw))]

    if cfg["seconds"]:
        split_text = "按时间：每片 %.0f 秒（%.1f 分钟）" % (
            cfg["seconds"], cfg["seconds"] / 60.0)
    else:
        split_text = "按大小：每片不超过 %s" % cfg["threshold"]

    log("")
    log("-" * 68)
    log("请确认设置")
    log("    文件夹     ：%s" % "、".join(str(f) for f in cfg["folders"]))
    log("    切分方式   ：%s" % split_text)
    log("    处理范围   ：%s" % ("全部视频文件（不按大小筛选）" if cfg["all"]
                                else "仅体积超过 %s 的文件" % cfg["threshold"]))
    log("    处理格式   ：%s" % str(cfg["ext"]).replace(",", " "))
    if ext_exclude:
        log("    排除格式   ：%s" % " ".join(ext_exclude))
    log("    最小体积   ：%s" % ("不限" if str(cfg["min_size"]) == "0"
                                else cfg["min_size"]))
    log("    仅限规则   ：%s" % format_rules(inc))
    log("    排除规则   ：%s" % format_rules(exc))
    log("    切割方式   ：ffmpeg 无损流拷贝（每段可独立播放）")
    log("    输出位置   ：%s" % (cfg["outdir"] or "和原片同目录"))
    log("    原文件处理 ：%s" % describe_mark(cfg["mark_source"], cfg["source_dir"]))
    log("    子目录     ：%s" % ("包含" if cfg["recursive"] else "不包含"))
    log("    覆盖已有   ：%s" % ("是" if cfg["overwrite"] else "否"))
    log("    保留元数据 ：%s" % ("是" if cfg["keep_metadata"] else "否"))
    log("    先预览     ：%s" % ("是" if cfg["preview"] else "否"))
    log("    调试信息   ：%s" % ("输出" if cfg["debug"] else "不输出"))
    log("-" * 68)


def maybe_save_preset(cfg: dict, loaded_name: str = None) -> None:
    """问一句要不要把这套设置存成预设。

    问的时机是「选完所有选项、确认设置之后，真正开始切割之前」：这时候参数
    还在眼前，印象最清楚；等切完再问，用户已经在等结果了，多半随手回车跳过。
    """
    items = load_presets()

    if loaded_name:
        # 套用预设进来的：问「更新它」比问「另存一份」更贴近真实意图
        if ask_yes_no("用当前这套设置更新预设「%s」" % loaded_name, default=True):
            items[loaded_name] = preset_from_config(cfg)
            if save_presets(items):
                log("已更新预设「%s」。" % loaded_name)
        return

    if not ask_yes_no("把这套设置保存成预设，下次直接复用", default=False):
        return

    while True:
        name = ask("    预设名（如「无人机素材」）：").strip()
        if not name:
            log("    名字为空，已跳过保存。")
            return
        if name in items and not ask_yes_no("    已有同名预设，覆盖它", default=False):
            continue
        items[name] = preset_from_config(cfg)
        if save_presets(items):
            log("已保存预设「%s」到 %s" % (name, presets_path()))
        return


def interactive_wizard(args):
    """问答式收集配置。返回配置 dict；用户取消则返回 None。"""
    global WIZARD_ACTIVE
    WIZARD_ACTIVE = True
    try:
        return _wizard_impl(args)
    except WizardCancelled:
        log("已取消，未做任何改动。")
        return None
    finally:
        WIZARD_ACTIVE = False


def _wizard_impl(args):
    """问答式收集配置的实际实现，取消由 interactive_wizard 统一接住。

    8 步的划分对应网页版的两处设置：「设置 → 切分参数」（第 2、3、5、6 步）
    与「监控目录 → 过滤规则 / 原片处理」（第 4 步、第 6 步）。这样用户在两边
    看到的是同一套选项，不会出现「网页上有、命令行里找不到」的落差。

    套用预设时只走第 1 步（文件夹）然后直接到汇总页 —— 其余 7 步的答案预设里
    已经有了，再问一遍就失去预设的意义了。
    """
    global WIZARD_ACTIVE

    log("")
    log("=" * 68)
    log("视频无损分割工具 · 交互模式")
    log("=" * 68)

    loaded = getattr(args, "preset_loaded", None)
    if loaded:
        # 命令行上已经 --preset 指定过了，别再问一次用哪个
        preset_name, preset_item = loaded, None
    else:
        log("共 8 步，覆盖网页版「设置 + 监控目录」里的全部可调项。")
        log("每一步直接回车即可使用推荐值，输入 q 可随时取消。")
        preset_name, preset_item = choose_preset()

    if preset_item:
        apply_preset_to_args(args, preset_item)

    folders = ask_folders()
    cfg = blank_config(args)
    cfg["folders"] = folders

    if preset_name:
        log("")
        log("已套用预设「%s」，跳过其余提问；确认下面这张表即可开始。" % preset_name)
    else:
        for step in (ask_split_mode, ask_formats, ask_filters,
                     ask_outdir_step, ask_mark_step, ask_misc, ask_preview_debug):
            cfg.update(step(cfg))

    show_summary(cfg)
    log("    回车 = 按上面设置开始    q = 取消")
    while True:
        raw = ask("    请确认 [回车]：").strip().lower()
        if raw == "q":
            log("已取消。")
            return None
        if raw in ("", "y", "yes"):
            break
        log("    输入无效，直接回车开始，或输入 q 取消。")

    # 设置已经确认，后面问「要不要存预设」时不能再让一个 q 把整件事取消掉 ——
    # 用户此时已经明确要跑了，中途退出会让人以为白填了一遍。
    WIZARD_ACTIVE = False
    maybe_save_preset(cfg, loaded_name=preset_name)

    return cfg



# ---------------------------------------------------------------- 入口

def close_log() -> None:
    global LOG_HANDLE
    if LOG_HANDLE is not None:
        try:
            LOG_HANDLE.close()
        except Exception:
            pass
        LOG_HANDLE = None


# ---------------------------------------------------------------- 结束前停窗
#
# 双击 exe 时控制台窗口在程序退出的一瞬间就没了，用户根本来不及看结果，
# 所以要在结束前停一下等回车。但从终端里跑的时候停一下只会碍事，
# 于是需要判断「这次是不是双击启动的」。


class _PROCESSENTRY32W(ctypes.Structure):
    """CreateToolhelp32Snapshot 用的进程条目。字段顺序必须和 winbase.h 一致。"""

    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        # ULONG_PTR：64 位下是 8 字节，用 POINTER(c_ulong) 才能对上
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


_TH32CS_SNAPPROCESS = 0x00000002


def process_table() -> dict:
    """一次快照拿到 {pid: (父pid, exe 文件名)}，失败返回空字典。"""
    if not IS_WINDOWS:
        return {}
    k32 = ctypes.windll.kernel32
    # 64 位下句柄是指针宽度，不声明 restype 会被截断成 int32 而出错
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    k32.Process32FirstW.argtypes = [ctypes.c_void_p,
                                    ctypes.POINTER(_PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [ctypes.c_void_p,
                                   ctypes.POINTER(_PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]

    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return {}
    table = {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID,
                                          entry.szExeFile)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    except Exception:                                          # noqa: BLE001
        return table
    finally:
        try:
            k32.CloseHandle(snap)
        except Exception:                                      # noqa: BLE001
            pass
    return table


def ancestor_names(limit: int = 12) -> list:
    """从自己往上找「真正把本程序启动起来的那一层」，返回沿途进程名。

    会跳过和自己同名的进程：PyInstaller onefile 打出来的可执行文件是
    「引导进程 + 真身进程」两个，它们同名，只有跳过才能看到真正的调用者。
    """
    table = process_table()
    if not table:
        return []
    own = Path(sys.executable).name.lower()
    names, seen, pid = [], set(), os.getpid()
    while pid and pid not in seen and len(names) < limit:
        seen.add(pid)
        entry = table.get(pid)
        if entry is None:
            break
        ppid, name = entry
        if name.lower() != own:
            names.append(name)
        pid = ppid
    return names


def console_process_count() -> int:
    """当前控制台里挂着几个进程。只有自己时返回 1。"""
    try:
        buf = (ctypes.c_uint * 4)()
        return ctypes.windll.kernel32.GetConsoleProcessList(buf, 4)
    except Exception:                                          # noqa: BLE001
        return 0


def launched_by_double_click() -> bool:
    """判断是不是「双击启动」——只有这种情况才需要在结束时停住等用户看完。

    两条判据，命中任一即为双击：

    1. 控制台里只有自己一个进程。双击时 Windows 会新建一个专属控制台，
       里面只有这个程序；而从已有的 cmd / PowerShell / bash 里跑，
       同一个控制台里至少还有那个 shell。

    2. 往上找，第一个不同名的祖先是 explorer.exe。这一条是为了兜住
       PyInstaller onefile：它是「引导进程 + 真身进程」两个同名进程，
       控制台里数是 2，判据 1 会失效，但父进程链仍然指向 explorer。

    输出被重定向（管道 / 写文件）时直接返回 False：那种场景没人在窗口前
    等着，而且 stdin 未必读得动，停下来只会把脚本卡住。
    """
    if not IS_WINDOWS:
        return False
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:                                          # noqa: BLE001
        return False
    if console_process_count() == 1:
        return True
    names = ancestor_names()
    return bool(names) and names[0].lower() == "explorer.exe"


def wait_before_close(code: int, mode: str = "auto") -> int:
    """结束前停住，让用户看完输出再关窗口。

    只在双击启动时自动触发（mode="auto"）：从终端跑的话历史输出本来就翻得到，
    再要一次回车只会碍事。--pause / --no-pause 可以强制打开或关掉。
    """
    if mode == "never" or (mode == "auto" and not launched_by_double_click()):
        return code

    log("")
    log("=" * 68)
    if code == 0:
        log("运行结束（成功）。以上是完整输出，日志文件里也留了一份。")
    else:
        log("运行结束，退出码 %d —— 非 0 通常意味着有文件没能切成功。" % code)
        log("失败原因见上面的输出，或日志文件里的「处理失败的文件」一节。")
    log("=" * 68)
    try:
        input("看完后按回车键关闭窗口 ... ")
    except (EOFError, KeyboardInterrupt):
        log("")
    return code


def main(argv=None) -> int:
    global LOG_HANDLE, LOG_PATH, DEBUG_MODE
    init_console()
    args = build_parser().parse_args(argv)
    DEBUG_MODE = bool(args.debug)

    # 日志默认开启。双击启动时控制台窗口容易被关掉，有了日志文件才方便
    # 回头把完整输出翻出来复制；不想要就加 --no-log。
    if not args.no_log:
        target = args.log or "video_splitter_log.txt"
        p = Path(target)
        try:
            LOG_PATH = str(p if p.is_absolute() else SCRIPT_DIR / p)
            LOG_HANDLE = open(LOG_PATH, "a", encoding="utf-8")
            LOG_HANDLE.write("\n" + "=" * 68 + "\n")
            LOG_HANDLE.write("===== 运行时间 %s =====\n"
                             % time.strftime("%Y-%m-%d %H:%M:%S"))
            LOG_HANDLE.write("===== 启动参数 %s\n" % " ".join(sys.argv[1:]))
            LOG_HANDLE.write("=" * 68 + "\n")
            LOG_HANDLE.flush()
        except Exception as exc:              # noqa: BLE001
            LOG_HANDLE = None
            LOG_PATH = None
            print("（无法写入日志文件 %s：%s）" % (target, exc), flush=True)

    # 预设：--list-presets / --delete-preset 是独立动作，做完就退出；
    # --preset 把设置写回 args，后面所有流程（含向导）都用同一套值。
    code = handle_preset_actions(args)
    if code is not None:
        close_log()
        return code

    try:
        threshold = parse_size(args.size)
    except ValueError as exc:
        log("参数错误：%s" % exc)
        close_log()
        return 2

    if args.seconds is not None and args.seconds <= 0:
        log("参数错误：--seconds 必须大于 0（例如 --seconds 300 表示每片 5 分钟）")
        close_log()
        return 2

    folders = [f for f in args.folders if str(f).strip()]
    # 没有给路径（比如双击启动）或显式加了 -i，就进入问答向导
    interactive = bool(args.interactive or not folders)

    # ffmpeg 缺失时先做环境检查：交互场景会问一句要不要自动下载安装
    ffmpeg, ffprobe = ensure_ffmpeg(args, allow_prompt=interactive)

    # 只给 --install-ffmpeg、不给文件夹也没要交互：装完 ffmpeg 就收工
    if args.install_ffmpeg and not folders and not args.interactive:
        log("")
        log("ffmpeg 状态：%s" % (ffmpeg or "未安装成功"))
        close_log()
        return 0 if ffmpeg else 1

    # 没有 ffmpeg 就没有第二条路，直接退出（向导里选不出别的模式）
    if not (ffmpeg and ffprobe):
        log("")
        log("未检测到 ffmpeg/ffprobe，无法切割。")
        log("先安装 ffmpeg（可重跑加 --install-ffmpeg），再运行本工具。")
        close_log()
        return 1

    if interactive:
        cfg = interactive_wizard(args)
        if cfg is None:
            close_log()
            return 0

        folders = cfg["folders"]
        args.size = cfg["threshold"]
        args.seconds = cfg["seconds"]
        args.all = bool(cfg.get("all"))
        args.ext = cfg["ext"]
        args.min_size = cfg["min_size"]
        # 规则以「原始文本行」存回 args，由 resolve_filters 统一拆解一次 ——
        # 向导里为了校验已经拆过，这里再拆一次是幂等的（拆过的行里不会再有
        # 未转义的逗号），但正则里转义过的逗号必须留到最后一步才还原。
        args.name_include = cfg["name_include"]
        args.name_exclude = cfg["name_exclude"]
        args.ext_exclude = cfg["ext_exclude"]
        args.outdir = cfg["outdir"] or None
        args.overwrite = bool(cfg.get("overwrite"))
        args.keep_metadata = bool(cfg.get("keep_metadata", True))
        args.source_dir = cfg["source_dir"]
        args.no_recursive = not cfg["recursive"]
        DEBUG_MODE = bool(cfg.get("debug"))
        if cfg["mark_source"] == "delete":
            args.delete_source = True
            args.mark_source = "none"
        else:
            args.delete_source = False
            args.mark_source = cfg["mark_source"]
        args.yes = True          # 汇总页已经确认过一次，不再重复追问

        try:
            threshold = parse_size(args.size)
        except ValueError as exc:
            log("参数错误：%s" % exc)
            close_log()
            return 2

        if cfg["preview"]:
            args.dry_run = True
            code = run_processing(args, folders, threshold, ffmpeg, ffprobe)
            log("")
            # 预览升级成真切割是不可逆的，所以这里只认「真人敲的回车」：
            # 输入流已经读完（管道 / 重定向）时 ask() 会拿默认值兜底，那种
            # 「回车」不能算同意，否则一次 `... < answers.txt` 就会真的开切。
            if not stdin_is_interactive():
                log("预览到此为止：当前没有可交互终端，不会自动开始切割。")
                log("确认预览结果没问题后，去掉预览选项重跑一次即可正式执行。")
                close_log()
                return code
            if ask_yes_no("预览结束，现在正式执行切割吗", default=True):
                args.dry_run = False
            else:
                log("已取消，未做任何改动。")
                close_log()
                return code

    code = run_processing(args, folders, threshold, ffmpeg, ffprobe)
    close_log()
    return code


if __name__ == "__main__":
    # --pause / --no-pause 要在 main() 之前先扫一遍：main() 可能在任何一步
    # 提前 return（参数错误、用户取消…），而「停不停」必须在最外层统一决定，
    # 否则那几条提前退出的路会一闪而过 —— 恰恰是出错时最需要看清输出的时候。
    _pause_mode = "auto"
    for _arg in sys.argv[1:]:
        if _arg == "--pause":
            _pause_mode = "always"
        elif _arg == "--no-pause":
            _pause_mode = "never"

    try:
        _code = main()
    except KeyboardInterrupt:
        log("\n已中断。")
        close_log()
        _code = 130
    except SystemExit as exc:
        # argparse 报错退出（退出码 2）也走这里
        close_log()
        _code = exc.code if isinstance(exc.code, int) else 0
    except BaseException:
        # 任何没预料到的异常都完整打印 + 写进日志，
        # 免得双击启动时窗口一闪而过、什么都看不到。
        import traceback
        log("")
        log("!! 程序异常退出，下面是完整堆栈（复制这段给开发者即可定位）：")
        log(traceback.format_exc())
        close_log()
        _code = 1

    sys.exit(wait_before_close(_code, _pause_mode))


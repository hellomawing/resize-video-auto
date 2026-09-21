#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video_splitter.py —— 大视频无损分割工具（Windows / macOS / Linux 通用）

作用：
    扫描指定文件夹中的视频文件，把体积超过阈值的文件直接切成若干段，
    全程不重新编码（画质、音质零损失），并尽量保留原文件的
    创建时间 / 修改时间 / 位置信息 / 相机信息等元数据。

两种使用方式：
    交互模式：不带任何路径直接运行（或加 -i），会逐项询问文件夹、分割阈值、
              切割模式、原文件处理方式等，每步回车即用推荐值。
    命令行模式：直接给出路径和参数，适合写进脚本或批处理重复执行。

两种定段方式（决定「每片切多长」）：
    按大小（默认，-s/--size）：把整条时间轴均分成 ceil(文件大小/阈值) 段，
            每段体积尽量接近、且都不超过阈值。适合「每片都必须小于 4G」。
    按时间（-t/--seconds N）：每片固定 N 秒，例如 300 就是每 5 分钟一段。
            片段时长整齐，适合按片段归档或上传。
            若因码率波动导致某片超过体积阈值，会自动缩小秒数重试，
            并在日志里写明实际用的秒数。

两种切割模式（决定「怎么切」）：
    bytes  纯二进制切割：逐字节原样拆分，零依赖、速度极快。
           特点：所有片段按编号顺序拼接即可 100% 还原原文件；
                 缺点是 MP4/MOV 等格式的第 2 段及之后无法单独双击播放
                 （文件头只在第一段里）。
    copy   FFmpeg 流拷贝（-c copy）：按关键帧重新封装，不重新编码。
           特点：每一段都能单独播放；画质音质同样零损失；
                 需要本机已安装 ffmpeg。

    默认 auto：检测到 ffmpeg 就用 copy，否则自动回退到 bytes。

关于 ffmpeg：
    脚本会先在 PATH 里找 ffmpeg，找不到再翻各平台常见安装位置
    （WinGet / Chocolatey / Scoop / Homebrew / /usr/local/bin 等），
    最后还会认脚本目录下自带的 ffmpeg/bin。
    全都没有时，交互模式会问一句「要不要自动下载安装」，
    也可以直接加 --install-ffmpeg。装的是官方下载页推荐的静态构建，
    只解压到脚本目录下的 ffmpeg/ 里，不写系统目录、不改 PATH。

关于大疆（DJI）等运动相机的 MP4（重要）：
    这类文件除了主视频和音频，还塞了 djmd / dbgi / tmcd 三个 data 流和
    一条 mjpeg 缩略图流。mp4 封装器写不了这些流，所以 copy 模式会自动
    跳过它们，只保留主视频+音频，否则 ffmpeg 会直接报
    "Could not find tag for codec none in stream #2" 而整体失败。
    这些附加流是相机自己的遥测数据（含拍摄定位/运动记录），
    想要一个字节都不丢，请用 bytes 模式——那是逐字节复制，原样保留。

关于 copy 模式保留不了的东西（实话实说）：
    * 上面那类容器装不下的附加流会被跳过。
    * 容器标签里的 encoder 会被 ffmpeg 强行写成 "Lavf x.x"（大疆的原值是
      "DJI OsmoAction4"）。这条实测无解：-metadata、-fflags +bitexact、
      原生标签名、换容器全试过，要么被覆盖要么直接消失。
    除此之外，creation_time / location / make / model 等标签都能带过去。
    对「一个字节都不能变」的极端要求，bytes 模式才是正解。

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
from pathlib import Path

# ---------------------------------------------------------------- 基础常量

DEFAULT_THRESHOLD = "3.9G"          # 默认分割阈值（比 FAT32 的 4GiB 上限留约 100MB 余量）
BUF_SIZE = 8 * 1024 * 1024          # 读写缓冲 8MB

# 默认处理的视频扩展名
DEFAULT_EXTS = (
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".flv",
    ".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".3gp", ".rmvb", ".vob",
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

# 脚本自身所在目录（自动安装的 ffmpeg 会放在这里的 ffmpeg/bin 下）
SCRIPT_DIR = Path(__file__).resolve().parent
LOCAL_FFMPEG_DIR = SCRIPT_DIR / "ffmpeg"


# ---------------------------------------------------------------- 控制台编码

def init_console() -> None:
    """让 Windows 控制台也能正常显示中文。"""
    if IS_WINDOWS:
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
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
    """
    ffmpeg, ffprobe = find_bin("ffmpeg"), find_bin("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe

    log("")
    log("环境检查  ：未检测到 ffmpeg。")
    log("            ffmpeg 只在「流拷贝模式」下需要（好处是每段都能单独播放）；")
    log("            「纯字节切割模式」不需要它，同样无损、速度更快。")

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
        log("            已跳过安装，将使用纯字节切割模式。")
        return ffmpeg, ffprobe

    if install_ffmpeg():
        return find_bin("ffmpeg"), find_bin("ffprobe")
    log("            安装未完成，本次将使用纯字节切割模式。")
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


# ---------------------------------------------------------------- 模式一：纯字节切割

def precheck_targets(paths, overwrite: bool) -> None:
    """动手之前先确认目标文件不存在，避免只写了一半才报错。"""
    for p in paths:
        if p.exists() and not overwrite:
            raise RuntimeError("目标文件已存在：%s（加 --overwrite 覆盖，或先删除旧切片）" % p)


def split_by_bytes(src: Path, threshold: int, outdir: Path,
                   dry_run: bool, overwrite: bool):
    """
    纯二进制切割：把文件均分成 n 段，保证每段 <= threshold，且 n 最小。
    不重新编码，不做任何字节改写，拼接即可还原原文件。
    """
    size = src.stat().st_size
    n = max(1, math.ceil(size / threshold))
    per = math.ceil(size / n)          # 均分后每段字节数，必定 <= threshold

    stem, suffix = src.stem, src.suffix
    planned = [(outdir / ("%s#%d%s" % (stem, i, suffix)), per) for i in range(1, n + 1)]

    if dry_run:
        return planned, "bytes"

    precheck_targets([p for p, _ in planned], overwrite)

    produced = []
    try:
        with open(src, "rb") as fin:
            for path, want in planned:
                if path.exists() and not overwrite:
                    raise RuntimeError("目标文件已存在：%s（加 --overwrite 覆盖）" % path)
                written = 0
                with open(path, "wb") as fout:
                    while written < want:
                        chunk = fin.read(min(BUF_SIZE, want - written))
                        if not chunk:
                            break
                        fout.write(chunk)
                        written += len(chunk)
                produced.append((path, written))
    except Exception:
        for path, _ in produced:
            try:
                path.unlink()
            except Exception:
                pass
        raise

    # 校验：各段之和必须等于原文件大小
    total = sum(w for _, w in produced)
    if total != size:
        raise RuntimeError("切割校验失败：合计 %d 字节，原文件 %d 字节" % (total, size))

    return [(p, w) for p, w in produced], "bytes"


# ---------------------------------------------------------------- 模式二：ffmpeg 流拷贝

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
        raise RuntimeError("该格式不适合流拷贝分段")

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
        log("       定位/运动记录），以及视频缩略图。")
        log("       想一个字节都不丢，请改用 --mode bytes（逐字节复制，原样保留）。")
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


# ---------------------------------------------------------------- 文件收集

def collect_files(folders, exts, recursive: bool, skip_dirs=()):
    files, seen = [], set()
    for folder in folders:
        base = Path(folder)
        if not base.is_dir():
            log("  [跳过] 不是文件夹：%s" % base)
            continue
        it = base.rglob("*") if recursive else base.glob("*")
        for p in it:
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
                try:
                    rel_parts = p.relative_to(base).parts[:-1]
                except ValueError:
                    rel_parts = ()
                if any(part in skip_dirs for part in rel_parts):
                    continue
                rp = p.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                files.append(p)
            except Exception:
                continue
    return files


def pick_folder_gui():
    """无命令行参数时，弹一个文件夹选择框（失败则返回 None）。"""
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.update()
        d = filedialog.askdirectory(title="选择包含视频的文件夹")
        root.destroy()
        return d or None
    except Exception:
        return None


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
  python video_splitter.py D:\\Videos --mode bytes      # 纯字节切割，不需要 ffmpeg
  python video_splitter.py D:\\Videos --mode copy       # 每段都能单独播放
  python video_splitter.py D:\\Videos --dry-run         # 只预览，不实际切割
  python video_splitter.py D:\\Videos --mark-source move # 原片移到 origin/ 文件夹
  python video_splitter.py D:\\Videos --mark-source none # 原片原地不动
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
    p.add_argument("-m", "--mode", choices=("auto", "bytes", "copy"), default="auto",
                   help="切割模式：auto=有 ffmpeg 用 copy 否则 bytes（默认）；"
                        "bytes=纯字节切割；copy=ffmpeg 流拷贝")
    p.add_argument("-o", "--outdir", default=None,
                   help="切片输出目录，默认与源文件同目录")
    p.add_argument("--ext", default=",".join(DEFAULT_EXTS),
                   help="要处理的扩展名，逗号分隔")
    p.add_argument("--no-recursive", action="store_true", help="只处理顶层目录，不进子目录")
    p.add_argument("--all", action="store_true",
                   help="不按大小筛选：扫描到的所有视频文件都切（默认只切超过阈值的）。"
                        "想「每 5 分钟切一段」而不受体积限制时用它")
    p.add_argument("--overwrite", action="store_true", help="目标切片已存在时直接覆盖")
    p.add_argument("--mark-source", choices=("rename", "move", "none"), default="rename",
                   help="切割后如何标记原文件，方便与切片区分："
                        "rename=改名 原名#origin.扩展名（默认）；"
                        "move=移动到单独的归档文件夹；none=原地不动")
    p.add_argument("--source-dir", default="origin",
                   help="--mark-source move 时的归档文件夹名，默认 origin")
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
                   help="禁止自动下载安装 ffmpeg（缺了就只用纯字节切割）")
    p.add_argument("--log", nargs="?", const="video_splitter_log.txt", default=None,
                   help="把输出同时写入日志文件（默认就开启，默认文件名 "
                        "video_splitter_log.txt；不想要就用 --no-log）")
    p.add_argument("--no-log", action="store_true",
                   help="不写日志文件，只在控制台输出")
    p.add_argument("--debug", action="store_true",
                   help="输出详细调试信息：ffmpeg/ffprobe 路径与版本、源文件流布局、"
                        "容器标签、ffmpeg 完整命令与返回码、每段时长码率明细")
    return p


# ---------------------------------------------------------------- 执行主体

def run_processing(args, folders, threshold: int, mode: str,
                   ffmpeg, ffprobe) -> int:
    """执行「扫描 -> 切割 -> 标记原文件 -> 汇总」，返回退出码。"""
    exts = tuple(e.strip().lower() for e in args.ext.split(",") if e.strip())
    exts = tuple(e if e.startswith(".") else "." + e for e in exts)

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
    log("切割模式   ：%s%s" % (mode, "（纯字节，零依赖）" if mode == "bytes"
                            else "（ffmpeg 流拷贝，每段可独立播放）"))
    if mode == "copy":
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
    if mode == "copy" and not ffmpeg:
        log("警告       ：未找到 ffmpeg，将改用纯字节切割模式")
        mode = "bytes"
    if mode == "bytes" and args.seconds:
        log("警告       ：bytes 是逐字节均分，做不到「按时间切分」，")
        log("             --seconds 会被忽略。想按秒切请用 --mode copy。")
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

    # 归档文件夹里的都是已经切过的原片，不再重复处理
    files = collect_files(folders, exts, not args.no_recursive, {args.source_dir})
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

        used_mode = mode
        t0 = time.time()
        try:
            if mode == "copy":
                try:
                    produced, used_mode = split_by_ffmpeg(
                        src, threshold, outdir, args.dry_run,
                        args.overwrite, ffmpeg, ffprobe, args.keep_metadata,
                        seg_seconds=args.seconds)
                except Exception as exc:      # noqa: BLE001
                    if "目标文件已存在" in str(exc):
                        raise                # 这类冲突回退也没用，直接报错更安全
                    if args.seconds:
                        # 你明确指定了按秒切分，就绝不偷偷降级成字节切割——
                        # 否则产物和预期完全不符，还会让人误以为用了 ffmpeg。
                        log("       ！按秒切分失败：%s" % exc)
                        log("       ！未回退为字节切割（因为你指定了按秒切分）。")
                        log("       ！加 --debug 可看到 ffmpeg 的完整命令与报错原文。")
                        log("       ！确实想用字节切割，请显式加 --mode bytes。")
                        raise
                    log("       流拷贝失败（%s），回退为纯字节切割。" % exc)
                    log("       想看失败原因，加 --debug 重跑即可看到 ffmpeg 报错原文。")
                    produced, used_mode = split_by_bytes(
                        src, threshold, outdir, args.dry_run, args.overwrite)
            else:
                produced, used_mode = split_by_bytes(
                    src, threshold, outdir, args.dry_run, args.overwrite)

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
                if used_mode == "copy" and ffprobe and len(produced) > 1:
                    d_src = probe_duration(src, ffprobe)
                    d_parts = [probe_duration(p, ffprobe) for p, _ in produced]
                    if d_src > 0 and all(d > 0 for d in d_parts):
                        gap = sum(d_parts) - d_src
                        ok = -1.0 <= gap <= 5.0 + 3.0 * len(d_parts)
                        log("       时长核对：切片合计 %s，原片 %s，差 %+.2f 秒%s"
                            % (format_duration(sum(d_parts)), format_duration(d_src),
                               gap, "（关键帧对齐的正常偏差）" if ok
                               else "  ⚠ 偏差偏大，请用 --debug 复核"))
                log("       完成：%d 段，用时 %.1f 秒，模式 %s" %
                    (len(produced), time.time() - t0, used_mode))
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
    if mode == "copy" and ok_count and not args.dry_run:
        log("切分方式   ：ffmpeg 流拷贝（-c copy），未重新编码，画质音质无损")
        log("每段可播放 ：是（可单独双击）")
    if mode == "bytes" and ok_count:
        log("提示：纯字节切割的第 2 段及之后需要合并后才能播放；")
        log("      合并命令（macOS/Linux）：cat 原名#1.mp4 原名#2.mp4 > 原名.mp4")
        log("      合并命令（Windows cmd）：copy /b 原名#1.mp4+原名#2.mp4 原名.mp4")
    log("=" * 68)
    if LOG_PATH:
        log("")
        log("★ 以上完整输出已保存到日志文件，随时可打开复制：")
        log("  %s" % LOG_PATH)
        log("  （不想要日志文件，下次加 --no-log 即可）")
    return 0 if fail_count == 0 else 1


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


def interactive_wizard(args, ffmpeg_ok: bool):
    """问答式收集配置。返回配置 dict；用户取消则返回 None。"""
    global WIZARD_ACTIVE
    WIZARD_ACTIVE = True
    try:
        return _wizard_impl(args, ffmpeg_ok)
    except WizardCancelled:
        log("已取消，未做任何改动。")
        return None
    finally:
        WIZARD_ACTIVE = False


def _wizard_impl(args, ffmpeg_ok: bool):
    """问答式收集配置的实际实现，取消由 interactive_wizard 统一接住。"""
    log("")
    log("=" * 68)
    log("视频无损分割工具 · 交互模式")
    log("=" * 68)
    log("每一步直接回车即可使用推荐值，输入 q 可随时取消。")

    # [1/7] 文件夹
    folders = []
    while not folders:
        log("")
        log("[1/7] 要处理的文件夹")
        log("    把文件夹拖进窗口，或粘贴路径（多个用空格分隔）")
        log("    直接回车 -> 弹出图形化选择框")
        raw = ask("    路径：")
        if not raw:
            picked = pick_folder_gui()
            if picked:
                folders = [picked]
                break
            log("    没有选择文件夹，请手动输入路径。")
            continue
        cands = parse_paths(raw)
        valid = [c for c in cands if Path(c).is_dir()]
        bad = [c for c in cands if c not in valid]
        if bad:
            log("    以下路径不是文件夹，已忽略：%s" % "、".join(bad))
        folders = valid
    log("    已选择：%s" % "、".join(str(f) for f in folders))

    # [2/7] 切分方式：按大小 还是 按时间
    log("")
    if args.seconds:
        log("（命令行已指定 --seconds %g，下面默认选中「按时间」）" % args.seconds)
    key = ask_menu("[2/7] 按什么切分", [
        ("1", "按大小", "每片体积不超过阈值（适合「每片都要小于 4G」这类硬限制）"),
        ("2", "按时间", "每片固定秒数，例如每 5 分钟一段（片段时长整齐）"),
    ], "2" if args.seconds else "1")

    preset = {"1": "3.9G", "2": "3.5G", "3": "2G"}
    seconds = None
    all_files = bool(args.all)
    if key == "1":
        # 按大小：先选体积上限，脚本把整条时间轴均分成若干段
        size_default = "4"
        for press_key, press_val in preset.items():
            if parse_size(args.size) == parse_size(press_val):
                size_default = press_key
                break
        key2 = ask_menu("    每片最大体积", [
            ("1", "3.9G", "FAT32 安全值（推荐）"),
            ("2", "3.5G", "更保守，兼容多数网盘"),
            ("3", "2G", "微信、邮件更稳"),
            ("4", "自定义", "例如 1.5G、800M"),
        ], size_default)
        if key2 in preset:
            threshold_text = preset[key2]
        else:
            while True:
                raw = ask("    请输入大小（如 1.5G / 800M）[%s]：" % args.size, args.size)
                try:
                    parse_size(raw)
                    threshold_text = raw
                    break
                except ValueError as exc:
                    log("    %s" % exc)
    else:
        # 按时间：先选每片秒数。大小阈值仍保留，用来挑文件、并兜底防止
        # 某片因为码率波动而超出体积上限。
        preset_t = [("1", 60.0, "1 分钟"), ("2", 180.0, "3 分钟"),
                    ("3", 300.0, "5 分钟"), ("4", 600.0, "10 分钟")]
        t_default = "3"
        if args.seconds:
            for k2, v2, _ in preset_t:
                if abs(args.seconds - v2) < 0.01:
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
            seconds = dict((k, v) for k, v, _ in preset_t)[key2]
        else:
            while True:
                raw = ask("    请输入每片秒数（如 240）：",
                          "%g" % args.seconds if args.seconds else "")
                try:
                    seconds = float(raw)
                    if seconds <= 0:
                        raise ValueError("秒数必须大于 0")
                    break
                except ValueError as exc:
                    log("    %s" % exc)
        threshold_text = args.size
        log("")
        log("    每片目标时长：%.0f 秒（%.1f 分钟）"
            % (seconds, seconds / 60.0))
        log("    体积上限仍为 %s：切出来若有片段超过它，会自动缩小秒数重试。"
            % threshold_text)
        log("")
        log("    默认只切「体积超过 %s」的视频。若想让每段固定时长对"
            % threshold_text)
        log("    所有视频都生效（不管文件大小），请选 y。")
        all_files = ask("    对所有视频切分（不只看超大文件）？[y/N]：",
                        "y" if args.all else "n").lower() in ("y", "yes")

    # [3/7] 切割模式
    if ffmpeg_ok:
        key = ask_menu("[3/7] 切割模式", [
            ("1", "copy", "ffmpeg 流拷贝，每段都能单独播放（推荐）"),
            ("2", "bytes", "纯字节切割，零依赖最快；第 2 段起需合并后播放"),
        ], "2" if args.mode == "bytes" else "1")
        mode = "copy" if key == "1" else "bytes"
    else:
        log("")
        log("[3/7] 切割模式")
        log("    未检测到 ffmpeg，只能使用 bytes 纯字节切割模式。")
        mode = "bytes"

    # [4/7] 原文件处理
    mark_default = {"rename": "1", "move": "2", "none": "3"}.get(args.mark_source, "1")
    if args.delete_source:
        mark_default = "4"
    key = ask_menu("[4/7] 切割完成后，原文件怎么处理", [
        ("1", "改名", "原名#origin.扩展名（推荐）"),
        ("2", "移动", "移到单独的 %s/ 文件夹" % args.source_dir),
        ("3", "不动", "保持原样"),
        ("4", "删除", "切割成功后删除原文件"),
    ], mark_default)
    mark_source = {"1": "rename", "2": "move", "3": "none", "4": "delete"}[key]
    if mark_source == "delete":
        log("")
        log("    ！！切割成功后会删除原文件，此操作无法撤销。")
        if ask("    确认删除原文件？输入 yes 继续：").lower() != "yes":
            log("    已自动改为：改名（#origin）")
            mark_source = "rename"

    # [5/7] 子目录
    log("")
    recursive = ask("[5/7] 是否包含子文件夹？[Y/n]：",
                    "n" if args.no_recursive else "y").lower() != "n"

    # [6/7] 预览
    log("")
    preview = ask("[6/7] 是否先预览一遍（不写任何文件）？[y/N]：",
                  "y" if args.dry_run else "n").lower() in ("y", "yes")

    # [7/7] 详细调试信息
    log("")
    log("[7/7] 是否输出详细调试信息？")
    log("    会额外打印 ffmpeg 路径与版本、源文件流布局、容器标签、")
    log("    ffmpeg 完整命令与返回码、每段时长码率明细。排查问题很有用。")
    debug = ask("    输出详细调试信息？[y/N]：",
                "y" if args.debug else "n").lower() in ("y", "yes")

    # 汇总确认
    mode_text = ("copy（ffmpeg 流拷贝，每段可独立播放）" if mode == "copy"
                 else "bytes（纯字节切割）")
    if seconds:
        split_text = "按时间：每片 %.0f 秒（%.1f 分钟）" % (seconds, seconds / 60.0)
    else:
        split_text = "按大小：每片不超过 %s" % threshold_text
    log("")
    log("-" * 68)
    log("请确认设置")
    log("    文件夹     ：%s" % "、".join(str(f) for f in folders))
    log("    切分方式   ：%s" % split_text)
    log("    处理范围   ：%s" % ("全部视频文件（不按大小筛选）" if all_files
                                else "仅体积超过 %s 的文件" % threshold_text))
    log("    切割模式   ：%s" % mode_text)
    log("    原文件处理 ：%s" % describe_mark(mark_source, args.source_dir))
    log("    子目录     ：%s" % ("包含" if recursive else "不包含"))
    log("    先预览     ：%s" % ("是" if preview else "否"))
    log("    调试信息   ：%s" % ("输出" if debug else "不输出"))
    log("-" * 68)
    log("    回车 = 按上面设置开始    q = 取消")

    while True:
        raw = ask("    请确认 [回车]：")
        low = raw.lower()
        if low == "q":
            log("已取消。")
            return None
        if low in ("", "y", "yes"):
            break
        log("    输入无效，直接回车开始，或输入 q 取消。")

    return {
        "folders": folders,
        "threshold": threshold_text,
        "seconds": seconds,
        "all": all_files,
        "mode": mode,
        "mark_source": mark_source,
        "source_dir": args.source_dir,
        "recursive": recursive,
        "preview": preview,
        "debug": debug,
    }


# ---------------------------------------------------------------- 入口

def close_log() -> None:
    global LOG_HANDLE
    if LOG_HANDLE is not None:
        try:
            LOG_HANDLE.close()
        except Exception:
            pass
        LOG_HANDLE = None


def resolve_mode(mode: str, ffmpeg, ffprobe) -> str:
    if mode == "auto":
        return "copy" if (ffmpeg and ffprobe) else "bytes"
    return mode


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

    if interactive:
        cfg = interactive_wizard(args, bool(ffmpeg and ffprobe))
        if cfg is None:
            close_log()
            return 0

        folders = cfg["folders"]
        args.size = cfg["threshold"]
        args.seconds = cfg["seconds"]
        args.all = bool(cfg.get("all"))
        args.mode = cfg["mode"]
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
            code = run_processing(args, folders, threshold,
                                  resolve_mode(args.mode, ffmpeg, ffprobe),
                                  ffmpeg, ffprobe)
            log("")
            if ask("预览结束。是否现在正式执行切割？[Y/n]：", "y").lower() == "n":
                log("已取消，未做任何改动。")
                close_log()
                return code
            args.dry_run = False

    code = run_processing(args, folders, threshold,
                          resolve_mode(args.mode, ffmpeg, ffprobe),
                          ffmpeg, ffprobe)
    close_log()
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("\n已中断。")
        close_log()
        sys.exit(130)
    except SystemExit:
        close_log()
        raise
    except BaseException:
        # 任何没预料到的异常都完整打印 + 写进日志，
        # 免得双击启动时窗口一闪而过、什么都看不到。
        import traceback
        log("")
        log("!! 程序异常退出，下面是完整堆栈（复制这段给开发者即可定位）：")
        log(traceback.format_exc())
        close_log()
        sys.exit(1)


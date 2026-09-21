#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/splitter.py —— 无损分割引擎（服务端版本）

本文件由 cli/video_splitter.py 派生，切割逻辑与其保持一致。
和桌面版的差异只有三处：

  1. 日志改成可插拔的 sink：服务端要把日志写进数据库并推到 WebSocket，
     不能再直接 print。
  2. 新增进度回调与取消支持：解析 ffmpeg 的 -progress 输出，
     让网页上能看到「第 2/3 段、42%」这样的实时进度。
  3. 去掉交互向导、文件夹选择框和 ffmpeg 自动下载：
     服务端运行在镜像里，ffmpeg 由镜像保证；一个后台服务不应该擅自联网下载二进制。

⚠️ 修改切割逻辑（关键帧重试、大疆遥测流的流映射、元数据回写）时，
   请同步 cli/video_splitter.py，两边必须一致。

设计说明：hooks（日志/进度/取消）用模块级全局变量传递，而不是层层加参数。
原因是本地只有一个 worker 串行执行任务，不存在并发写这些变量的情况；
这与原脚本用全局 DEBUG_MODE / LOG_HANDLE 的风格一致。
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------- 基础常量

DEFAULT_THRESHOLD = "3.9G"          # 比 FAT32 的 4GiB 上限留约 100MB 余量
BUF_SIZE = 8 * 1024 * 1024          # 读写缓冲 8MB

# 切分中途的工作目录前缀。它必须建在「和输出目录同一个文件系统」上：
# 分段先落进工作目录，全部校验通过后再 shutil.move 成最终文件名，跨文件系统
# 的 move 不是改名而是真正的拷贝，整套切片会多读一遍多写一遍（实测让一次
# 6.5G 的切分从 2 分多钟变成 4 分半）。
TMP_PREFIX = ".vsplit-tmp-"

DEFAULT_EXTS = (
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".flv",
    ".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".3gp", ".rmvb", ".vob",
)

# 适合用 ffmpeg segment 流拷贝的格式（按关键帧切分后每段仍可独立播放）
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

# ffmpeg 会强行改写的容器标签，保留不了原值（实测无解，详见桌面版文档）
FFMPEG_LOCKED_TAGS = {"encoder"}

IS_WINDOWS = os.name == "nt"
IS_MAC = platform.system() == "Darwin"

# 项目根目录（core/ 的上一级）；用于找自带的 ffmpeg/bin
PROJECT_DIR = Path(__file__).resolve().parent.parent
LOCAL_FFMPEG_DIR = PROJECT_DIR / "ffmpeg"


class Cancelled(RuntimeError):
    """任务被用户取消。"""


# ---------------------------------------------------------------- 钩子

_log_sink = None        # Callable[[str], None]
_progress_cb = None     # Callable[[dict], None]
_cancel_event = None    # threading.Event
_debug = False


def set_hooks(log_sink=None, progress_cb=None, cancel_event=None,
              debug: bool = False) -> None:
    """安装本次任务的钩子。每次任务开始前调用一次即可。"""
    global _log_sink, _progress_cb, _cancel_event, _debug
    _log_sink = log_sink
    _progress_cb = progress_cb
    _cancel_event = cancel_event
    _debug = bool(debug)


def clear_hooks() -> None:
    set_hooks(None, None, None, False)


def log(msg: str = "") -> None:
    """输出一行日志：有 sink 就交给 sink，没有就退回标准输出。"""
    if _log_sink is not None:
        try:
            _log_sink(msg)
            return
        except Exception:
            # sink 自己出问题不该拖垮切割任务
            pass
    try:
        print(msg, flush=True)
    except Exception:
        pass


def dbg(msg: str = "") -> None:
    if _debug:
        log("   [调试] " + msg)


def dbg_block(title: str, lines) -> None:
    if not _debug:
        return
    log("   [调试] ┌─ %s" % title)
    for ln in lines:
        log("   [调试] │ %s" % ln)
    log("   [调试] └─")


def emit_progress(phase: str = None, progress: float = None,
                  parts_done: int = None, parts_total: int = None,
                  message: str = None) -> None:
    """把进度推给上层（只推送非 None 的字段，避免把已有值覆盖成空）。"""
    if _progress_cb is None:
        return
    payload = {}
    if phase is not None:
        payload["phase"] = phase
    if progress is not None:
        payload["progress"] = max(0.0, min(1.0, float(progress)))
    if parts_done is not None:
        payload["partsDone"] = int(parts_done)
    if parts_total is not None:
        payload["partsTotal"] = int(parts_total)
    if message is not None:
        payload["message"] = message
    if not payload:
        return
    try:
        _progress_cb(payload)
    except Exception:
        pass


def check_cancel() -> None:
    """任务被取消时抛 Cancelled，让上层干净地收尾。"""
    if _cancel_event is not None and _cancel_event.is_set():
        raise Cancelled("任务已被取消")


# ---------------------------------------------------------------- 工具函数

def parse_size(text: str) -> int:
    """把 '3.9G' / '500M' / '1024' 这类写法解析成字节数（1G = 1024^3）。"""
    s = str(text).strip().upper().replace(" ", "")
    m = re.match(r"^([0-9]*\.?[0-9]+)([KMGTP]?)(IB|B)?$", s)
    if not m:
        raise ValueError("无法解析的大小写法：%s（示例：3.9G / 500M / 2G）" % text)
    num = float(m.group(1))
    unit = m.group(2)
    mult = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3,
            "T": 1024 ** 4, "P": 1024 ** 5}[unit]
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


def normalize_exts(exts) -> tuple:
    """把 ['.mp4','mov'] 这类输入规整成 ('mp4', '.mov') 形式的小写集合。"""
    out = []
    for e in exts or ():
        e = str(e).strip().lower()
        if not e:
            continue
        out.append(e if e.startswith(".") else "." + e)
    return tuple(out) or DEFAULT_EXTS


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

    try:
        os.utime(dst, ns=(int(atime * 1e9), int(mtime * 1e9)))
        notes.append("修改时间")
    except Exception:
        pass

    ok = _set_windows_creation_time(dst, ctime) or _set_mac_creation_time(dst, ctime)
    if ok:
        notes.append("创建时间")
    elif IS_MAC and ctime is not None:
        notes.append("创建时间[未设置：需 Xcode 命令行工具]")

    if not IS_WINDOWS:
        try:
            shutil.copymode(src, dst)
        except Exception:
            pass

    return "+".join(notes) if notes else "未设置"


# ---------------------------------------------------------------- ffmpeg 探测

def candidate_dirs():
    """按优先级列出 ffmpeg 可能藏身的目录（容器里通常就是 /usr/bin）。"""
    dirs = [LOCAL_FFMPEG_DIR / "bin", PROJECT_DIR / "bin"]

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
        dirs += [Path("/usr/local/bin"), Path("/usr/bin"), Path("/bin"),
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


def tool_version(path):
    """读工具版本号的第一行，失败返回 None。"""
    if not path:
        return None
    try:
        r = subprocess.run([path, "-version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        lines = (r.stdout or "").splitlines()
        return lines[0] if lines else None
    except Exception:
        return None


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
    读取源文件的流布局与容器标签。

    unmuxable 用来判断「有没有 mp4 装不下的流」——这是本项目踩过的坑：
    大疆（DJI）的 MP4 里有 hevc 主视频 + aac 音频之外，还塞了
    djmd / dbgi / tmcd 三个 data 流和一个 mjpeg 缩略图流。
    mp4 封装器写不了这些，于是 `-map 0` 会直接报
    "Could not find tag for codec none in stream #2" 而整体失败。
    """
    info = {"streams": [], "tags": {}, "unmuxable": False, "n_video": 0, "n_audio": 0}
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

    extra = [s for s in info["streams"] if s["codec_type"] not in ("video", "audio")]
    info["unmuxable"] = bool(extra) or n_video > 1
    info["n_video"] = n_video
    info["n_audio"] = n_audio
    info["extra"] = extra

    # major_brand 之类不是真正的元数据标签，是 ffprobe 自己解析出来的，不能回写
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
    done_bytes = 0
    try:
        with open(src, "rb") as fin:
            for idx, (path, want) in enumerate(planned, start=1):
                if path.exists() and not overwrite:
                    raise RuntimeError("目标文件已存在：%s（加 --overwrite 覆盖）" % path)
                written = 0
                with open(path, "wb") as fout:
                    while written < want:
                        check_cancel()
                        chunk = fin.read(min(BUF_SIZE, want - written))
                        if not chunk:
                            break
                        fout.write(chunk)
                        written += len(chunk)
                        emit_progress(
                            phase="splitting",
                            progress=(done_bytes + written) / max(size, 1),
                            parts_done=idx - 1, parts_total=n,
                            message="正在切分：第 %d/%d 段" % (idx, n))
                done_bytes += written
                produced.append((path, written))
                emit_progress(phase="splitting",
                              progress=done_bytes / max(size, 1),
                              parts_done=idx, parts_total=n)
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


# ---------------------------------------------------------------- 工作目录

def _sweep_stale_workdirs(base: Path, keep: Path) -> None:
    """
    清掉上次残留的临时工作目录。

    正常路径下每次切分结束都会自己删掉工作目录（成功、失败、取消都删），
    但容器被 kill -9、断电这类硬中断走不到清理代码，留下的就是几个 GB 的
    垃圾。所以下次在同一位置开工作目录时顺手扫一遍，只删明显过期的
    （超过一天），避免误伤别的进程正在用的目录。
    """
    try:
        cutoff = time.time() - 24 * 3600
        for d in sorted(base.glob(TMP_PREFIX + "*")):
            if d == keep or not d.is_dir():
                continue
            try:
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
                    log("       清理上次残留的工作目录：%s" % d.name)
            except OSError:
                continue
    except OSError:
        pass


def make_workdir(outdir: Path) -> Path:
    """
    建一个切分用的临时工作目录，**优先和输出目录在同一个文件系统上**。

    为什么非要同文件系统：分段是先写进工作目录、最后再 shutil.move 成最终
    文件名的。跨文件系统的 move 不是改名，而是老老实实读一遍写一遍——整套
    切片等于白搬一次。实测在这块 NAS 上，6.5G 的片子因此从约 2 分钟变成
    4 分半，一半时间花在搬数据上。

    选址顺序：输出目录的上一级 -> 输出目录本身 -> 系统临时目录（兜底）。
    上一级优先，是因为它通常落在监控目录之外，实时监听压根看不见；
    万一前两个都不满足（比如输出目录本身就是挂载点、上一级不可写），
    退回系统临时目录，行为与老版本完全一致。
    """
    outdir = Path(outdir)
    try:
        want_dev = os.stat(outdir).st_dev
    except OSError:
        want_dev = None

    for base in (outdir.parent, outdir):
        try:
            if want_dev is not None and os.stat(base).st_dev != want_dev:
                continue
            tmpdir = Path(tempfile.mkdtemp(prefix=TMP_PREFIX, dir=base))
        except OSError:
            continue
        _sweep_stale_workdirs(base, tmpdir)
        dbg("工作目录：%s（与输出同一文件系统，最后一步只改名不搬数据）" % tmpdir)
        return tmpdir

    tmpdir = Path(tempfile.mkdtemp(prefix=TMP_PREFIX))
    dbg("工作目录：%s（找不到与输出同文件系统的位置，退回系统临时目录）" % tmpdir)
    return tmpdir


# ---------------------------------------------------------------- 模式二：ffmpeg 流拷贝

class FatalSplitError(RuntimeError):
    """重试也不可能成功的错误（容器不支持、目标文件冲突等），直接往外抛。"""


def _cmd_line(cmd) -> str:
    """把命令数组拼成一行好复制、好粘贴的字符串（含空格的参数自动加引号）。"""
    return " ".join(('"%s"' % c if (" " in c and not c.startswith('"')) else c)
                    for c in cmd)


def _drain(stream, sink: list) -> None:
    """后台线程把管道读空，避免 ffmpeg 写满缓冲区后卡死。"""
    try:
        for line in stream:
            sink.append(line)
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _run_ffmpeg(cmd, duration: float, tmpdir: Path, suffix: str,
                n_target: int) -> tuple:
    """
    跑 ffmpeg 并实时回调进度，返回 (returncode, stderr 全文)。

    进度怎么来的：在命令里加 `-progress pipe:1`，ffmpeg 会往 stdout
    持续输出 key=value 形式的状态。这里读 out_time_us 换算成已处理时长。

    有个坑：segment 封装器会按段重置输出时间戳（我们显式传了
    -reset_timestamps 1），于是 out_time 在每进入新的一段时会回跳。
    所以不能直接用 out_time / duration，必须做「回跳即换段」的累加：
        total = 已归零段落的时长之和 + 当前段落已走的时长
    这样无论 ffmpeg 是否重置时间戳，算出来都是对的。
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1)

    err_lines: list = []
    err_thread = threading.Thread(target=_drain, args=(proc.stderr, err_lines),
                                  daemon=True)
    err_thread.start()

    base_us = 0          # 已结束段落累计时长（微秒）
    prev_us = 0          # 当前段落已走时长（微秒）
    parts_done = 0
    last_emit = 0.0
    cancel_sent = False

    try:
        for raw in proc.stdout:
            if _cancel_event is not None and _cancel_event.is_set():
                if not cancel_sent:
                    cancel_sent = True
                    log("       收到取消请求，正在中止 ffmpeg…")
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                continue

            line = raw.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()

            if key == "progress" and value.strip() == "end":
                # 一段结束，把它的时长收进基数
                base_us += prev_us
                prev_us = 0
                parts_done += 1
                continue

            if key not in ("out_time_us", "out_time_ms"):
                continue
            try:
                cur_us = int(value.strip())
            except ValueError:
                continue

            if cur_us + 500_000 < prev_us:
                # 时间戳回跳 -> 进入了新的一段
                base_us += prev_us
                prev_us = cur_us
                parts_done += 1
            else:
                prev_us = max(prev_us, cur_us)

            now = time.time()
            if now - last_emit < 0.5:
                continue
            last_emit = now

            total_us = base_us + prev_us
            if duration > 0:
                progress = (total_us / 1_000_000.0) / duration
            else:
                progress = 0.0
            emit_progress(
                phase="splitting", progress=progress,
                parts_done=max(0, parts_done), parts_total=n_target,
                message="正在切分：第 %d/%d 段" % (max(1, parts_done + 1), n_target))
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass

    # 取消后 ffmpeg 可能只是退出，不代表任务成功，这里明确抛出去
    if cancel_sent:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
        err_thread.join(timeout=5)
        raise Cancelled("任务已被取消")

    try:
        code = proc.wait(timeout=60 * 60 * 6)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        raise RuntimeError("ffmpeg 运行超过 6 小时，已强行终止")

    err_thread.join(timeout=10)
    return code, "".join(err_lines)


def split_by_ffmpeg(src: Path, threshold: int, outdir: Path, dry_run: bool,
                    overwrite: bool, ffmpeg: str, ffprobe, keep_meta: bool,
                    seg_seconds=None):
    """
    FFmpeg 流拷贝分割：-c copy，只换容器不重新编码，画质音质零损失，
    每一段都能独立播放。

    两种定段方式：
      * seg_seconds 有值 -> 按时间切：-segment_time 就是你要的秒数。
      * seg_seconds 为 None -> 按大小切：把整条时间轴均分成
        ceil(文件大小 / 阈值) 段，每段体积尽量接近且不超阈值。

    注意 ffmpeg 的切点必须落在关键帧上，所以每段实际时长会略长于目标值。
    无论哪种方式，只要切出来有片段超阈值，都会自动调整参数重试。
    """
    size = src.stat().st_size
    ext = src.suffix.lower()
    seg_format = SEGMENT_FRIENDLY.get(ext)
    if not seg_format:
        raise RuntimeError("该格式不适合流拷贝分段")

    emit_progress(phase="probe", message="正在读取视频信息…")
    duration = probe_duration(src, ffprobe)
    if duration <= 0:
        raise RuntimeError("无法读取视频时长")

    stem, suffix = src.stem, src.suffix

    # ---- 流布局与容器标签：决定映射哪些流、回写哪些标签 ----
    layout = probe_streams(src, ffprobe)
    restricted = ["-map", "0:v:0", "-map", "0:a?"]
    if layout["unmuxable"]:
        mapping = list(restricted)
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
        log("       想一个字节都不丢，请把切割模式改成「纯字节切割」。")
    else:
        mapping = ["-map", "0"]
    allow_mapping_fallback = mapping != restricted

    if _debug:
        lines = ["文件 %s，大小 %s，时长 %s"
                 % (src.name, human_size(size), format_duration(duration))]
        if duration > 0:
            lines.append("平均码率约 %.2f Mbps（%.0f kbps）"
                         % (size * 8 / duration / 1e6, size * 8 / duration / 1e3))
        lines.append("容器 %s -> 分段封装器 %s" % (ext or "?", seg_format))
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
        for k, v in sorted((layout["tags"] or {}).items()):
            lock = "（ffmpeg 会强制改写，无法保留）" if k.lower() in FFMPEG_LOCKED_TAGS else ""
            lines.append("标签 %s = %s%s" % (k, v, lock))
        if not keep_meta:
            lines.append("注意：本次不保留元数据，容器标签不会写入切片")
        dbg_block("源文件诊断", lines)

    # ---- 分段策略 ----
    if seg_seconds:
        n_expected = max(1, math.ceil(duration / seg_seconds))
        log("       按时间切分：目标每段 %.1f 秒，预计 %d 段" % (seg_seconds, n_expected))
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
        check_cancel()
        tmpdir = make_workdir(outdir)
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

            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
                   "-progress", "pipe:1", "-nostats", "-y",
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
            code, stderr_text = _run_ffmpeg(cmd, duration, tmpdir, suffix, n_target)
            dbg("ffmpeg 返回码 %d，耗时 %.2f 秒" % (code, time.time() - t_ff))

            if code != 0:
                detail = " ".join((stderr_text or "").split())[-400:]
                dbg("ffmpeg 报错原文：%s" % (detail or "(无输出)"))
                if allow_mapping_fallback:
                    # 猜错了容器能力，换成精选映射重试一次（不缩小时长，不算一次浪费）
                    allow_mapping_fallback = False
                    mapping = list(restricted)
                    mapping_switched = True
                    last_error = "容器装不下全部流，改为只保留主视频+音频重试"
                    log("       %s" % last_error)
                    continue
                raise FatalSplitError("ffmpeg 执行失败：%s" % (detail or "无错误输出"))

            parts = sorted(tmpdir.glob("part_*" + suffix))
            if not parts:
                raise RuntimeError("ffmpeg 未产生任何片段")

            if _debug:
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
                    last_error = ("有片段 %s 超过阈值，按时间模式自动把每段时长"
                                  "缩到 %.1f 秒重试" % (human_size(biggest), eff_seconds))
                else:
                    n_target += 1
                    last_error = ("有片段 %s 超过阈值，改为切成 %d 段重试"
                                  % (human_size(biggest), n_target))
                log("       %s" % last_error)
                continue

            emit_progress(phase="verifying", progress=1.0,
                          parts_done=len(parts), parts_total=len(parts),
                          message="正在整理切片…")
            produced = []
            for idx, part in enumerate(parts, start=1):
                check_cancel()
                target = outdir / ("%s#%d%s" % (stem, idx, suffix))
                if target.exists():
                    if not overwrite:
                        raise RuntimeError("目标文件已存在：%s（请开启覆盖或先删除旧切片）" % target)
                    target.unlink()
                shutil.move(str(part), str(target))
                produced.append((target, target.stat().st_size))
            return produced, "copy"

        except Cancelled:
            raise
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

_OWN_PRODUCT_RE = re.compile(r"#(?P<kind>\d+|origin)$", re.IGNORECASE)

# 跳过原因的文案。放在这里是为了让「切片」和「原片」分得开：
# 以前统称「切片或已标记的原片」，用户看不出跳过的是哪一种。
SKIP_REASON = {
    "slice": "是本工具切出来的切片",
    "origin": "是已经切分过的原片",
}


def classify_own_product(path: Path) -> str | None:
    """
    归类本工具自己产生的文件：

        "slice"  -> xxx#1.MP4、xxx#12.MP4   切片，切分的结果
        "origin" -> xxx#origin.MP4         切完后被改名的原片
        None     -> 不是本工具的产物

    为什么要区分这两种：它们的含义正好相反——切片是「结果」，#origin 是
    「喂给结果的那份素材」。只回答「是不是我的产物」不足以判断目录状态：
    切片在 = 这次切分是完整的；切片没了而 #origin 还在 = 结果被删了，
    这个原片其实可以重新切一遍。大小写不敏感（SMB/Samba 上 #origin 可能变成
    #ORIGIN）。
    """
    m = _OWN_PRODUCT_RE.search(path.stem)
    if not m:
        return None
    return "origin" if m.group("kind").lower() == "origin" else "slice"


def product_base(path: Path) -> tuple[str, str] | None:
    """
    取产物对应的「原名 + 扩展名」，用来把切片和它的原片对上号：
    xxx#1.MP4 和 xxx#origin.MP4 都得到 ("xxx", ".MP4")。不是产物则返回 None。
    """
    kind = classify_own_product(path)
    if kind is None:
        return None
    stem = path.stem
    if kind == "origin":
        return stem[: -len("#origin")], path.suffix
    return stem[: stem.rindex("#")], path.suffix


def is_slice_or_origin(path: Path) -> bool:
    """判断是不是本工具自己产生的文件（切片 #1 / 已标记的原片 #origin）。"""
    return classify_own_product(path) is not None


def is_internal_temp(path) -> bool:
    """
    判断路径是否落在切分中途的工作目录里。

    这些是还没写完的分段文件，名字和普通视频一模一样（part_00001.mp4），
    光靠扩展名和产物后缀都认不出来，所以扫描器与实时监听必须显式排除，
    否则它们会被当成新视频入队，切出一堆垃圾。
    """
    try:
        return any(str(part).startswith(TMP_PREFIX) for part in Path(path).parts)
    except Exception:
        return False


def collect_files(folders, exts, recursive: bool, skip_dirs=(), on_skip=None):
    """
    扫描目录下的视频文件，自动跳过自己的产物和归档目录。

    on_skip 是可选的旁路汇报：传了就在每次「看到一个视频文件、但按规则不处理」
    时回调一次，参数是 (路径, 原因)。

    为什么需要它：过滤本身是必要的（不跳过就会把刚切完的原片再切一遍，
    数据会废掉），但**默默跳过**会让用户陷入误判——目录里明明躺着视频，
    界面却说「没有发现需要处理的视频」，用户只能靠猜。把这个信息如实带出去，
    用户才分得清「真的没有」和「被有意跳过了」。不传时行为和以前完全一致，
    命令行那条路不受任何影响。
    """
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
                if is_internal_temp(p):
                    # 切分中途的临时分段，不是用户的文件，也没必要惊动 on_skip
                    continue
                kind = classify_own_product(p)
                if kind:
                    # 报出具体是哪一种：上层要据此判断「切片还在不在」，
                    # 光知道「是我的产物」判断不了目录当前是什么状态
                    if on_skip:
                        on_skip(p, SKIP_REASON[kind])
                    continue
                if any(part in skip_dirs for part in p.parts):
                    if on_skip:
                        on_skip(p, "位于原片归档目录")
                    continue
                rp = p.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                files.append(p)
            except Exception:
                continue
    return files


# ---------------------------------------------------------------- 单文件任务入口

def split_one(src: Path,
              *,
              threshold: int,
              mode: str = "auto",
              outdir: Path = None,
              ffmpeg=None,
              ffprobe=None,
              seg_seconds: float = None,
              keep_metadata: bool = True,
              overwrite: bool = False,
              mark_source_mode: str = "rename",
              source_dir: str = "origin",
              delete_source: bool = False,
              dry_run: bool = False) -> dict:
    """
    处理单个视频文件，返回结果字典。服务端每个任务调用一次。

    返回：
        {
          "usedMode": "copy" | "bytes",
          "parts": [{"path": str, "name": str, "size": int}],
          "totalBytes": int,
          "durationSec": float,
          "sourceAction": str,        # 原文件处理结果描述
          "warnings": [str],
          "skipped": bool,
          "reason": str,
        }
    """
    src = Path(src)
    outdir = Path(outdir) if outdir else src.parent
    warnings = []

    size = src.stat().st_size
    resolved = mode
    if resolved == "auto":
        resolved = "copy" if (ffmpeg and ffprobe) else "bytes"
    if resolved == "copy" and not ffmpeg:
        warnings.append("未找到 ffmpeg，已自动改用纯字节切割")
        resolved = "bytes"
    if resolved == "bytes" and seg_seconds:
        warnings.append("纯字节切割做不到「按时间切分」，每片时长参数已忽略")
        seg_seconds = None

    emit_progress(phase="probe", progress=0.0, parts_done=0, parts_total=0,
                  message="正在读取视频信息…")
    duration = probe_duration(src, ffprobe) if ffprobe else 0.0

    outdir.mkdir(parents=True, exist_ok=True)

    used_mode = resolved
    if resolved == "copy":
        try:
            produced, used_mode = split_by_ffmpeg(
                src, threshold, outdir, dry_run, overwrite,
                ffmpeg, ffprobe, keep_metadata, seg_seconds=seg_seconds)
        except Cancelled:
            raise
        except Exception as exc:      # noqa: BLE001
            if "目标文件已存在" in str(exc):
                raise
            if seg_seconds:
                # 用户明确要求按时间切分，就绝不偷偷降级成字节切割，
                # 否则产物和预期完全不符，还会让人误以为用了 ffmpeg。
                raise
            warnings.append("流拷贝失败（%s），已回退为纯字节切割" % exc)
            log("       流拷贝失败（%s），回退为纯字节切割。" % exc)
            produced, used_mode = split_by_bytes(
                src, threshold, outdir, dry_run, overwrite)
    else:
        produced, used_mode = split_by_bytes(
            src, threshold, outdir, dry_run, overwrite)

    emit_progress(phase="verifying", message="正在校验与套用时间戳…")
    parts = []
    total = 0
    for path, w in produced:
        if not dry_run:
            copy_timestamps(src, path)
        parts.append({"path": str(path), "name": path.name, "size": int(w)})
        total += w

    # 时长核对：确认时间轴上没丢内容。流拷贝按关键帧切分，每段末尾会多包
    # 一个 GOP，所以总和略大属正常。
    if not dry_run and used_mode == "copy" and ffprobe and len(produced) > 1 and duration > 0:
        try:
            d_parts = [probe_duration(p, ffprobe) for p, _ in produced]
            if all(d > 0 for d in d_parts):
                gap = sum(d_parts) - duration
                if not (-1.0 <= gap <= 5.0 + 3.0 * len(d_parts)):
                    warnings.append("切片时长合计与原片偏差 %.2f 秒，建议开启调试信息复核" % gap)
        except Exception:
            pass

    emit_progress(phase="marking", message="正在处理原文件…")
    if dry_run:
        source_action = "（预览）原文件未改动"
    elif delete_source:
        try:
            src.unlink()
            source_action = "原文件已删除"
        except Exception as exc:              # noqa: BLE001
            source_action = "切片已生成，但原文件删除失败（%s），请手动删除" % exc
            warnings.append(source_action)
    else:
        source_action = mark_source(src, mark_source_mode, source_dir)

    emit_progress(phase="done", progress=1.0,
                  parts_done=len(parts), parts_total=len(parts),
                  message="处理完成")

    return {
        "usedMode": used_mode,
        "parts": parts,
        "totalBytes": total,
        "durationSec": duration,
        "sourceAction": source_action,
        "warnings": warnings,
        "skipped": False,
        "reason": "",
    }

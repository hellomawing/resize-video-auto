#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
undo_split.py —— 撤销 video_splitter.py 的分割动作（Windows / macOS / Linux 通用）

作用：
    把一次分割「完整回退」——
      1. 删掉分割产生的切片（原名#1、原名#2 ...）
      2. 把原片 原名#origin.扩展名 改回原本的 原名.扩展名

    只处理「切片 + 原片」齐全的一组文件；原片缺失的孤儿切片不会动，
    避免误删。

安全设计（很重要）：
    * 默认是预览模式，只看不动。确认无误后加 --yes 才真正执行。
    * 执行前会逐个校验「删掉这些切片会不会丢数据」，不通过就一个切片都不删。
      校验方式按切割模式自动选择：
        - 纯字节切割（bytes）：各切片字节之和必须严格等于原片字节数，
          差一个字节都不放行。这是最强的证明。
        - ffmpeg 流拷贝（copy）：切片之和必然小于原片（mp4 装不下的
          djmd/dbgi 遥测流、mjpeg 缩略图会被跳过），所以改用时长核对——
          各切片时长之和 ≈ 原片时长才放行，能证明时间轴上没有内容缺失。
      需要 ffprobe 才能做时长核对；找不到 ffprobe 时 copy 模式的切片
      会被保守地跳过不删。
    * 原片改回原名时，如果目标名已被占用，会跳过并报告，绝不覆盖。

用法：
    python undo_split.py "C:\\Users\\hello\\Desktop\\DJI_001"            # 预览
    python undo_split.py "C:\\Users\\hello\\Desktop\\DJI_001" --yes      # 执行
    python undo_split.py <目录> --keep-origin                            # 只校验，保留原片名
    python undo_split.py <目录> --trash                                  # 切片移入回收站而非直接删除
"""

from __future__ import annotations

import argparse
import ctypes
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_MAC = platform.system() == "Darwin"

# 与 video_splitter.py 保持一致
SLICE_RE = re.compile(r"^(?P<base>.+)#(?P<idx>\d+)$")
ORIGIN_SUFFIX = "#origin"

VIDEO_EXTS = (
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".flv",
    ".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".3gp", ".rmvb", ".vob",
)


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


def log(msg: str = "") -> None:
    print(msg, flush=True)


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0 or unit == "TB":
            return "%.2f %s" % (num, unit) if unit != "B" else "%d B" % num
        num /= 1024.0
    return "%.2f TB" % num


def format_duration(seconds: float) -> str:
    """把秒数格式化成 1:23:45.6 / 12:34.5 这种可读写法。"""
    if seconds <= 0:
        return "-"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h:
        return "%d:%02d:%04.1f" % (h, m, s)
    return "%d:%04.1f" % (m, s)


# ---------------------------------------------------------------- ffprobe 探测

def find_bin(name: str):
    """找 ffprobe/ffmpeg：PATH → 脚本目录 ffmpeg/bin → 常见安装位置。"""
    exe = name + ".exe" if IS_WINDOWS else name
    found = shutil.which(exe) or shutil.which(name)
    if found:
        return found
    here = Path(__file__).resolve().parent
    cands = [here / "ffmpeg" / "bin" / exe, here / "ffmpeg" / "bin" / name]
    if IS_WINDOWS:
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        cands += [local / "Microsoft" / "WinGet" / "Links" / exe,
                  Path("C:/ffmpeg/bin") / exe,
                  Path("C:/Program Files/ffmpeg/bin") / exe]
        pkg = local / "Microsoft" / "WinGet" / "Packages"
        if pkg.is_dir():
            for d in pkg.glob("*FFmpeg*"):
                cands += [d / "bin" / exe, d / exe]
    if IS_MAC:
        cands += [Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name]
    for c in cands:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


def probe_duration(path: Path, ffprobe) -> float:
    """读取媒体时长（秒），失败返回 0。"""
    if ffprobe:
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=180)
            return float((r.stdout or "").strip())
        except Exception:
            pass
    ffmpeg = find_bin("ffmpeg")
    if ffmpeg:
        try:
            r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180)
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r.stderr or "")
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        except Exception:
            pass
    return 0.0


def verify_deletable(origin: Path, parts, size: int, total: int, ffprobe) -> dict:
    """
    判断「删掉这些切片是否会丢数据」，返回
        {"ok": bool, "mode": "bytes"|"copy"|"?", "reason": str,
         "d_origin": float, "d_parts": [float], "d_sum": float, "checked": bool}

    video_splitter.py 有两种切割模式，产物完全不同，所以校验方式也不同：

    * bytes（纯字节切割）：切片就是把源文件按字节切开，所以
      「各切片字节之和 == 原片字节数」必须严格成立，差一个字节都不行。
      这是最强的证明，直接放行。

    * copy（ffmpeg 流拷贝）：按关键帧重新封装，mp4 装不下的流（大疆的
      djmd / dbgi / tmcd 遥测流、mjpeg 缩略图）会被跳过，所以
      「切片之和 < 原片大小」是正常现象，字节校验必然失败。
      这种情况改用时长核对：只要各切片时长之和 ≈ 原片时长，
      就说明时间轴上没有任何一段内容被漏掉。
      切点落在关键帧上会让每段略微变长，所以总和允许比原片长一点。
    """
    res = {"ok": False, "mode": "?", "reason": "", "d_origin": 0.0,
           "d_parts": [], "d_sum": 0.0, "checked": False}

    if total == size:
        res.update(ok=True, mode="bytes", checked=True,
                   reason="切片字节之和与原片完全一致（纯字节切割）")
        return res

    if total > size:
        res["reason"] = ("切片合计 %s 反而大于原片 %s，不正常"
                         % (human_size(total), human_size(size)))
        return res

    if not ffprobe:
        res["reason"] = ("切片合计 %s 小于原片 %s（像是流拷贝产物），"
                         "但找不到 ffprobe，无法核对时长，不能确认完整"
                         % (human_size(total), human_size(size)))
        return res

    d_origin = probe_duration(origin, ffprobe)
    d_parts = [probe_duration(p, ffprobe) for p in parts]
    d_sum = sum(d_parts)
    res.update(mode="copy", d_origin=d_origin, d_parts=d_parts, d_sum=d_sum,
               checked=True)

    if d_origin <= 0 or any(d <= 0 for d in d_parts):
        res.update(ok=False, reason="无法读取时长，不能确认切片完整")
        return res

    slack = max(5.0, 3.0 * len(parts))       # 每段按关键帧对齐，最多多一个 GOP
    if d_origin * 0.99 <= d_sum <= d_origin + slack:
        res.update(ok=True,
                   reason="切片时长合计 %.2f 秒 ≈ 原片 %.2f 秒，时间轴无缺失"
                          % (d_sum, d_origin))
    elif d_sum < d_origin * 0.99:
        res["reason"] = ("切片时长合计仅 %.2f 秒，原片 %.2f 秒，少了 %.2f 秒内容"
                         % (d_sum, d_origin, d_origin - d_sum))
    else:
        res["reason"] = ("切片时长合计 %.2f 秒，比原片 %.2f 秒多出 %.2f 秒，异常"
                         % (d_sum, d_origin, d_sum - d_origin))
    return res


# ---------------------------------------------------------------- 扫描

def scan_groups(folder: Path, recursive: bool, source_dir: str, ffprobe=None):
    """
    找出所有「切片 + 原片」组合，返回 (groups, orphans)。

    groups  : [{"base": 原文件名主干, "suffix": 扩展名, "origin": Path,
                "slices": [Path...], "size": 原片字节数, "sum": 切片合计,
                "mode": "bytes"|"copy"|"?", "ok": 是否可安全删除,
                "reason": 判定依据或失败原因,
                "d_origin"/"d_parts"/"d_sum": 时长信息（秒）}]
    orphans : 有切片但找不到原片的文件列表
    """
    base_iter = folder.rglob("*") if recursive else folder.glob("*")
    files = []
    for p in base_iter:
        try:
            if p.is_file():
                files.append(p)
        except OSError:
            continue

    # 目录形式归档（--mark-source move）也算原片
    originals = {}          # (base, suffix) -> Path
    slices = {}             # (base, suffix) -> [(idx, Path)]

    for p in files:
        stem, suffix = p.stem, p.suffix
        if suffix.lower() not in VIDEO_EXTS:
            continue

        # 1) 同目录改名形式：原名#origin.ext
        if stem.endswith(ORIGIN_SUFFIX):
            key = (stem[: -len(ORIGIN_SUFFIX)], suffix)
            originals.setdefault(key, p)
            continue

        # 2) 归档目录形式：origin/原名.ext
        if p.parent.name == source_dir:
            key = (stem, suffix)
            originals.setdefault(key, p)
            continue

        # 3) 切片：原名#数字.ext
        m = SLICE_RE.match(stem)
        if m:
            key = (m.group("base"), suffix)
            slices.setdefault(key, []).append((int(m.group("idx")), p))

    groups, orphans = [], []
    for key, parts in sorted(slices.items()):
        base, suffix = key
        parts = [p for _, p in sorted(parts, key=lambda x: x[0])]
        origin = originals.get(key)
        if origin is None:
            orphans.extend(parts)
            continue
        size = origin.stat().st_size
        total = sum(p.stat().st_size for p in parts)
        verdict = verify_deletable(origin, parts, size, total, ffprobe)
        group = {
            "base": base,
            "suffix": suffix,
            "origin": origin,
            "slices": parts,
            "size": size,
            "sum": total,
            "mode": verdict["mode"],
            "ok": verdict["ok"],
            "reason": verdict["reason"],
            "d_origin": verdict["d_origin"],
            "d_parts": verdict["d_parts"],
            "d_sum": verdict["d_sum"],
        }
        groups.append(group)

    return groups, orphans


# ---------------------------------------------------------------- 删除

def delete_file(path: Path, use_trash: bool) -> None:
    """删除文件；use_trash=True 时尽量走回收站/废纸篓。"""
    if use_trash:
        if IS_WINDOWS:
            # 用 Shell API 送进回收站（不依赖第三方库）
            try:
                class SHFILEOPSTRUCTW(ctypes.Structure):
                    _fields_ = [
                        ("hwnd", ctypes.c_void_p),
                        ("wFunc", ctypes.c_uint),
                        ("pFrom", ctypes.c_wchar_p),
                        ("pTo", ctypes.c_wchar_p),
                        ("fFlags", ctypes.c_uint16),
                        ("fAnyOperationsAborted", ctypes.c_bool),
                        ("hNameMappings", ctypes.c_void_p),
                        ("lpszProgressTitle", ctypes.c_wchar_p),
                    ]
                FO_DELETE = 3
                FOF_ALLOWUNDO = 0x0040
                FOF_NOCONFIRMATION = 0x0010
                FOF_SILENT = 0x0004
                op = SHFILEOPSTRUCTW()
                op.wFunc = FO_DELETE
                op.pFrom = str(path.resolve()) + "\0\0"
                op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
                rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
                if rc != 0:
                    raise OSError("SHFileOperationW 返回 %d" % rc)
                return
            except Exception:
                pass          # 回收站不可用就退化成直接删除
        elif IS_MAC:
            try:
                import subprocess
                subprocess.run(
                    ["osascript", "-e",
                     'tell application "Finder" to delete POSIX file "%s"'
                     % path.resolve()],
                    check=True, capture_output=True)
                return
            except Exception:
                pass
        else:
            trashed = shutil.which("gio") or shutil.which("trash-put")
            if trashed:
                try:
                    import subprocess
                    cmd = ([trashed, "trash", str(path.resolve())]
                           if trashed.endswith("gio")
                           else [trashed, str(path.resolve())])
                    subprocess.run(cmd, check=True, capture_output=True)
                    return
                except Exception:
                    pass
    path.unlink()


def restore_origin(group) -> str:
    """把 原名#origin.扩展名 改回 原名.扩展名，返回结果描述。"""
    origin = group["origin"]
    target = origin.with_name(group["base"] + group["suffix"])
    if origin == target:
        return "原片本就在原名位置"
    if target.exists():
        return "原片未改名：%s 已存在，请手工确认" % target.name
    try:
        origin.rename(target)
    except OSError as exc:
        return "原片改名失败（%s）" % exc
    return "原片已恢复为 %s" % target.name


# ---------------------------------------------------------------- 主流程

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="undo_split.py",
        description="撤销 video_splitter.py 的分割：删切片、把 #origin 改回原名",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python undo_split.py D:\\Videos           # 预览会删什么、改什么名（默认不动手）
  python undo_split.py D:\\Videos --yes     # 确认无误后真正执行
  python undo_split.py D:\\Videos --trash --yes   # 切片进回收站而不是直接删
  python undo_split.py D:\\Videos --keep-origin   # 只校验不删，看看对不对得上
""")
    p.add_argument("folders", nargs="*", help="要处理的文件夹（可多个）")
    p.add_argument("--yes", action="store_true",
                   help="真正执行删除与改名（不加此参数只预览）")
    p.add_argument("--trash", action="store_true",
                   help="把切片移入回收站/废纸篓，而不是直接删除")
    p.add_argument("--keep-origin", action="store_true",
                   help="不把 #origin 改回原名（只删切片，或只做校验）")
    p.add_argument("--keep-slices", action="store_true",
                   help="不删切片（只把 #origin 改回原名）")
    p.add_argument("--no-recursive", action="store_true",
                   help="只处理顶层目录，不进子目录")
    p.add_argument("--source-dir", default="origin",
                   help="--mark-source move 时用的归档文件夹名，默认 origin")
    return p


def main(argv=None) -> int:
    init_console()
    args = build_parser().parse_args(argv)

    folders = [Path(f) for f in args.folders if str(f).strip()]
    if not folders:
        log("请给出要处理的文件夹路径。")
        log("例如：python undo_split.py \"C:\\Users\\hello\\Desktop\\DJI_001\"")
        return 2

    apply_changes = bool(args.yes)
    ffprobe = find_bin("ffprobe")
    log("=" * 68)
    log("撤销分割工具")
    log("=" * 68)
    log("处理目录 ：%s" % "、".join(str(f) for f in folders))
    log("当前模式 ：%s" % ("正式执行" if apply_changes else "预览（不加 --yes 不会改动任何文件）"))
    log("ffprobe  ：%s" % (ffprobe or "未找到（只能校验纯字节切割，流拷贝切片会被跳过）"))
    if args.keep_origin:
        log("原片改名 ：跳过（--keep-origin）")
    if args.keep_slices:
        log("切片删除 ：跳过（--keep-slices）")

    total_groups = total_ok = total_bad = 0
    freed = 0
    deleted = restored = 0
    problems = []

    for folder in folders:
        if not folder.is_dir():
            log("")
            log("[跳过] 不是文件夹：%s" % folder)
            continue

        groups, orphans = scan_groups(folder, not args.no_recursive,
                                      args.source_dir, ffprobe)
        if not groups and not orphans:
            log("")
            log("[%s] 没有发现「切片 + 原片」组合。" % folder)
            continue

        log("")
        log("-" * 68)
        log("[%s] 找到 %d 组可回退的分割" % (folder, len(groups)))
        log("-" * 68)

        for g in groups:
            total_groups += 1
            size_txt = human_size(g["size"])
            log("")
            log("%s%s  原片 %s" % (g["base"], g["suffix"], size_txt))
            if g["mode"] == "copy" and g["d_origin"] > 0:
                log("  原片时长 %s" % format_duration(g["d_origin"]))
            for i, s in enumerate(g["slices"]):
                dur = ""
                if i < len(g["d_parts"]) and g["d_parts"][i] > 0:
                    dur = "  %s" % format_duration(g["d_parts"][i])
                log("  切片  %-42s %s%s" % (s.name, human_size(s.stat().st_size), dur))
            log("  切片合计 %s（占原片 %.1f%%）"
                % (human_size(g["sum"]),
                   g["sum"] * 100.0 / g["size"] if g["size"] else 0))
            if not g["ok"]:
                total_bad += 1
                log("  ！校验不通过：%s" % g["reason"])
                log("  ！为安全起见不删除任何切片（切片的完整性无法确认）。")
                problems.append("%s：%s" % (g["base"], g["reason"]))
                # 改名不是破坏性操作，且随时可改回去，所以这一步照做：
                # 切片缺失时再把原片也留在 #origin 名下，只会让目录更乱。
                if not args.keep_origin:
                    if apply_changes:
                        result = restore_origin(g)
                        log("  %s（本次只恢复文件名，未删任何切片）" % result)
                        if result.startswith("原片已恢复"):
                            restored += 1
                        elif "未改名" in result or "失败" in result:
                            problems.append("%s %s" % (g["base"], result))
                    else:
                        log("  （预览）原片将改回 %s（切片保留）"
                            % (g["base"] + g["suffix"]))
                continue

            total_ok += 1
            log("  校验通过 [%s 模式]：%s" % (g["mode"], g["reason"]))

            if not apply_changes:
                if not args.keep_slices:
                    log("  （预览）将删除以上 %d 个切片，释放 %s"
                        % (len(g["slices"]),
                           human_size(sum(p.stat().st_size for p in g["slices"]))))
                if not args.keep_origin:
                    log("  （预览）原片将改回 %s%s" % (g["base"], g["suffix"]))
                continue

            if not args.keep_slices:
                failed = []
                # 体积必须在删除前先记下来——删完再 stat 当然只能得到 0
                slice_bytes = {}
                for s in g["slices"]:
                    try:
                        slice_bytes[s] = s.stat().st_size
                    except OSError:
                        slice_bytes[s] = 0
                for s in g["slices"]:
                    try:
                        delete_file(s, args.trash)
                        deleted += 1
                        freed += slice_bytes[s]
                    except Exception as exc:          # noqa: BLE001
                        failed.append("%s（%s）" % (s.name, exc))
                if failed:
                    log("  ！部分切片删除失败：%s" % "、".join(failed))
                    problems.append("%s 切片删除失败" % g["base"])
                    log("  ！原片保持 #origin 不动，等你处理完再回退。")
                    continue

            if not args.keep_origin:
                result = restore_origin(g)
                log("  %s" % result)
                if result.startswith("原片已恢复"):
                    restored += 1
                elif "未改名" in result or "失败" in result:
                    problems.append("%s %s" % (g["base"], result))

        if orphans:
            log("")
            log("！发现 %d 个找不到原片的孤儿切片，为安全起见不动它们：" % len(orphans))
            for o in orphans[:10]:
                log("    %s" % o)
            if len(orphans) > 10:
                log("    ...（其余 %d 个略）" % (len(orphans) - 10))
            problems.append("%d 个孤儿切片未处理" % len(orphans))

    log("")
    log("=" * 68)
    if apply_changes:
        log("处理完成：校验通过 %d 组，跳过 %d 组，删除切片 %d 个，恢复原片名 %d 个，"
            "释放约 %s" % (total_ok, total_bad, deleted, restored, human_size(freed)))
    else:
        log("预览完成：可回退 %d 组，异常 %d 组。" % (total_ok, total_bad))
        log("确认上面内容无误后，加 --yes 真正执行：")
        log('  python undo_split.py "%s" --yes' % folders[0])
    log("=" * 68)

    if problems:
        log("")
        log("需要你留意的 %d 项：" % len(problems))
        for p in problems[:20]:
            log("  - %s" % p)
        if len(problems) > 20:
            log("  ...（其余 %d 项略）" % (len(problems) - 20))

    return 0 if (total_groups and not total_bad) or not total_groups else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("\n已中断，未完成的操作请重新运行。")
        sys.exit(130)

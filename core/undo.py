#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/undo.py —— 撤销分割（服务端版本）

本文件由 cli/undo_split.py 派生。差异：
  * 去掉命令行与 print，改成返回结构化数据，供 API 层序列化给前端
  * 删除目标从「直接删除」改为「默认走回收站/废纸篓」
  * 保留核心安全设计：校验不通过的组一个切片都不删

安全设计（很重要）：
    执行前会逐个校验「删掉这些切片会不会丢数据」，不通过就一个切片都不删。
    校验方式按切割模式自动选择：
      - 纯字节切割（bytes）：各切片字节之和必须严格等于原片字节数，差一个
        字节都不放行。这是最强的证明。
      - ffmpeg 流拷贝（copy）：切片之和与原片对不上（可能是小了——mp4 装不下的
        djmd/dbgi 遥测流、mjpeg 缩略图被跳过；也可能是略大——按关键帧切分时
        每段会多包一个 GOP），所以改用时长核对——
        各切片时长之和 ≈ 原片时长才放行。
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from . import splitter as engine

IS_WINDOWS = os.name == "nt"
IS_MAC = platform.system() == "Darwin"

SLICE_RE = re.compile(r"^(?P<base>.+)#(?P<idx>\d+)$")
ORIGIN_SUFFIX = "#origin"

VIDEO_EXTS = engine.DEFAULT_EXTS


def verify_deletable(origin: Path, parts, size: int, total: int, ffprobe) -> dict:
    """
    判断「删掉这些切片是否会丢数据」，返回
        {"ok": bool, "mode": "bytes"|"copy"|"?", "reason": str,
         "dOrigin": float, "dParts": [float], "dSum": float}

    判定顺序：
      1. 切片字节之和与原片**完全相等** -> 确定是纯字节切割，这是最强的证明，
         直接放行，连 ffprobe 都不需要。
      2. 否则就是流拷贝产物，改用时长核对：切片时长之和 ≈ 原片时长
         才说明时间轴上没有缺失。

    关于「切片之和可能大于原片」——这点很容易搞错：
    ffmpeg 的切点必须落在关键帧上，每段末尾会多包一个 GOP，
    所以切片总和比原片**略大一点是正常的**（实测 1MB 的片子切成 3 段，
    会多出约 0.2%）。把它当成异常会导致正常的切分无法撤销。
    真正需要警惕的是「大出一大截」，这里用 50% 作为兜底红线。
    """
    res = {"ok": False, "mode": "?", "reason": "", "dOrigin": 0.0,
           "dParts": [], "dSum": 0.0}

    if total == size:
        res.update(ok=True, mode="bytes",
                   reason="切片字节之和与原片完全一致（纯字节切割）")
        return res

    if total > size * 1.5:
        res["reason"] = ("切片合计 %s 比原片 %s 大出太多，不正常"
                         % (engine.human_size(total), engine.human_size(size)))
        return res

    if not ffprobe:
        res["reason"] = ("切片合计 %s 与原片 %s 不一致（像是流拷贝产物，"
                         "按关键帧切分会让每段多包一个 GOP），"
                         "但找不到 ffprobe，无法核对时长，不能确认完整"
                         % (engine.human_size(total), engine.human_size(size)))
        return res

    d_origin = engine.probe_duration(origin, ffprobe)
    d_parts = [engine.probe_duration(p, ffprobe) for p in parts]
    d_sum = sum(d_parts)
    res.update(mode="copy", dOrigin=d_origin, dParts=d_parts, dSum=d_sum)

    if d_origin <= 0 or any(d <= 0 for d in d_parts):
        res.update(ok=False, reason="无法读取时长，不能确认切片完整")
        return res

    slack = max(5.0, 3.0 * len(parts))       # 每段按关键帧对齐，最多多一个 GOP
    if d_origin * 0.99 <= d_sum <= d_origin + slack:
        # 把体积关系一并写进判定依据，用户看到「总和比原片大」时才不会慌
        if total < size:
            detail = "切片合计 %s < 原片 %s（mp4 装不下的附加流被跳过）" % (
                engine.human_size(total), engine.human_size(size))
        else:
            detail = "切片合计 %s 略大于原片 %s（关键帧对齐的正常多包）" % (
                engine.human_size(total), engine.human_size(size))
        res.update(ok=True,
                   reason="切片时长合计 %.2f 秒 ≈ 原片 %.2f 秒，时间轴无缺失；%s"
                          % (d_sum, d_origin, detail))
    elif d_sum < d_origin * 0.99:
        res["reason"] = ("切片时长合计仅 %.2f 秒，原片 %.2f 秒，少了 %.2f 秒内容"
                         % (d_sum, d_origin, d_origin - d_sum))
    else:
        res["reason"] = ("切片时长合计 %.2f 秒，比原片 %.2f 秒多出 %.2f 秒，异常"
                         % (d_sum, d_origin, d_sum - d_origin))
    return res


def scan_groups(folder: Path, recursive: bool = True, source_dir: str = "origin",
                ffprobe=None) -> tuple:
    """
    找出所有「切片 + 原片」组合，返回 (groups, orphans)。
    groups 中每项含 ok/reason，供前端展示哪些能安全撤销。
    """
    base_iter = folder.rglob("*") if recursive else folder.glob("*")
    files = []
    for p in base_iter:
        try:
            if p.is_file():
                files.append(p)
        except OSError:
            continue

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
        groups.append({
            "base": base,
            "suffix": suffix,
            "origin": str(origin),
            "slices": [{"path": str(p), "name": p.name, "size": p.stat().st_size}
                       for p in parts],
            "originSize": size,
            "sliceSum": total,
            "mode": verdict["mode"],
            "ok": verdict["ok"],
            "reason": verdict["reason"],
            "originDuration": verdict["dOrigin"],
            "sliceDurations": verdict["dParts"],
            "durationSum": verdict["dSum"],
        })

    return groups, orphans


def _windows_recycle(path: Path) -> bool:
    """
    用 Shell API 把文件送进回收站。

    坑记录（踩过，务必别改回去）：
    pFrom 要求的格式是「以双 NUL 结尾的路径列表」。如果直接把
    "路径\\0\\0" 赋给 c_wchar_p，ctypes 会按 C 字符串处理、在第一个 NUL 处
    截断，Shell 于是继续往后读内存，把垃圾数据当成第二个待删条目。
    表现出来的现象极具误导性：**文件确实被回收到回收站了，但函数返回
    ERROR_FILE_NOT_FOUND(2)**，调用方以为失败、又去 unlink，结果抛
    FileNotFoundError，最终报告「删除失败」——文件其实早没了。

    正确做法是用 create_unicode_buffer 造一个带内嵌 NUL 的缓冲区，
    并在调用期间保持它的引用不被回收。
    """
    try:
        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", ctypes.c_void_p),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_uint16),
                # BOOL 是 4 字节，用 c_bool（1 字节）虽然靠对齐凑出了相同的
                # 结构体大小，但读回收状态会读到填充字节，这里必须用 c_int
                ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p),
            ]

        FO_DELETE = 3
        FOF_ALLOWUNDO = 0x0040        # 关键：走回收站而不是直接删
        FOF_NOCONFIRMATION = 0x0010
        FOF_SILENT = 0x0004
        FOF_NOERRORUI = 0x0400        # 服务里跑，绝不能弹错误对话框

        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        # buf 必须在调用结束前一直有引用，否则可能被 GC 掉导致指针悬空
        buf = ctypes.create_unicode_buffer(str(path.resolve()) + "\0")
        op.pFrom = ctypes.cast(buf, ctypes.c_wchar_p)
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI

        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return rc == 0 and not op.fAnyOperationsAborted
    except Exception:
        return False


def _mac_trash(path: Path) -> bool:
    try:
        subprocess.run(
            ["osascript", "-e",
             'tell application "Finder" to delete POSIX file "%s"' % path.resolve()],
            check=True, capture_output=True)
        return True
    except Exception:
        return False


def _linux_trash(path: Path) -> bool:
    """
    Linux 上尝试用 trash-put / gio。

    注意：精简容器镜像里通常没有这两个命令，也没有桌面环境，
    所以这里失败是常态，会退化成直接删除。调用方必须如实告诉用户
    到底是「进了回收站」还是「永久删除」，不能默认承诺回收站。
    """
    tool = shutil.which("trash-put") or shutil.which("gio")
    if not tool:
        return False
    try:
        cmd = ([tool, "trash", str(path.resolve())]
               if tool.endswith("gio") else [tool, str(path.resolve())])
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except Exception:
        return False


def delete_file(path: Path, use_trash: bool = True) -> str:
    """
    删除文件，返回实际发生的方式：
        "trash"  -> 进了回收站/废纸篓，还能找回
        "delete" -> 永久删除，找不回来了
    调用方有义务把这个结果如实呈现给用户。

    判定原则：**看文件还在不在，而不是看 API 的返回码**。
    实测 Windows 上 SHFileOperationW 存在「文件已经被删掉、返回值却是
    ERROR_FILE_NOT_FOUND(2)」的情况（某些卷没配回收站时就会这样）。
    如果相信返回码再去 unlink，只会得到一个 FileNotFoundError，
    最后报给用户「删除失败」——而文件其实已经没了，纯属误导。
    所以这里统一以「调用后文件是否存在」为准，并保持「拿不准就按永久删除
    上报」的保守态度：绝不把永久删除说成可恢复。
    """
    try:
        if not path.exists():
            # 已经不在了（比如上一次操作删到一半中断），按成功处理
            return "delete"
    except OSError:
        pass

    if use_trash:
        if IS_WINDOWS:
            recycled = _windows_recycle(path)
        elif IS_MAC:
            recycled = _mac_trash(path)
        else:
            recycled = _linux_trash(path)

        try:
            if not path.exists():
                # 文件确实没了。只有 API 明确报成功才算「进回收站」，
                # 否则按永久删除上报，不给用户错误的心理预期。
                return "trash" if recycled else "delete"
        except OSError:
            pass

    path.unlink(missing_ok=True)
    return "delete"


def restore_origin(group) -> str:
    """把 原名#origin.扩展名 改回 原名.扩展名，返回结果描述。"""
    origin = Path(group["origin"])
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


def apply_undo(folder: Path, *, recursive: bool = True, source_dir: str = "origin",
               delete_slices: bool = True, restore: bool = True,
               trash: bool = True, ffprobe=None) -> dict:
    """
    真正执行撤销。返回结果报告。

    对校验不通过的组：切片一个都不删，但原片改名照做——改名不是破坏性操作，
    随时可以改回去；而切片缺失时还把原片留在 #origin 名下只会让目录更乱。
    """
    folder = Path(folder)
    groups, orphans = scan_groups(folder, recursive, source_dir, ffprobe)

    deleted = restored = skipped = 0
    trashed = 0
    freed = 0
    problems = []
    details = []

    for g in groups:
        if not g["ok"]:
            skipped += 1
            problems.append("%s：%s" % (g["base"], g["reason"]))
            msg = "校验不通过，未删除任何切片"
            if restore:
                r = restore_origin(g)
                msg += "；" + r
                if r.startswith("原片已恢复"):
                    restored += 1
            details.append({"base": g["base"], "action": "skipped", "message": msg})
            continue

        if delete_slices:
            failed = []
            group_trashed = 0
            slice_bytes = {}
            # 体积必须在删除前先记下来——删完再 stat 当然只能得到 0
            for s in g["slices"]:
                p = Path(s["path"])
                try:
                    slice_bytes[p] = p.stat().st_size
                except OSError:
                    slice_bytes[p] = 0
            for s in g["slices"]:
                p = Path(s["path"])
                try:
                    how = delete_file(p, trash)
                    deleted += 1
                    if how == "trash":
                        trashed += 1
                        group_trashed += 1
                    freed += slice_bytes[p]
                except Exception as exc:          # noqa: BLE001
                    failed.append("%s（%s）" % (p.name, exc))
            if failed:
                problems.append("%s 切片删除失败" % g["base"])
                details.append({"base": g["base"], "action": "error",
                                "message": "部分切片删除失败：%s，原片保持 #origin 不动"
                                           % "、".join(failed)})
                continue

        action = "deleted" if delete_slices else ""
        msg = []
        if delete_slices:
            # 回收站不是到处都有（精简容器里就没有），所以必须如实说明
            # 到底是「还能找回」还是「已经没了」，不能让用户误以为有后悔药。
            if trash and group_trashed == len(g["slices"]):
                way = "已移入回收站"
            elif trash and group_trashed == 0:
                way = "已永久删除（当前环境没有可用的回收站）"
            elif trash:
                way = "已删除（%d 个进回收站，%d 个永久删除）" % (
                    group_trashed, len(g["slices"]) - group_trashed)
            else:
                way = "已永久删除"
            msg.append("%s %d 个切片，释放 %s"
                       % (way, len(g["slices"]),
                          engine.human_size(sum(s["size"] for s in g["slices"]))))
        if restore:
            r = restore_origin(g)
            msg.append(r)
            if r.startswith("原片已恢复"):
                restored += 1
                action += "+restored"
            elif "未改名" in r or "失败" in r:
                problems.append("%s %s" % (g["base"], r))
        details.append({"base": g["base"], "action": action or "noop",
                        "message": "；".join(msg)})

    if orphans:
        problems.append("%d 个孤儿切片未处理（找不到对应原片，为安全起见不动）"
                        % len(orphans))

    return {
        "deleted": deleted,
        "trashed": trashed,
        "restored": restored,
        "skipped": skipped,
        "freedBytes": freed,
        "problems": problems,
        "details": details,
        "orphans": [str(o) for o in orphans],
    }

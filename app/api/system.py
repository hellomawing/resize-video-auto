#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/api/system.py —— 健康检查、统计、目录浏览
"""

from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from .. import config, db
from ..models import BrowseOut, DirItem, DirShortcut, HealthOut, StatsOut, ToolInfo
from core import splitter as engine

router = APIRouter(tags=["system"])
_STARTED = time.time()


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    ffmpeg = engine.find_bin("ffmpeg")
    ffprobe = engine.find_bin("ffprobe")
    return HealthOut(
        ok=True,
        version=config.APP_VERSION,
        python=platform.python_version(),
        ffmpeg=ToolInfo(path=ffmpeg, version=engine.tool_version(ffmpeg),
                        ok=bool(ffmpeg)),
        ffprobe=ToolInfo(path=ffprobe, version=engine.tool_version(ffprobe),
                         ok=bool(ffprobe)),
        time=db.now_iso(),
        uptime_sec=round(time.time() - _STARTED, 1),
    )


@router.get("/stats", response_model=StatsOut)
def stats() -> StatsOut:
    summary = db.stats_summary()
    return StatsOut(
        jobs=summary["jobs"],
        watchpoints=len(config.load_watchpoints()),
        today_bytes=summary["todayBytes"],
        total_bytes=summary["totalBytes"],
        total_parts=summary["totalParts"],
    )


# ---------------------------------------------------------------- 目录浏览

def _allowed_roots() -> list:
    settings = config.load_settings()
    roots = []
    for raw in settings["watch"].get("allowedRoots") or []:
        try:
            roots.append(Path(raw).resolve())
        except Exception:
            continue
    return roots


def _within(path: Path, roots) -> bool:
    """路径是否落在某个白名单根目录之内（含根本身）。"""
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def ensure_allowed(path: Path, roots=None) -> Path:
    """
    确认路径落在白名单根目录内。这是网页上所有「用户给路径」的入口
    都必须过的一道闸——否则容器把 NAS 整盘挂进来了，等于把整个文件系统
    暴露成一个可读接口。
    """
    roots = roots if roots is not None else _allowed_roots()
    if not roots:
        raise HTTPException(status_code=400, detail="尚未配置可访问根目录，请先在设置里填写")
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="路径无法解析：%s" % exc)
    if _within(resolved, roots):
        return resolved
    raise HTTPException(
        status_code=403,
        detail="路径 %s 不在可访问范围内。允许的根目录：%s"
               % (resolved, "、".join(str(r) for r in roots)))


def _collect_shortcuts(settings, roots) -> list:
    """目录选择器的「常用目录」。详见 models.DirShortcut 的注释。

    三个来源，按「用户最可能想去」排序：
      1. 已添加的监控目录 —— 撤销与重切的主战场，备注顺手当说明
      2. 最近任务出现过的目录 —— 手动扫过、切过的地方
      3. 系统设置里的输出目录 —— 哪怕只用过一次也得看得见

    白名单之外的目录不给入口：点了也是 403，摆出来只会让人白跑一趟。
    """
    items, seen = [], set()

    def add(raw, name, kind, note=""):
        raw = (raw or "").strip()
        if not raw:
            return
        try:
            resolved = Path(raw).resolve()
        except OSError:
            return
        key = str(resolved)
        if key in seen or not _within(resolved, roots):
            return
        seen.add(key)
        items.append(DirShortcut(name=name or resolved.name or key, path=key,
                                 kind=kind, note=note))

    for wp in config.load_watchpoints():
        path = wp.get("path") or ""
        add(path, Path(path).name, "watchpoint", wp.get("note") or "")
    # 读库失败不该让整个目录列表挂掉 —— 快捷入口是锦上添花，不是必需品
    try:
        for raw in db.recent_job_dirs():
            add(raw, Path(raw).name, "job", "最近处理过")
    except Exception:                                  # noqa: BLE001
        pass
    outdir = settings["split"].get("outdir") or ""
    add(outdir, Path(outdir).name, "setting", "系统设置里的输出目录")
    return items


@router.get("/browse", response_model=BrowseOut)
def browse(path: str = Query(default=None, description="要浏览的目录，省略则返回各根目录")):
    roots = _allowed_roots()
    settings = config.load_settings()
    shortcuts = _collect_shortcuts(settings, roots)

    # 存在的根才放进 roots（下拉里可选），不存在的单独报出来。
    # 系统默认白名单是 /vol1~4，但真机上往往只有 /vol1 —— 把 /vol2~4
    # 摆在选择器里，用户点一下只会得到一句「目录不存在」。
    live_roots, missing_roots = [], []
    for r in roots:
        (live_roots if r.is_dir() else missing_roots).append(str(r))
    root_texts = live_roots or [str(r) for r in roots]

    if not path:
        dirs, error = [], None
        dirs = [DirItem(name=Path(r).name or r, path=r) for r in live_roots]
        if not dirs:
            error = ("配置的可访问根目录都不存在：%s。"
                     "容器里请确认这些路径已经挂载进来"
                     % "、".join(str(r) for r in roots))
        return BrowseOut(path="", parent=None, roots=root_texts,
                         missing_roots=missing_roots, dirs=dirs,
                         shortcuts=shortcuts, video_count=0, error=error)

    target = ensure_allowed(Path(path), roots)

    if not target.is_dir():
        return BrowseOut(path=str(target), parent=str(target.parent),
                         roots=root_texts, missing_roots=missing_roots,
                         dirs=[], shortcuts=shortcuts, video_count=0,
                         error="目录不存在，或者容器没有权限访问它"
                               "（确认路径拼写、以及它是否已挂载进容器）")

    exts = set(engine.normalize_exts(settings["split"].get("ext")))
    dirs, videos, error = [], 0, None
    try:
        with os.scandir(target) as it:
            for entry in it:
                try:
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(DirItem(name=entry.name, path=entry.path))
                    elif Path(entry.name).suffix.lower() in exts:
                        videos += 1
                except OSError:
                    continue
    except PermissionError:
        # 这一条真机上必然踩到：fnOS 的存储池根 /vol1 权限位是 000、
        # 没有扩展 ACL，readdir 直接 EACCES，但**访问它下面的目录完全正常**。
        # 所以别说一句「没有权限」就完事，得给出路 —— 否则用户会以为
        # 是自己配置错了，或者以为整块盘都读不了。
        error = ("没有权限列出该目录的子目录。请注意：这并不代表它下面的目录不可用"
                 " —— 像 fnOS 就把存储池根目录（/vol1）故意设成不可枚举，"
                 "但直接访问它下面的路径（如 /vol1/1000/…）完全正常。"
                 "请用上方的「常用目录」直达已知目录（已添加的监控目录、"
                 "最近任务目录、系统输出目录都在里面）；"
                 "要添加一个全新的目录，请先到「设置 → 可访问根目录白名单」"
                 "把根改到可枚举的层，例如 /vol1/1000。")
    except OSError as exc:
        error = "读取目录失败：%s" % exc

    # @ 开头的是系统目录（@appdata、@appshare 之类），排到后面去
    dirs.sort(key=lambda d: (d.name.startswith("@"), d.name.lower()))
    parent = str(target.parent) if target.parent != target else None
    return BrowseOut(path=str(target), parent=parent, roots=root_texts,
                     missing_roots=missing_roots, dirs=dirs,
                     shortcuts=shortcuts, video_count=videos, error=error)


@router.get("/env")
def env_info() -> dict:
    """调试用：看看容器里的关键环境，排查挂载/权限问题很有用。"""
    return {
        "python": sys.executable,
        "pythonVersion": platform.python_version(),
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "dataDir": str(config.DATA_DIR),
        "dataDirWritable": os.access(config.DATA_DIR, os.W_OK),
        "ffmpeg": engine.find_bin("ffmpeg"),
        "ffprobe": engine.find_bin("ffprobe"),
        "allowedRoots": [
            {"path": str(r), "exists": r.is_dir(), "writable": os.access(r, os.W_OK)}
            for r in _allowed_roots()
        ],
        "uid": os.getuid() if hasattr(os, "getuid") else None,
        "gid": os.getgid() if hasattr(os, "getgid") else None,
    }

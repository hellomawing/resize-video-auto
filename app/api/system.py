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
from ..models import BrowseOut, DirItem, HealthOut, StatsOut, ToolInfo
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
        schedules=len(config.load_schedules()),
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
    for root in roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise HTTPException(
        status_code=403,
        detail="路径 %s 不在可访问范围内。允许的根目录：%s"
               % (resolved, "、".join(str(r) for r in roots)))


@router.get("/browse", response_model=BrowseOut)
def browse(path: str = Query(default=None, description="要浏览的目录，省略则返回各根目录")):
    roots = _allowed_roots()
    root_texts = [str(r) for r in roots]

    if not path:
        dirs, error = [], None
        for r in roots:
            if r.is_dir():
                dirs.append(DirItem(name=r.name or str(r), path=str(r)))
        if not dirs:
            error = ("配置的可访问根目录都不存在：%s。"
                     "容器里请确认这些路径已经挂载进来" % "、".join(root_texts))
        return BrowseOut(path="", parent=None, roots=root_texts,
                         dirs=dirs, video_count=0, error=error)

    target = ensure_allowed(Path(path), roots)

    if not target.is_dir():
        return BrowseOut(path=str(target), parent=str(target.parent),
                         roots=root_texts, dirs=[], video_count=0,
                         error="目录不存在或没有访问权限")

    settings = config.load_settings()
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
        error = "没有权限读取该目录"
    except OSError as exc:
        error = "读取目录失败：%s" % exc

    # @ 开头的是系统目录（@appdata、@appshare 之类），排到后面去
    dirs.sort(key=lambda d: (d.name.startswith("@"), d.name.lower()))
    parent = str(target.parent) if target.parent != target else None
    return BrowseOut(path=str(target), parent=parent, roots=root_texts,
                     dirs=dirs, video_count=videos, error=error)


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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/api/undo.py —— 撤销分割

这是本服务里唯一会删文件的接口，所以设计上刻意保守：
  * 预览与执行分离，UI 必须先看到「哪一组能撤销、依据是什么」
  * 校验不通过的组，一个切片都不删
  * 默认走回收站/废纸篓，而不是直接 unlink
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import config
from ..models import (UndoApplyIn, UndoApplyOut, UndoDetail, UndoGroup,
                      UndoLoneOrigin, UndoPreviewIn, UndoPreviewOut)
from .system import ensure_allowed
from core import splitter as engine
from core import undo

router = APIRouter(prefix="/undo", tags=["undo"])


def _resolve_dir(raw: str) -> Path:
    target = ensure_allowed(Path(raw))
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="目录不存在或没有访问权限：%s" % target)
    return target


@router.post("/preview", response_model=UndoPreviewOut)
def preview(payload: UndoPreviewIn) -> UndoPreviewOut:
    target = _resolve_dir(payload.path)
    settings = config.load_settings()
    ffprobe = engine.find_bin("ffprobe")

    groups, orphans, origin_only = undo.scan_groups(
        target, recursive=payload.recursive,
        # 把所有可能的归档目录名都算上：它随监控目录/系统设置变化，
        # 只认当前那一个名字的话，早先归档走的原片就配不上对了
        source_dir=config.collect_archive_dirs(settings),
        ffprobe=ffprobe)

    ok_count = sum(1 for g in groups if g["ok"])
    return UndoPreviewOut(
        path=str(target),
        groups=[UndoGroup(**g) for g in groups],
        origin_only=[UndoLoneOrigin(**o) for o in origin_only],
        orphans=[str(o) for o in orphans],
        ok_count=ok_count,
        bad_count=len(groups) - ok_count)


@router.post("/apply", response_model=UndoApplyOut)
def apply(payload: UndoApplyIn) -> UndoApplyOut:
    target = _resolve_dir(payload.path)
    if not (payload.delete_slices or payload.restore_origin
            or payload.restore_origin_only):
        raise HTTPException(
            status_code=400,
            detail="没有勾选任何要执行的操作（删除切片 / 恢复原片名 / 恢复无切片原片名）")

    settings = config.load_settings()
    ffprobe = engine.find_bin("ffprobe")
    report = undo.apply_undo(
        target, recursive=payload.recursive,
        source_dir=config.collect_archive_dirs(settings),
        delete_slices=payload.delete_slices,
        restore=payload.restore_origin,
        restore_origin_only=payload.restore_origin_only,
        trash=payload.trash,
        ffprobe=ffprobe)

    return UndoApplyOut(
        deleted=report["deleted"],
        trashed=report["trashed"],
        restored=report["restored"],
        restored_orphans=report["restoredOrphans"],
        skipped=report["skipped"],
        freed_bytes=report["freedBytes"],
        problems=report["problems"],
        details=[UndoDetail(**d) for d in report["details"]],
        orphans=report["orphans"])

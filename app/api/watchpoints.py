#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/api/watchpoints.py —— 监控目录的增删改查
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import config, db
from ..models import ScanResult, WatchPoint, WatchPointCreate, WatchPointUpdate
from ..services import scanner
from ..services.monitor import monitor as monitor_service
from .system import ensure_allowed

router = APIRouter(prefix="/watchpoints", tags=["watchpoints"])


def _find(watchpoints: list, wp_id: str) -> dict:
    for wp in watchpoints:
        if wp.get("id") == wp_id:
            return wp
    raise HTTPException(status_code=404, detail="监控目录不存在")


@router.get("", response_model=list[WatchPoint])
def list_watchpoints() -> list:
    return [WatchPoint(**wp) for wp in config.load_watchpoints()]


@router.post("", response_model=WatchPoint)
def create_watchpoint(payload: WatchPointCreate) -> WatchPoint:
    target = ensure_allowed(Path(payload.path))
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="目录不存在或没有访问权限：%s" % target)

    watchpoints = config.load_watchpoints()
    if any(Path(wp["path"]).resolve() == target for wp in watchpoints):
        raise HTTPException(status_code=409, detail="该目录已经在监控列表里了")

    item = {
        "id": "wp_" + uuid.uuid4().hex[:8],
        "path": str(target),
        "recursive": bool(payload.recursive),
        "enabled": True,
        "note": payload.note or "",
        "createdAt": db.now_iso(),
        "lastScanAt": None,
        "videoCount": 0,
    }
    watchpoints.append(item)
    config.save_watchpoints(watchpoints)
    monitor_service.reload()
    return WatchPoint(**item)


@router.put("/{wp_id}", response_model=WatchPoint)
def update_watchpoint(wp_id: str, payload: WatchPointUpdate) -> WatchPoint:
    watchpoints = config.load_watchpoints()
    item = _find(watchpoints, wp_id)

    if payload.recursive is not None:
        item["recursive"] = bool(payload.recursive)
    if payload.enabled is not None:
        item["enabled"] = bool(payload.enabled)
    if payload.note is not None:
        item["note"] = payload.note

    config.save_watchpoints(watchpoints)
    monitor_service.reload()
    return WatchPoint(**item)


@router.delete("/{wp_id}")
def delete_watchpoint(wp_id: str) -> dict:
    watchpoints = config.load_watchpoints()
    _find(watchpoints, wp_id)
    remaining = [wp for wp in watchpoints if wp.get("id") != wp_id]
    config.save_watchpoints(remaining)
    monitor_service.reload()
    return {"ok": True, "message": "已移除该监控目录（不会删除磁盘上的任何文件）"}


@router.post("/{wp_id}/scan", response_model=ScanResult)
def scan_watchpoint(wp_id: str) -> ScanResult:
    """
    立即扫描一次。注意：仍然会走文件稳定检测，正在拷贝的文件会被延后处理，
    这不是卡住了，而是在保护你的数据。
    """
    watchpoints = config.load_watchpoints()
    item = _find(watchpoints, wp_id)
    if not Path(item["path"]).is_dir():
        raise HTTPException(status_code=400, detail="目录已不存在：%s" % item["path"])
    result = scanner.scan_watchpoint(item, trigger="manual")
    return ScanResult(found=result["found"], queued=result["queued"],
                      skipped=result["skipped"], message=result["message"])

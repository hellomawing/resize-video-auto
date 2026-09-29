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
from ..models import (FilterListPreviewIn, FilterListPreviewOut,
                      FilterPreviewIn, FilterPreviewOut, ScanIn, ScanResult,
                      WatchPoint, WatchPointCreate, WatchPointUpdate)
from ..services import filters as filter_rules
from ..services import scanner, scheduler
from ..services.monitor import monitor as monitor_service
from core import splitter as engine
from .system import ensure_allowed

router = APIRouter(prefix="/watchpoints", tags=["watchpoints"])


def _find(watchpoints: list, wp_id: str) -> dict:
    for wp in watchpoints:
        if wp.get("id") == wp_id:
            return wp
    raise HTTPException(status_code=404, detail="监控目录不存在")


def _to_model(item: dict) -> WatchPoint:
    """补上 nextScanAt —— 它不落盘，而是每次查询时按当前扫描计划算出来的。"""
    return WatchPoint(**item, next_scan_at=scheduler.next_scan_time(item["id"]))


def _apply(watchpoints: list) -> None:
    """
    保存监控目录并让后台重新装配。

    两件事必须同时做：monitor 管实时监听那一路，scheduler 管定时扫描那一路，
    少叫一个就会出现「界面上改了、后台还在按老规矩扫」。
    """
    config.save_watchpoints(watchpoints)
    monitor_service.reload()
    scheduler.reload_watchpoint_jobs()


def _validated_filters(payload) -> dict:
    """
    归一化请求里的过滤规则，写错了当场 400。

    为什么要在这儿严格校验、而不是丢给落盘时的容错归一化：正则写错是**用户
    当场能改**的问题，静默丢掉那一行会让他以为规则生效了，结果文件照旧被切。
    落盘那一层仍然保留静默剔除（防手工改坏配置文件），两层各管一件事。
    """
    raw = payload.model_dump(by_alias=True) if payload is not None else {}
    try:
        return config.normalize_filters(raw, strict=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("", response_model=list[WatchPoint])
def list_watchpoints() -> list:
    return [_to_model(wp) for wp in config.load_watchpoints()]


# 刻意声明在 /{wp_id} 系列路由**之前**：路径段数虽然不同、现在还撞不上，
# 但先占住这个具体名字，将来万一加了 POST /{wp_id} 也不会把它吃掉
@router.post("/filter-preview", response_model=FilterPreviewOut)
def preview_filters(payload: FilterPreviewIn) -> FilterPreviewOut:
    """
    试算一条样例路径会不会被这套规则挡下 —— 编辑页「命中预览」用的。

    纯计算：不读磁盘、不写配置、不碰扫描队列，可以随便点。
    规则怎么算完全交给 services.filters（与真实扫描同一份逻辑），
    这里只负责把规则归一化一遍，好让坏正则有个说法。
    """
    raw = payload.filters.model_dump(by_alias=True) if payload.filters else {}
    try:
        normalized = config.normalize_filters(raw, strict=True)
    except ValueError as exc:
        # 正则编译不过：预览照样 200，由 message 说明是哪一条
        return FilterPreviewOut(ok=False, message=str(exc))
    return FilterPreviewOut(
        **filter_rules.preview(normalized, payload.path, payload.base_path))


@router.post("/filter-list-preview", response_model=FilterListPreviewOut)
def preview_filter_list(payload: FilterListPreviewIn) -> FilterListPreviewOut:
    """
    对监控目录下**已存在文件**做命中预览 —— 编辑页「命中预览」的列表版。

    只读：不写配置、不入队、不碰扫描队列。列出的候选视频口径与扫描一致
    （引擎能处理的扩展名，由系统设置决定），逐文件走与真实扫描同一份规则
    判定（services.filters.explain），所以「预览说会处理就真的会处理」。
    递归与否由前端按监控目录的递归开关传入。
    """
    raw = payload.filters.model_dump(by_alias=True) if payload.filters else {}
    try:
        normalized = config.normalize_filters(raw, strict=True)
    except ValueError as exc:
        # 正则编译不过：预览照样 200，由 message 说明是哪一条
        return FilterListPreviewOut(ok=False, message=str(exc))

    settings = config.load_settings()
    exts = set(engine.normalize_exts(settings["split"].get("ext")))
    return FilterListPreviewOut(
        **filter_rules.preview_list(normalized, payload.path,
                                    payload.recursive, exts))


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
        "scanMode": payload.scan_mode,
        "scanIntervalHours": payload.scan_interval_hours,
        "scanTime": payload.scan_time,
        # 空串 = 跟随系统设置（不是「没填」）
        "markSource": payload.mark_source or "",
        "sourceDir": payload.source_dir or "",
        "note": payload.note or "",
        # 只看这些 / 不看这些（空 = 不过滤）。规则写错在这里就 400
        "filters": _validated_filters(payload.filters),
        "createdAt": db.now_iso(),
        "lastScanAt": None,
        "videoCount": 0,
    }
    watchpoints.append(item)
    _apply(watchpoints)
    return _to_model(_find(config.load_watchpoints(), item["id"]))


@router.put("/{wp_id}", response_model=WatchPoint)
def update_watchpoint(wp_id: str, payload: WatchPointUpdate) -> WatchPoint:
    watchpoints = config.load_watchpoints()
    item = _find(watchpoints, wp_id)

    if payload.recursive is not None:
        item["recursive"] = bool(payload.recursive)
    if payload.scan_mode is not None:
        item["scanMode"] = payload.scan_mode
    if payload.scan_interval_hours is not None:
        item["scanIntervalHours"] = int(payload.scan_interval_hours)
    if payload.scan_time is not None:
        item["scanTime"] = payload.scan_time
    # 这里判的是 None 而不是空串：空串是有意义的取值，意思正好相反 ——
    # 「清掉本目录的覆盖，改回跟随系统设置」
    if payload.mark_source is not None:
        item["markSource"] = payload.mark_source
    if payload.source_dir is not None:
        item["sourceDir"] = payload.source_dir
    if payload.note is not None:
        item["note"] = payload.note
    # 过滤规则整块替换：传 {} 就是「清空全部规则，回到不过滤」。
    # 不用 None 表达清空，理由与 markSource 那两项相同 —— 两者语义不同
    if payload.filters is not None:
        item["filters"] = _validated_filters(payload.filters)

    _apply(watchpoints)
    # 回读一次：保存时会顺手纠正非法值（比如填了个 7 小时），
    # 把纠正后的结果返回给前端，界面显示的和真正生效的就永远一致
    return _to_model(_find(config.load_watchpoints(), wp_id))


@router.delete("/{wp_id}")
def delete_watchpoint(wp_id: str) -> dict:
    watchpoints = config.load_watchpoints()
    _find(watchpoints, wp_id)
    remaining = [wp for wp in watchpoints if wp.get("id") != wp_id]
    _apply(remaining)
    return {"ok": True, "message": "已移除该监控目录（不会删除磁盘上的任何文件）"}


@router.post("/{wp_id}/scan", response_model=ScanResult)
def scan_watchpoint(wp_id: str, payload: ScanIn | None = None) -> ScanResult:
    """
    立即扫描一次 —— 这是「手动分割」的入口，刻意不做任何 scanMode 判断：
    哪怕这个目录设成「仅手动」，用户点它就该扫。

    body 可选：临时指定「这一次」原片怎么处理，优先级高于本目录的设置。
    不传就按本目录设置，再退回系统默认值。

    注意仍然会走文件稳定检测，正在拷贝的文件会被延后处理，这不是卡住了，
    而是在保护你的数据。
    """
    watchpoints = config.load_watchpoints()
    item = _find(watchpoints, wp_id)
    if not Path(item["path"]).is_dir():
        raise HTTPException(status_code=400, detail="目录已不存在：%s" % item["path"])
    override = payload.model_dump(by_alias=True) if payload else None
    result = scanner.scan_watchpoint(item, trigger="manual",
                                     mark_override=override)
    return ScanResult.from_engine(result)

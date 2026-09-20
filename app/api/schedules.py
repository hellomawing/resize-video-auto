#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/api/schedules.py —— 定时任务的增删改查
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import config, db
from ..models import Schedule, ScheduleCreate, ScheduleUpdate
from ..services import scheduler

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _find(schedules: list, sid: str) -> dict:
    for sc in schedules:
        if sc.get("id") == sid:
            return sc
    raise HTTPException(status_code=404, detail="定时任务不存在")


def _decorate(item: dict) -> dict:
    """补上前端要展示的两个计算字段：中文描述与下次执行时间。"""
    out = dict(item)
    out["cronText"] = scheduler.describe_cron(item.get("cron", ""))
    out["nextRunAt"] = scheduler.next_run_time(item.get("id")) if item.get("enabled", True) else None
    return out


def _validate_watchpoints(ids) -> list:
    """过滤掉已经被删掉的监控目录 id，避免留下悬空引用。"""
    if not ids:
        return []
    existing = {wp.get("id") for wp in config.load_watchpoints()}
    return [i for i in ids if i in existing]


@router.get("", response_model=list[Schedule])
def list_schedules() -> list:
    return [Schedule(**_decorate(sc)) for sc in config.load_schedules()]


@router.post("", response_model=Schedule)
def create_schedule(payload: ScheduleCreate) -> Schedule:
    try:
        scheduler.validate_cron(payload.cron)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not (payload.name or "").strip():
        raise HTTPException(status_code=400, detail="请给定时任务起个名字")

    schedules = config.load_schedules()
    item = {
        "id": "sc_" + uuid.uuid4().hex[:8],
        "name": payload.name.strip(),
        "cron": payload.cron.strip(),
        "enabled": bool(payload.enabled),
        "watchpointIds": _validate_watchpoints(payload.watchpoint_ids),
        "lastRunAt": None,
    }
    schedules.append(item)
    config.save_schedules(schedules)
    scheduler.reload_schedules()
    return Schedule(**_decorate(item))


@router.put("/{sid}", response_model=Schedule)
def update_schedule(sid: str, payload: ScheduleUpdate) -> Schedule:
    schedules = config.load_schedules()
    item = _find(schedules, sid)

    if payload.cron is not None:
        try:
            scheduler.validate_cron(payload.cron)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        item["cron"] = payload.cron.strip()
    if payload.name is not None:
        if not payload.name.strip():
            raise HTTPException(status_code=400, detail="名称不能为空")
        item["name"] = payload.name.strip()
    if payload.enabled is not None:
        item["enabled"] = bool(payload.enabled)
    if payload.watchpoint_ids is not None:
        item["watchpointIds"] = _validate_watchpoints(payload.watchpoint_ids)

    config.save_schedules(schedules)
    scheduler.reload_schedules()
    return Schedule(**_decorate(item))


@router.delete("/{sid}")
def delete_schedule(sid: str) -> dict:
    schedules = config.load_schedules()
    _find(schedules, sid)
    config.save_schedules([sc for sc in schedules if sc.get("id") != sid])
    scheduler.reload_schedules()
    return {"ok": True, "message": "已删除该定时任务"}


@router.post("/{sid}/run")
def run_schedule_now(sid: str) -> dict:
    schedules = config.load_schedules()
    _find(schedules, sid)
    ok, message = scheduler.run_now(sid)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


@router.get("/presets")
def cron_presets() -> list:
    """给前端用的常用表达式预设，避免用户对着 cron 语法发呆。"""
    presets = [
        ("*/30 * * * *", "每 30 分钟"),
        ("0 * * * *", "每小时整点"),
        ("0 */6 * * *", "每 6 小时"),
        ("0 3 * * *", "每天凌晨 3 点"),
        ("0 2 * * 0", "每周日凌晨 2 点"),
        ("0 4 1 * *", "每月 1 日凌晨 4 点"),
    ]
    return [{"cron": expr, "text": scheduler.describe_cron(expr) or text}
            for expr, text in presets]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/api/settings.py —— 切分/监控/服务参数的读写
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import config
from ..models import Settings
from ..services import scheduler
from ..services.events import bus
# 注意：要的是模块里那个单例，不是模块本身
from ..services.monitor import monitor as monitor_service

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=Settings)
def read_settings() -> Settings:
    return Settings(**config.load_settings())


@router.put("", response_model=Settings)
def write_settings(payload: Settings) -> Settings:
    """
    保存设置后要立刻让改动生效：
      * 监控服务重新装配（允许根目录、实时监听开关可能变了）
      * 监控目录的扫描计划重排（时区可能变了，cron 的触发时刻跟着变）
    目录白名单改了之后，正在监听的目录如果不再合法，也应该被摘掉。
    """
    saved = config.save_settings(payload.model_dump(by_alias=True))
    monitor_service.reload()
    scheduler.reload_watchpoint_jobs()
    bus.publish({"type": "settings.updated"})
    return Settings(**saved)

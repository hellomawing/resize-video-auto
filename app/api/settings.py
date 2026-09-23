#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/api/settings.py —— 切分/监控/服务参数的读写
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import config, db
from ..models import ConfigBundle, ImportResult, Settings
from ..services import scheduler
from ..services.events import bus
# 注意：要的是模块里那个单例，不是模块本身
from ..services.monitor import monitor as monitor_service
from .system import ensure_allowed

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=Settings)
def read_settings() -> Settings:
    return Settings(**config.load_settings())


@router.put("", response_model=Settings)
def write_settings(payload: Settings) -> Settings:
    """
    保存设置后要立刻让改动生效：
      * 监控服务重新装配（实时监听开关、稳定检测参数可能变了）
      * 监控目录的扫描计划重排（时区可能变了，cron 的触发时刻跟着变）
    """
    saved = config.save_settings(payload.model_dump(by_alias=True))
    monitor_service.reload()
    scheduler.reload_watchpoint_jobs()
    bus.publish({"type": "settings.updated"})
    return Settings(**saved)


@router.get("/export", response_model=ConfigBundle)
def export_config() -> ConfigBundle:
    """
    导出配置，供备份或换机器时用。

    只导出「配置」不导出「运行数据」：设置、监控目录、归档目录记录。
    任务历史（video-splitter.db）不在这里 —— 换机器一般不需要搬历史，
    真要连历史一起搬，请整卷打包（README「数据目录」一节）。
    """
    return ConfigBundle(
        version=1,
        exported_at=db.now_iso(),
        settings=config.load_settings(),
        watchpoints=config.load_watchpoints(),
        archive_dirs=sorted(config.load_known_archive_dirs()),
    )


@router.post("/import", response_model=ImportResult)
def import_config(payload: ConfigBundle) -> ImportResult:
    """
    导入配置。三块内容的合并语义刻意不同：

      * 设置 —— 整体替换。用户导的就是一台机器的完整设置，逐项合并没有意义。
      * 监控目录 —— 按**路径**合并（已存在的更新成导入内容，没有的新增）。
        不能按 id 合并：换机器后 id 必然对不上，按 id 判重会把整份重复添加一遍。
        本机侧的运行时字段（创建时间、上次扫描、视频数）保留，
        导入时也不覆盖已有的 id。
      * 归档目录记录 —— **只增不减**，和运行时记名的语义保持一致。
        少记一个名字就可能把归档里的原片重新切一遍，所以宁可多记。

    路径不在容器已挂载目录内的监控目录会被跳过并计数，不会让整份导入失败 ——
    换机器后挂载不同是很常见的，为一条路径否定整份备份没有道理。
    """
    data = payload.model_dump(by_alias=True)

    applied = False
    incoming = data.get("settings")
    if isinstance(incoming, dict) and incoming:
        config.save_settings(incoming)
        applied = True

    current = config.load_watchpoints()
    by_path = {}
    for wp in current:
        path = str(wp.get("path") or "").rstrip("/")
        if path:
            by_path[path] = wp

    added = updated = skipped = 0
    for raw in data.get("watchpoints") or []:
        if not isinstance(raw, dict):
            continue
        item = config.normalize_watchpoint(raw)
        if not (item.get("path") or "").strip():
            continue
        try:
            target = ensure_allowed(Path(item["path"]))
        except (HTTPException, OSError):
            skipped += 1
            continue

        item["path"] = str(target)
        exist = by_path.get(str(target))
        if exist is None:
            item["id"] = item.get("id") or ("wp_" + uuid.uuid4().hex[:8])
            item["createdAt"] = item.get("createdAt") or db.now_iso()
            item.setdefault("lastScanAt", None)
            item.setdefault("videoCount", 0)
            current.append(item)
            by_path[str(target)] = item
            added += 1
        else:
            # 保留本机的 id 与运行时字段，其余按导入内容更新
            keep_id = exist.get("id")
            runtime = {k: exist.get(k)
                       for k in ("createdAt", "lastScanAt", "videoCount")}
            merged = dict(item)
            merged.update(runtime)
            merged["id"] = keep_id
            exist.clear()
            exist.update(merged)
            updated += 1

    known_before = config.load_known_archive_dirs()
    names = [n for n in (data.get("archiveDirs") or [])
             if isinstance(n, str) and n.strip()]
    merged_dirs = config.remember_archive_dirs(names)
    dirs_added = len(merged_dirs - known_before)

    if added or updated:
        config.save_watchpoints(current)
    monitor_service.reload()
    scheduler.reload_watchpoint_jobs()
    bus.publish({"type": "settings.updated"})

    parts = []
    if applied:
        parts.append("设置已替换")
    parts.append("监控目录新增 %d、更新 %d" % (added, updated))
    if skipped:
        parts.append("跳过 %d（路径不在容器已挂载的目录内）" % skipped)
    parts.append("归档记录新增 %d" % dirs_added)
    return ImportResult(settings_applied=applied, watchpoints_added=added,
                        watchpoints_updated=updated, watchpoints_skipped=skipped,
                        archive_dirs_added=dirs_added,
                        message="导入完成：" + "；".join(parts))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/models.py —— 对外 JSON 的请求/响应模型

字段命名：Python 里用 snake_case，对外一律 camelCase。
靠 alias_generator 自动转换，不手写别名，也不在业务代码里手动拼字符串。
（docs/api.md 是唯一约定，改这里必须同步改文档。）
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# ---------------------------------------------------------------- 系统

class ToolInfo(CamelModel):
    path: Optional[str] = None
    version: Optional[str] = None
    ok: bool = False


class HealthOut(CamelModel):
    ok: bool
    version: str
    python: str
    ffmpeg: ToolInfo
    ffprobe: ToolInfo
    time: str
    uptime_sec: float


class JobCounts(CamelModel):
    queued: int = 0
    running: int = 0
    success: int = 0
    failed: int = 0
    canceled: int = 0
    skipped: int = 0


class StatsOut(CamelModel):
    jobs: JobCounts
    watchpoints: int = 0
    schedules: int = 0
    today_bytes: int = 0
    total_bytes: int = 0
    total_parts: int = 0


class DirItem(CamelModel):
    name: str
    path: str


class BrowseOut(CamelModel):
    path: str
    parent: Optional[str] = None
    roots: list[str] = Field(default_factory=list)
    dirs: list[DirItem] = Field(default_factory=list)
    video_count: int = 0
    error: Optional[str] = None


# ---------------------------------------------------------------- 设置

class SplitSettings(CamelModel):
    mode: Literal["auto", "copy", "bytes"] = "auto"
    by_size: bool = True
    size: str = "3.9G"
    seconds: float = 300
    all: bool = False
    ext: list[str] = Field(default_factory=list)
    recursive: bool = True
    outdir_mode: Literal["same", "custom"] = "same"
    outdir: str = ""
    mark_source: Literal["rename", "move", "none", "delete"] = "rename"
    source_dir: str = "origin"
    keep_metadata: bool = True
    overwrite: bool = False
    debug: bool = False


class WatchSettings(CamelModel):
    realtime: bool = True
    poll_interval: int = 30
    settle_seconds: int = 60
    min_size: str = "0"
    ignore_suffixes: list[str] = Field(default_factory=list)
    allowed_roots: list[str] = Field(default_factory=list)


class ServerSettings(CamelModel):
    host: str = "0.0.0.0"
    port: int = 8099
    job_log_lines: int = 2000


class Settings(CamelModel):
    split: SplitSettings = Field(default_factory=SplitSettings)
    watch: WatchSettings = Field(default_factory=WatchSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)


# ---------------------------------------------------------------- 监控目录

class WatchPoint(CamelModel):
    id: str
    path: str
    recursive: bool = True
    enabled: bool = True
    note: str = ""
    created_at: str = ""
    last_scan_at: Optional[str] = None
    video_count: int = 0


class WatchPointCreate(CamelModel):
    path: str
    recursive: bool = True
    note: str = ""


class WatchPointUpdate(CamelModel):
    recursive: Optional[bool] = None
    enabled: Optional[bool] = None
    note: Optional[str] = None


class ScanResult(CamelModel):
    found: int = 0
    queued: int = 0
    skipped: int = 0
    message: str = ""


# ---------------------------------------------------------------- 定时任务

class Schedule(CamelModel):
    id: str
    name: str
    cron: str
    enabled: bool = True
    watchpoint_ids: list[str] = Field(default_factory=list)
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    cron_text: str = ""


class ScheduleCreate(CamelModel):
    name: str
    cron: str
    enabled: bool = True
    watchpoint_ids: list[str] = Field(default_factory=list)


class ScheduleUpdate(CamelModel):
    name: Optional[str] = None
    cron: Optional[str] = None
    enabled: Optional[bool] = None
    watchpoint_ids: Optional[list[str]] = None


# ---------------------------------------------------------------- 撤销

class UndoPreviewIn(CamelModel):
    path: str
    recursive: bool = True


class UndoSlice(CamelModel):
    path: str
    name: str
    size: int


class UndoGroup(CamelModel):
    base: str
    suffix: str
    origin: str
    slices: list[UndoSlice] = Field(default_factory=list)
    origin_size: int = 0
    slice_sum: int = 0
    mode: str = "?"
    ok: bool = False
    reason: str = ""
    origin_duration: float = 0
    slice_durations: list[float] = Field(default_factory=list)
    duration_sum: float = 0


class UndoPreviewOut(CamelModel):
    path: str
    groups: list[UndoGroup] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)
    ok_count: int = 0
    bad_count: int = 0


class UndoApplyIn(CamelModel):
    path: str
    recursive: bool = True
    delete_slices: bool = True
    restore_origin: bool = True
    trash: bool = True


class UndoDetail(CamelModel):
    base: str
    action: str
    message: str


class UndoApplyOut(CamelModel):
    deleted: int = 0
    trashed: int = 0
    restored: int = 0
    skipped: int = 0
    freed_bytes: int = 0
    problems: list[str] = Field(default_factory=list)
    details: list[UndoDetail] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- 清理

class ClearJobsIn(CamelModel):
    statuses: list[str] = Field(default_factory=lambda: ["success", "failed", "canceled"])

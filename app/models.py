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

# 原片处理方式，取值与 app/config.py 的 MARK_SOURCES 一致，改一处要同步另一处。
#   rename 加 #origin 后缀留在原处 | move 移到归档子目录
#   none   不处理原片            | delete 切分成功后删除原片
MarkSource = Literal["rename", "move", "none", "delete"]


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
    mark_source: MarkSource = "rename"
    source_dir: str = "resize-video-origin-file"
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

# 扫描方式：realtime 实时监听 | interval 每隔 N 小时 | daily 每天 HH:MM | manual 仅手动
# 取值与 app/config.py 的 SCAN_MODES 必须一致，改这里要同步改那边。
ScanMode = Literal["realtime", "interval", "daily", "manual"]


class WatchPoint(CamelModel):
    id: str
    path: str
    recursive: bool = True
    scan_mode: ScanMode = "realtime"
    scan_interval_hours: int = 6
    scan_time: str = "03:00"
    # 原片处理方式。空串 = 跟随系统设置 —— 这是有意的「未设置」状态，
    # 不是缺省值，解析优先级见 config.resolve_mark_policy
    mark_source: str = ""
    source_dir: str = ""
    note: str = ""
    created_at: str = ""
    last_scan_at: Optional[str] = None
    # 下次自动扫描时间；实时监听与仅手动没有「下次」，返回 None
    next_scan_at: Optional[str] = None
    video_count: int = 0


class WatchPointCreate(CamelModel):
    path: str
    recursive: bool = True
    scan_mode: ScanMode = "realtime"
    scan_interval_hours: int = 6
    scan_time: str = "03:00"
    mark_source: str = ""
    source_dir: str = ""
    note: str = ""


class WatchPointUpdate(CamelModel):
    recursive: Optional[bool] = None
    scan_mode: Optional[ScanMode] = None
    scan_interval_hours: Optional[int] = None
    scan_time: Optional[str] = None
    # 传空串表示「改回跟随系统设置」，所以这两个字段不能用 None 表达「清空」
    mark_source: Optional[str] = None
    source_dir: Optional[str] = None
    note: Optional[str] = None


class IgnoredFile(CamelModel):
    """扫描时「看到了但按规则不处理」的文件。

    存在的意义是把「真的没有视频」和「有视频、但被保护性跳过」区分开——
    以前这两种情况的返回都是 found=0，用户只能靠猜。

    kind / resettable 是再往下一层的分化：光说「跳过了」还不够，
    用户真正要判断的是「这个还救不救得回来」。
    """
    name: str
    reason: str
    kind: str = ""            # "slice" 切片 / "origin" 已切分过的原片
    resettable: bool = False  # 切片已不在的原片 -> 恢复原名即可重新分割


class ResettableDir(CamelModel):
    """含「切片已不在的原片」的目录。前端据此逐目录发起恢复。"""
    path: str
    recursive: bool = True


class ScanResult(CamelModel):
    found: int = 0
    queued: int = 0
    skipped: int = 0
    waiting: int = 0
    ignored: list[IgnoredFile] = Field(default_factory=list)
    ignored_total: int = 0
    resettable_total: int = 0
    resettable_dirs: list[ResettableDir] = Field(default_factory=list)
    message: str = ""

    @classmethod
    def from_engine(cls, result: dict) -> ScanResult:
        """把扫描引擎的 dict 结果转成对外模型。

        两个扫描入口（全部目录 / 单个目录）出的键完全一致，转换只写这一处，
        以后加字段就不会漏掉其中一个接口。
        """
        return cls(
            found=result.get("found", 0),
            queued=result.get("queued", 0),
            skipped=result.get("skipped", 0),
            waiting=result.get("waiting", 0),
            ignored=result.get("ignored") or [],
            ignored_total=result.get("ignoredTotal", 0),
            resettable_total=result.get("resettableTotal", 0),
            resettable_dirs=result.get("resettableDirs") or [],
            message=result.get("message", ""))


class ScanIn(CamelModel):
    """手动扫描时的临时覆盖参数。

    字段留空（或整个请求体不传）= 跟随该监控目录 / 系统设置。它刻意**不落盘**：
    表达的是「就这一次按这个方式处理」，改的是一次行为，不是配置。
    """
    mark_source: Optional[MarkSource] = None
    source_dir: Optional[str] = None


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


class UndoLoneOrigin(CamelModel):
    """已被切分、但切片已经不在了的原片（`原名#origin.扩展名`）。

    成因多半是切片被手工删除或移走了。这类文件以前在界面上完全隐身：
    扫描器把它当「已处理」跳过，撤销页又只从切片出发去找原片，于是它
    既不在 groups 也不在 orphans 里，只能 SSH 手工改名才能脱困。
    它本身不是错误，只是「已经没有可撤销的东西了」。
    """
    # 字段名与 UndoGroup.origin 对齐：两者都是「原片的路径」，
    # 共用同一个恢复函数，就没必要各叫各的
    origin: str
    name: str
    base: str = ""
    suffix: str = ""
    size: int = 0
    mtime: str = ""


class UndoPreviewOut(CamelModel):
    path: str
    groups: list[UndoGroup] = Field(default_factory=list)
    origin_only: list[UndoLoneOrigin] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)
    ok_count: int = 0
    bad_count: int = 0


class UndoApplyIn(CamelModel):
    path: str
    recursive: bool = True
    delete_slices: bool = True
    restore_origin: bool = True
    # 同时把「切片已不在」的原片也恢复原名。默认关，因为恢复原名等于让它
    # 重新变回待处理文件，实时监听会立刻再切一遍 —— 必须由用户明确要求。
    restore_origin_only: bool = False
    trash: bool = True


class UndoDetail(CamelModel):
    base: str
    action: str
    message: str


class UndoApplyOut(CamelModel):
    deleted: int = 0
    trashed: int = 0
    restored: int = 0
    restored_orphans: int = 0
    skipped: int = 0
    freed_bytes: int = 0
    problems: list[str] = Field(default_factory=list)
    details: list[UndoDetail] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- 清理

class ClearJobsIn(CamelModel):
    statuses: list[str] = Field(default_factory=lambda: ["success", "failed", "canceled"])

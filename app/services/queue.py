#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/services/queue.py —— 任务入队、取消、重试

队列本身没有单独的内存结构：排队中的任务就是数据库里 status='queued' 的行，
按 created_at 排序即为队列顺序。这样重启服务不会丢队列，
也不用担心内存队列和数据库两份状态对不上。

worker 靠一个 Event 被唤醒（见 runner.py），不做忙轮询。
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from .. import config, db
from ..services.events import bus
from core import splitter as engine

# 有任务入队时置位，唤醒睡着的 worker
_wake = threading.Event()

# 运行中任务的取消旗标：job_id -> Event
_cancel_flags: dict[str, threading.Event] = {}
_flags_lock = threading.Lock()


def notify() -> None:
    _wake.set()


def wait_for_work(timeout: float = 2.0) -> None:
    """等有新任务；超时也返回，让 worker 能周期性检查其他状态。"""
    _wake.wait(timeout)
    _wake.clear()


def new_job_id() -> str:
    return "job_" + uuid.uuid4().hex[:8]


# ---------------------------------------------------------------- 参数解析

def resolve_threshold(settings: dict) -> int:
    try:
        return engine.parse_size(settings["split"].get("size")
                                 or engine.DEFAULT_THRESHOLD)
    except ValueError:
        return engine.parse_size(engine.DEFAULT_THRESHOLD)


def resolve_outdir(src: Path, settings: dict) -> Path:
    split = settings["split"]
    if split.get("outdirMode") == "custom":
        custom = (split.get("outdir") or "").strip()
        if custom:
            return Path(custom)
    return src.parent


# ---------------------------------------------------------------- 入队

def _watchpoint(wp_id: str | None) -> dict | None:
    """按 id 取监控目录配置。取不到就返回 None，任务按系统默认解析。"""
    if not wp_id:
        return None
    for wp in config.load_watchpoints():
        if wp.get("id") == wp_id:
            return wp
    return None


def enqueue(src, trigger: str = "manual", watchpoint_id: str = None,
            src_size: int = None, mark_override: dict = None) -> tuple:
    """
    创建一个排队任务。返回 (job, reason)：
        job 非 None 表示入队成功；job 为 None 时 reason 说明被拒的原因。

    mark_override 是「就这一次」的原片处理方式（手动扫描时临时选的），
    优先级高于该监控目录的设置与系统默认值。
    """
    src = Path(src)
    if not src.is_file():
        return None, "文件不存在：%s" % src
    if engine.is_slice_or_origin(src):
        return None, "这是本工具产生的切片或已标记的原片，不再处理"

    # 同一个文件已经有排队中/运行中的任务就不再重复入队
    if db.active_job_for(str(src)) is not None:
        return None, "该文件已有进行中的任务"

    settings = config.load_settings()
    if src_size is None:
        try:
            src_size = src.stat().st_size
        except OSError as exc:
            return None, "无法读取文件大小（%s）" % exc

    job_id = new_job_id()
    outdir = resolve_outdir(src, settings)
    # 原片处理方式在入队这一刻就定下来（本次手动 > 该目录设置 > 系统默认），
    # 快照进任务行；执行阶段直接用它，不再回头读设置
    policy = config.resolve_mark_policy(settings, _watchpoint(watchpoint_id),
                                        mark_override)
    job = {
        "id": job_id,
        "src": str(src),
        "src_name": src.name,
        "src_size": int(src_size),
        "outdir": str(outdir),
        "status": "queued",
        "phase": "waiting",
        "progress": 0.0,
        "parts_total": 0,
        "parts_done": 0,
        # 固定流拷贝。这列保留是为了让老任务记录（曾经的 auto/bytes）仍能正常显示，
        # 也方便将来真的需要区分时不用再改表结构。
        "mode": "copy",
        "used_mode": None,
        "trigger": trigger,
        "watchpoint_id": watchpoint_id,
        "mark_source": policy["markSource"],
        "source_dir": policy["sourceDir"],
        "message": "已加入队列，等待处理",
        "error": None,
        "produced": [],
        "warnings": [],
        "duration_sec": None,
        "created_at": db.now_iso(),
        "started_at": None,
        "finished_at": None,
    }
    if not db.insert_job(job):
        return None, "该文件已有进行中的任务"

    created = db.get_job(job_id)
    bus.publish({"type": "job.created", "job": created})
    notify()
    return created, ""


# ---------------------------------------------------------------- 取消

def register_running(job_id: str) -> threading.Event:
    """worker 开始跑某个任务时登记一个取消旗标。"""
    flag = threading.Event()
    with _flags_lock:
        _cancel_flags[job_id] = flag
    return flag


def unregister_running(job_id: str) -> None:
    with _flags_lock:
        _cancel_flags.pop(job_id, None)


def is_cancel_requested(job_id: str) -> bool:
    with _flags_lock:
        flag = _cancel_flags.get(job_id)
    return bool(flag and flag.is_set())


def request_cancel(job_id: str) -> tuple:
    """返回 (是否处理成功, 说明)。"""
    job = db.get_job(job_id)
    if job is None:
        return False, "任务不存在"

    if job["status"] == "queued":
        db.update_job(job_id, status="canceled", phase="done",
                      finished_at=db.now_iso(), message="已取消（尚未开始）")
        bus.publish({"type": "job.updated", "job": db.get_job(job_id)})
        return True, "已取消"

    if job["status"] == "running":
        with _flags_lock:
            flag = _cancel_flags.get(job_id)
        if flag is None:
            return False, "任务刚刚结束，无法取消"
        flag.set()
        db.update_job(job_id, message="已请求中止，正在等待当前步骤结束")
        bus.publish({"type": "job.updated", "job": db.get_job(job_id)})
        return True, "已请求中止"

    return False, "该任务已经结束，无需取消"


# ---------------------------------------------------------------- 重试

def retry(job_id: str) -> tuple:
    """
    重跑一个失败/取消的任务。刻意新建一条记录而不是复用原来的 id：
    保留完整历史，出问题时能对照两次的日志。
    """
    job = db.get_job(job_id)
    if job is None:
        return False, "任务不存在", None
    if job["status"] in ("queued", "running"):
        return False, "任务正在进行中，无需重试", None

    # 沿用原任务的处理方式：重试的是同一个文件，用户要的是「把上次没做完的
    # 事做完」，而不是按现在的设置换一套行为
    new_job, reason = enqueue(
        Path(job["src"]), trigger="retry",
        watchpoint_id=job.get("watchpointId"),
        mark_override={"markSource": job.get("markSource"),
                       "sourceDir": job.get("sourceDir")})
    if new_job is None:
        return False, reason, None
    return True, "已重新加入队列", new_job


# ---------------------------------------------------------------- 清理

def clear_finished(statuses=None) -> int:
    """清理已结束的任务记录。绝不清理 queued/running。"""
    statuses = statuses or ["success", "failed", "canceled", "skipped"]
    safe = [s for s in statuses if s not in ("queued", "running")]
    if not safe:
        return 0
    return db.clear_jobs(safe)

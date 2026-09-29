#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/services/scheduler.py —— 监控目录的定时扫描（APScheduler）

「什么时候扫」只有一个入口：每个监控目录自己的扫描方式。
    realtime  实时监听 —— 在 monitor.py 里，不走本模块
    interval  每 N 小时  ┐
    daily     每天 HH:MM ┘ 由本模块排成 APScheduler 的 cron job
    manual    不自动扫描

用 APScheduler 而不是往系统 crontab 里写：计划要能在网页上随时改、要能拿到
「下次执行时间」显示给用户，还要能在容器里工作。JobStore 用内存即可——计划的
定义本身就存在 watchpoints.json 里，服务启动时按它重新注册一遍，
不需要第二份持久化状态。

cron 表达式统一用标准的 5 段格式（分 时 日 月 周），和 Linux crontab 一致；
「扫描方式 → cron」的翻译规则在 config.scan_cron()，那边是唯一真相。
"""

from __future__ import annotations

import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .. import config
from ..services import scanner
from ..services.events import bus

# 本地时区：用户在网页上写「每天凌晨 3 点」，指的当然是他所在时区的 3 点。
# 容器里靠 TZ 环境变量（compose 里设成 Asia/Shanghai）来对齐。
LOCAL_TZ = datetime.now().astimezone().tzinfo

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()

# job id 前缀。目前只有监控目录派生这一种计划，前缀的作用是重排时能一次把旧的
# 清干净，同时又不会碰到别的模块注册进来的 job。
WATCH_JOB_PREFIX = "watchpoint:"


def _log(msg: str) -> None:
    print("[scheduler] %s" % msg, flush=True)


# ---------------------------------------------------------------- 生命周期

def start_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is None:
            _scheduler = BackgroundScheduler(
                timezone=LOCAL_TZ,
                job_defaults={
                    # 错过的执行不堆积：容器关机一晚上，第二天不该补跑几十次
                    "coalesce": True,
                    "max_instances": 1,
                    "misfire_grace_time": 300,
                })
            _scheduler.start()
    reload_watchpoint_jobs()


def stop_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            try:
                _scheduler.shutdown(wait=False)
            except Exception:
                pass
            _scheduler = None


def reload_watchpoint_jobs() -> None:
    """
    按每个监控目录的「扫描方式」注册或撤销它的自动扫描任务。

    只有 interval / daily 两种模式会产生 job（realtime 靠 monitor 的监听 + 轮询，
    manual 本来就什么都不做）。改完扫描方式后必须调一次这个函数，否则计划不会生效。
    """
    if _scheduler is None:
        return
    with _lock:
        for job in _scheduler.get_jobs():
            if job.id.startswith(WATCH_JOB_PREFIX):
                job.remove()

        count = 0
        for wp in config.load_watchpoints():
            expr = config.scan_cron(wp)
            if not expr:
                continue
            try:
                trigger = CronTrigger.from_crontab(expr, timezone=LOCAL_TZ)
            except Exception as exc:                  # noqa: BLE001
                _log("监控目录 %s 的扫描计划无效，已跳过：%s" % (wp.get("path"), exc))
                continue
            _scheduler.add_job(
                _run_watchpoint_scan, trigger=trigger,
                id=WATCH_JOB_PREFIX + wp["id"], args=[wp["id"]],
                replace_existing=True)
            count += 1
        _log("已注册 %d 个监控目录的定时扫描" % count)


def _next_run(job_id: str):
    if _scheduler is None:
        return None
    job = _scheduler.get_job(job_id)
    if job is None or job.next_run_time is None:
        return None
    try:
        return job.next_run_time.astimezone(LOCAL_TZ).isoformat(timespec="seconds")
    except Exception:                                 # noqa: BLE001
        return None


def next_scan_time(watchpoint_id: str):
    """
    取监控目录的下次自动扫描时间。

    实时监听模式的「下次」就是「随时」，仅手动模式没有下次，两者都返回 None，
    由前端按 scanMode 自己决定显示什么文案。
    """
    return _next_run(WATCH_JOB_PREFIX + watchpoint_id)


# ---------------------------------------------------------------- 执行

def _run_watchpoint_scan(watchpoint_id: str) -> None:
    """某个监控目录的定时扫描到点了。"""
    wp = next((w for w in config.load_watchpoints()
               if w.get("id") == watchpoint_id), None)
    if wp is None:
        return
    # 界面上刚把扫描方式改成「仅手动 / 实时」时，旧 job 可能还会响一次；
    # 这里再确认一遍模式，避免用户刚关掉定时扫描就被多扫一回。
    if wp.get("scanMode") not in ("interval", "daily"):
        return

    path = wp.get("path")
    _log("触发监控目录定时扫描：%s" % path)
    bus.publish({"type": "watchpoint.scan", "watchpointId": watchpoint_id,
                 "path": path})
    try:
        result = scanner.scan_watchpoint(wp, trigger="watch")
        _log("监控目录 %s 定时扫描完成：%s" % (path, result.get("message")))
        if result.get("queued") or result.get("waiting"):
            bus.publish({
                "type": "scan.finished",
                "watchpointId": watchpoint_id,
                "found": result.get("found", 0),
                "queued": result.get("queued", 0),
                "waiting": result.get("waiting", 0),
                "message": result.get("message", ""),
            })
    except Exception as exc:                          # noqa: BLE001
        _log("监控目录 %s 定时扫描出错：%s" % (path, exc))

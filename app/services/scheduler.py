#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/services/scheduler.py —— 定时任务（APScheduler）

用 APScheduler 而不是往系统 crontab 里写：定时任务需要能在网页上随时增删改、
要能拿到「下次执行时间」显示给用户，还要能在容器里工作。
APScheduler 的 JobStore 用内存即可——任务定义本身就存在 schedules.json 里，
服务启动时重新注册一遍，不需要第二份持久化状态。

cron 表达式统一用标准的 5 段格式（分 时 日 月 周），和 Linux crontab 一致，
用户不用再学一套新语法。
"""

from __future__ import annotations

import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .. import config, db
from ..services import scanner
from ..services.events import bus

# 本地时区：用户在网页上写「每天凌晨 3 点」，指的当然是他所在时区的 3 点。
# 容器里靠 TZ 环境变量（compose 里设成 Asia/Shanghai）来对齐。
LOCAL_TZ = datetime.now().astimezone().tzinfo

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()

# job id 前缀把两类任务分开，互不干扰：
#   schedule:   用户在「定时任务」页面自己建的
#   watchpoint: 监控目录上「扫描方式」自动派生的
# 分开的好处是重排其中一类时不会误删另一类，用户也不会在定时任务列表里
# 看见一堆系统生成的条目。
SCHEDULE_PREFIX = "schedule:"
WATCH_JOB_PREFIX = "watchpoint:"

WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]


def _log(msg: str) -> None:
    print("[scheduler] %s" % msg, flush=True)


# ---------------------------------------------------------------- cron 描述

def _fmt_time(minute: str, hour: str) -> str:
    try:
        return "%02d:%02d" % (int(hour), int(minute))
    except (TypeError, ValueError):
        return "%s:%s" % (hour, minute)


def _step(value: str) -> int | None:
    """识别 */N 写法，返回 N。"""
    if isinstance(value, str) and value.startswith("*/"):
        try:
            return int(value[2:])
        except ValueError:
            return None
    return None


def describe_cron(expr: str) -> str:
    """把 5 段 cron 翻译成中文，识别不了就原样返回。"""
    parts = (expr or "").split()
    if len(parts) != 5:
        return expr or ""
    minute, hour, dom, month, dow = parts

    # 每 N 分钟
    if dom == "*" and month == "*" and dow == "*":
        n = _step(minute)
        if n and hour == "*":
            return "每 %d 分钟" % n
        if minute == "*" and hour == "*":
            return "每分钟"
        # 每 N 小时
        nh = _step(hour)
        if nh and minute.isdigit():
            return "每 %d 小时（第 %s 分）" % (nh, minute)

    if not (minute.isdigit() and hour.isdigit()):
        return expr

    time_text = _fmt_time(minute, hour)

    if dom == "*" and month == "*" and dow == "*":
        return "每天 %s" % time_text

    if dom == "*" and month == "*" and dow != "*":
        try:
            days = [int(d) for d in dow.split(",")]
            names = "、".join(WEEKDAYS[d % 7] for d in days)
        except (ValueError, IndexError):
            return expr
        if len(days) == 1 and days[0] in (1, 2, 3, 4, 5):
            return "每周%s %s" % (names.replace("周", ""), time_text)
        return "%s %s" % (names, time_text)

    if dom != "*" and month == "*" and dow == "*":
        try:
            days = [int(d) for d in dom.split(",")]
            return "每月 %s 日 %s" % ("、".join(str(d) for d in days), time_text)
        except ValueError:
            return expr

    if month != "*":
        try:
            months = [int(m) for m in month.split(",")]
            days = [int(d) for d in dom.split(",")] if dom != "*" else [1]
            return "每年 %s 月 %s 日 %s" % (
                "、".join(str(m) for m in months),
                "、".join(str(d) for d in days), time_text)
        except ValueError:
            return expr

    return expr


def validate_cron(expr: str) -> CronTrigger:
    """校验表达式，不合法就抛 ValueError，附带人话说明。"""
    expr = (expr or "").strip()
    if not expr:
        raise ValueError("cron 表达式不能为空")
    if len(expr.split()) != 5:
        raise ValueError("cron 表达式需要 5 段：分 时 日 月 周，例如 0 3 * * *")
    try:
        return CronTrigger.from_crontab(expr, timezone=LOCAL_TZ)
    except Exception as exc:                          # noqa: BLE001
        raise ValueError("cron 表达式无法解析（%s）" % exc)


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
    reload_schedules()


def stop_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            try:
                _scheduler.shutdown(wait=False)
            except Exception:
                pass
            _scheduler = None


def reload_schedules() -> None:
    """按 schedules.json 重新注册所有定时任务，并顺带重排监控目录的扫描计划。"""
    if _scheduler is None:
        return
    with _lock:
        for job in _scheduler.get_jobs():
            if job.id.startswith(SCHEDULE_PREFIX):
                job.remove()

        for sc in config.load_schedules():
            if not sc.get("enabled", True):
                continue
            try:
                trigger = validate_cron(sc.get("cron", ""))
            except ValueError as exc:
                _log("定时任务「%s」的 cron 无效，已跳过：%s" % (sc.get("name"), exc))
                continue
            _scheduler.add_job(
                _run_schedule, trigger=trigger, id="schedule:%s" % sc["id"],
                args=[sc["id"]], replace_existing=True)
        _log("已注册 %d 个定时任务" % len([j for j in _scheduler.get_jobs()
                                          if j.id.startswith(SCHEDULE_PREFIX)]))
    # 注意在 _lock 之外调用：_lock 是不可重入的普通 Lock，嵌套会直接死锁
    reload_watchpoint_jobs()


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


def next_run_time(schedule_id: str):
    """取某个定时任务的下次执行时间（ISO 字符串）；未启用或取不到返回 None。"""
    return _next_run(SCHEDULE_PREFIX + schedule_id)


def next_scan_time(watchpoint_id: str):
    """
    取监控目录的下次自动扫描时间。

    实时监听模式的「下次」就是「随时」，仅手动模式没有下次，两者都返回 None，
    由前端按 scanMode 自己决定显示什么文案。
    """
    return _next_run(WATCH_JOB_PREFIX + watchpoint_id)


# ---------------------------------------------------------------- 执行

def _run_schedule(schedule_id: str) -> None:
    sc = next((s for s in config.load_schedules() if s.get("id") == schedule_id), None)
    if sc is None or not sc.get("enabled", True):
        return

    name = sc.get("name") or schedule_id
    _log("触发定时任务：%s" % name)
    bus.publish({"type": "schedule.fired", "scheduleId": schedule_id, "name": name})

    try:
        result = scanner.scan_all(trigger="schedule",
                                 watchpoint_ids=sc.get("watchpointIds") or None)
        _log("定时任务「%s」完成：%s" % (name, result.get("message")))
    except Exception as exc:                          # noqa: BLE001
        _log("定时任务「%s」出错：%s" % (name, exc))

    # 回写执行时间，网页上要显示「上次执行」
    schedules = config.load_schedules()
    for item in schedules:
        if item.get("id") == schedule_id:
            item["lastRunAt"] = db.now_iso()
            break
    config.save_schedules(schedules)


def run_now(schedule_id: str) -> tuple:
    """立即执行一次（不改变原有排期），放到线程里跑避免阻塞请求。"""
    sc = next((s for s in config.load_schedules() if s.get("id") == schedule_id), None)
    if sc is None:
        return False, "定时任务不存在"
    if not config.load_watchpoints():
        return False, "还没有配置任何监控目录，定时任务没有可扫描的目标"
    threading.Thread(target=_run_schedule, args=[schedule_id],
                     name="schedule-manual", daemon=True).start()
    return True, "已开始执行"


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

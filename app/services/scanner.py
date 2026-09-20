#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/services/scanner.py —— 扫描目录、文件稳定检测、入队

这里有本项目最关键的一条安全规则：**文件稳定检测**。

往 NAS 拷一个 10GB 视频时，监控会立刻看到这个文件。如果当场开切，
切到的是半截文件，而且切完还会把原片改名成 #origin —— 数据实际就废了。
所以任何文件在进入队列之前，都必须先证明「它不再被写了」：

    1. 修改时间已经比现在早 settleSeconds 秒以上 -> 认为是老文件，直接放行
    2. 否则必须连续两次观察到体积没变，且距离首次观察超过 settleSeconds

两条都保留是刻意的：条件 1 让历史文件（比如刚装好软件时扫描整个媒体库）
立刻就能处理，不用傻等；条件 2 用来兜住「拷贝工具最后才回写修改时间」
这种情况——那时 mtime 看着很老，但体积还在涨，只能靠体积观察拦住。

稳定检测的状态放在内存里：这只是「几十秒内的临时观察」，
进程重启后重新观察一遍即可，不值得持久化。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .. import config, db
from ..services import queue as job_queue
from core import splitter as engine

_MAX_TRACKED = 20000


class StabilityTracker:
    def __init__(self) -> None:
        self._seen: dict[str, tuple[int, float]] = {}

    def check(self, path: Path, settle_seconds: int) -> tuple[bool, str]:
        """返回 (能否入队, 说明)。会顺带更新观察状态。"""
        if settle_seconds <= 0:
            return True, ""
        try:
            st = os.stat(path)
        except OSError as exc:
            return False, "无法读取文件信息（%s）" % exc

        now = time.time()
        key = str(path)
        age = now - st.st_mtime
        prev = self._seen.get(key)

        if prev is None or prev[0] != st.st_size:
            # 第一次看到，或体积还在变化 -> 重新开始计时
            self._seen[key] = (st.st_size, now)
            if age >= settle_seconds:
                return True, "修改时间已稳定"
            return False, "等待文件稳定（还需约 %d 秒）" % max(
                1, int(settle_seconds - age))

        if now - prev[1] >= settle_seconds:
            return True, "体积已稳定 %d 秒" % settle_seconds
        if age >= settle_seconds:
            return True, "修改时间已稳定"
        return False, "等待文件稳定（还需约 %d 秒）" % max(
            1, int(settle_seconds - (now - prev[1])))

    def forget(self, path) -> None:
        self._seen.pop(str(path), None)

    def prune(self) -> None:
        """条目太多时丢掉一小时前的记录，避免长时间运行后内存缓慢增长。"""
        if len(self._seen) <= _MAX_TRACKED:
            return
        cutoff = time.time() - 3600
        self._seen = {k: v for k, v in self._seen.items() if v[1] >= cutoff}


tracker = StabilityTracker()


# ---------------------------------------------------------------- 过滤条件

def build_spec(settings: dict) -> dict:
    split = settings["split"]
    watch = settings["watch"]
    try:
        threshold = engine.parse_size(split.get("size") or engine.DEFAULT_THRESHOLD)
    except ValueError:
        threshold = engine.parse_size(engine.DEFAULT_THRESHOLD)
    try:
        min_size = engine.parse_size(watch.get("minSize") or "0")
    except ValueError:
        min_size = 0
    return {
        "exts": engine.normalize_exts(split.get("ext")),
        "threshold": threshold,
        "min_size": min_size,
        "all": bool(split.get("all", False)),
        "recursive": bool(split.get("recursive", True)),
        "settle": int(watch.get("settleSeconds") or 0),
        "ignore_suffixes": tuple(
            s.lower() for s in (watch.get("ignoreSuffixes") or [])),
        "source_dir": split.get("sourceDir") or "origin",
    }


def consider_file(path, spec: dict, trigger: str,
                  watchpoint_id: str = None) -> tuple[str, str]:
    """
    判断单个文件该不该入队，返回 (结果, 说明)。
    结果取值：queued / waiting / skipped / duplicate
    """
    path = Path(path)
    try:
        if not path.is_file():
            return "skipped", "文件不存在"
        if path.suffix.lower() not in spec["exts"]:
            return "skipped", "不是要处理的视频格式"
        if engine.is_slice_or_origin(path):
            return "skipped", "是本工具产生的切片或已标记的原片"
        if spec["source_dir"] in path.parts:
            return "skipped", "位于原片归档目录"
        if path.suffix.lower() in spec["ignore_suffixes"]:
            return "skipped", "临时文件后缀"
        size = path.stat().st_size
    except OSError as exc:
        return "skipped", "无法读取（%s）" % exc

    if size < spec["min_size"]:
        return "skipped", "小于最小体积限制"
    if not spec["all"] and size <= spec["threshold"]:
        return "skipped", "未超过大小阈值，无需切分"

    ok, reason = tracker.check(path, spec["settle"])
    if not ok:
        return "waiting", reason

    job, why = job_queue.enqueue(path, trigger=trigger,
                                watchpoint_id=watchpoint_id, src_size=size)
    if job is None:
        return "duplicate", why
    tracker.forget(path)
    return "queued", "已入队"


# ---------------------------------------------------------------- 目录扫描

def scan_paths(paths, trigger: str, watchpoint_id: str = None,
               respect_settle: bool = True) -> dict:
    """扫描若干目录并入队，返回统计结果。"""
    settings = config.load_settings()
    spec = build_spec(settings)
    if not respect_settle:
        spec["settle"] = 0

    valid_dirs = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            valid_dirs.append(p)

    if not valid_dirs:
        return {"found": 0, "queued": 0, "skipped": 0, "waiting": 0,
                "message": "没有可扫描的目录（路径不存在或不是文件夹）",
                "details": []}

    files = engine.collect_files(valid_dirs, spec["exts"], spec["recursive"],
                                {spec["source_dir"]})

    queued = waiting = skipped = 0
    details = []
    for f in files:
        status, reason = consider_file(f, spec, trigger, watchpoint_id)
        if status == "queued":
            queued += 1
        elif status == "waiting":
            waiting += 1
            details.append(reason)
        else:
            skipped += 1

    tracker.prune()

    message = "扫描完成：%d 个视频，入队 %d 个" % (len(files), queued)
    if waiting:
        message += "，%d 个还在拷贝中需等待" % waiting
    if skipped:
        message += "，跳过 %d 个" % skipped
    if not files:
        message = "扫描完成：没有发现需要处理的视频"

    return {"found": len(files), "queued": queued, "skipped": skipped,
            "waiting": waiting, "message": message,
            "details": sorted(set(details))[:5]}


def scan_watchpoint(watchpoint: dict, trigger: str = "manual") -> dict:
    """扫描单个监控目录，并回写它的 lastScanAt / videoCount。"""
    result = scan_paths([watchpoint["path"]], trigger,
                        watchpoint_id=watchpoint.get("id"))
    watchpoints = config.load_watchpoints()
    for wp in watchpoints:
        if wp.get("id") == watchpoint.get("id"):
            wp["lastScanAt"] = db.now_iso()
            wp["videoCount"] = result["found"]
            break
    config.save_watchpoints(watchpoints)
    return result


def scan_all(trigger: str = "manual", watchpoint_ids=None) -> dict:
    """扫描全部（或指定）启用的监控目录。"""
    watchpoints = [w for w in config.load_watchpoints() if w.get("enabled", True)]
    if watchpoint_ids:
        wanted = set(watchpoint_ids)
        watchpoints = [w for w in watchpoints if w.get("id") in wanted]

    total = {"found": 0, "queued": 0, "skipped": 0, "waiting": 0, "details": []}
    for wp in watchpoints:
        r = scan_watchpoint(wp, trigger)
        for key in ("found", "queued", "skipped", "waiting"):
            total[key] += r.get(key, 0)
        total["details"].extend(r.get("details") or [])

    if not watchpoints:
        total["message"] = "没有启用的监控目录，请先去「监控目录」页面添加"
    else:
        total["message"] = "扫描完成：%d 个目录，%d 个视频，入队 %d 个" % (
            len(watchpoints), total["found"], total["queued"])
        if total["waiting"]:
            total["message"] += "，%d 个还在拷贝中需等待" % total["waiting"]
    return total

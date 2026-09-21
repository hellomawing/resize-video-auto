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
from collections import Counter
from pathlib import Path

from .. import config, db
from ..services import queue as job_queue
from core import splitter as engine

_MAX_TRACKED = 20000

# 单次扫描最多回传多少条「被跳过的文件」明细。切片可能有几十个，
# 全带回去会把响应撑大；总数照实统计，明细封顶即可。
_MAX_IGNORED = 50


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

def build_spec(settings: dict, extra_archive_dirs=None) -> dict:
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
        # 所有可能被当作归档目录的名字：系统默认 + 各监控目录用到的 + 本次覆盖
        # 指定的 + 历史默认名。用集合而不是单个名字 —— 归档目录能按监控目录
        # 分别设置，只排除一个名字的话，别处归档走的原片会被当成新视频再切一遍。
        #
        # ⚠️ 排除是按「路径里任意一段同名」判断的（与 core.collect_files 一致），
        # 所以归档目录名不能取成路径上已有的段名（如 vol1、1000），否则会把整棵树
        # 都排除掉。默认名足够特别，改名前请留意这一点。
        "archive_dirs": config.collect_archive_dirs(
            settings, extra=extra_archive_dirs),
    }


def consider_file(path, spec: dict, trigger: str,
                  watchpoint_id: str = None,
                  mark_override: dict = None) -> tuple[str, str]:
    """
    判断单个文件该不该入队，返回 (结果, 说明)。
    结果取值：queued / waiting / skipped / duplicate

    mark_override 会原样交给入队逻辑，用来实现「这一次手动扫描按指定的
    方式处理原片」，只影响本次入队的任务。
    """
    path = Path(path)
    try:
        if not path.is_file():
            return "skipped", "文件不存在"
        if path.suffix.lower() not in spec["exts"]:
            return "skipped", "不是要处理的视频格式"
        if engine.is_internal_temp(path):
            return "skipped", "是切分中途的临时分段"
        if engine.is_slice_or_origin(path):
            return "skipped", "是本工具产生的切片或已标记的原片"
        if any(part in spec["archive_dirs"] for part in path.parts):
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
                                watchpoint_id=watchpoint_id, src_size=size,
                                mark_override=mark_override)
    if job is None:
        return "duplicate", why
    tracker.forget(path)
    return "queued", "已入队"


# ---------------------------------------------------------------- 目录扫描

def _summarize_counts(counter) -> str:
    """把「原因 -> 个数」讲成一句人话，如「2 个是本工具切出来的切片」。"""
    return "，".join("%d 个%s" % (n, reason) for reason, n in counter.most_common())


def summarize_ignored(ignored: list) -> str:
    """
    把被跳过的文件按原因归类成一句人话。

    逐个列文件名只会变成噪音（一个切过 20 段的视频就有 20 个切片），
    用户真正需要知道的是「跳过了几类东西、各多少个」。
    """
    return _summarize_counts(Counter(item["reason"] for item in ignored))


def _analyze_skips(raw_skips: list) -> dict:
    """
    把收集阶段记下的「看到了但按规矩不处理」的产物分类。

    重点是把**切片已不在的原片**单独挑出来：目录里躺着一个 `#origin`，
    看着像「已经处理完了」，但如果它的切片被删掉或搬走了，这次切分的
    结果其实已经没了 —— 那个原片是**可以重新分割**的。以前它和普通跳过
    共用一句「是本工具切出来的切片或已标记的原片」，用户根本看不出
    「这个还能救」，只能靠猜。分类必须在整个目录遍历完之后做：判断切片
    在不在，需要先看全所有文件。

    返回：detail(明细，可重新分割的排在前面) / total / resettable /
    dirs(含孤立原片的目录，去重) / otherReasons(其余跳过的原因计数)
    """
    # 先把所有切片的「原名+扩展名」记下来，这是判断原片有没有配对的依据
    slice_keys = set()
    for p, _ in raw_skips:
        if engine.classify_own_product(p) == "slice":
            key = engine.product_base(p)
            if key:
                slice_keys.add(key)

    resettable_items, other_items, dirs = [], [], []
    other_reasons = Counter()

    for p, reason in raw_skips:
        kind = engine.classify_own_product(p) or ""
        key = engine.product_base(p)
        alone = (kind == "origin" and key is not None
                 and key not in slice_keys)
        item = {"name": p.name, "reason": reason, "kind": kind,
                "resettable": alone}
        if alone:
            parent = str(p.parent)
            if parent not in dirs:
                dirs.append(parent)
            resettable_items.append(item)
        else:
            other_items.append(item)
            other_reasons[reason] += 1

    # 明细封顶 50 条时，「可以重新分割」的必须排在前面 ——
    # 它们等着用户拿主意，被一堆切片名挤出视野就等于没提示
    return {
        "detail": (resettable_items + other_items)[:_MAX_IGNORED],
        "total": len(raw_skips),
        "resettable": len(resettable_items),
        "dirs": dirs,
        "otherReasons": other_reasons,
    }


def _skip_notes(info: dict) -> str:
    """把「跳过了什么」讲清楚，尤其是「有能补救的」这件事。"""
    notes = []
    if info["resettable"]:
        notes.append("目录里有 %d 个原片的分割结果已经不在（切片被删除或移走），"
                     "可以重新分割" % info["resettable"])
    others = info["total"] - info["resettable"]
    if others:
        detail = _summarize_counts(info["otherReasons"])
        notes.append("另有 %d 个是已切分完成的产物，已跳过%s"
                     % (others, "（%s）" % detail if detail else ""))
    return "；".join(notes)


def _scan_message(found: int, queued: int, waiting: int, skipped: int,
                  info: dict) -> str:
    """
    把一次扫描的结果讲成一句人话。

    最容易被带偏的是「一个都没入队、目录里却有视频」：只报「0 个视频」
    等于什么都没说，用户没法判断是没扫到、被跳过了、还是能救回来。
    """
    if found:
        head = "扫描完成：%d 个视频，入队 %d 个" % (found, queued)
        if waiting:
            head += "，%d 个还在拷贝中需等待" % waiting
        if skipped:
            head += "，跳过 %d 个" % skipped
    else:
        head = "扫描完成：没有需要处理的新视频"

    notes = _skip_notes(info)
    return head + "。" + notes + "。" if notes else head


def scan_paths(paths, trigger: str, watchpoint_id: str = None,
               respect_settle: bool = True, mark_override: dict = None) -> dict:
    """扫描若干目录并入队，返回统计结果。

    mark_override 是「就这一次」的原片处理方式（手动扫描时前端传进来的，
    camelCase 键），只作用于本次入队的任务，不写回任何配置。
    """
    settings = config.load_settings()
    extra = [mark_override.get("sourceDir")] if mark_override else None
    spec = build_spec(settings, extra_archive_dirs=extra)
    if not respect_settle:
        spec["settle"] = 0

    valid_dirs = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            valid_dirs.append(p)

    if not valid_dirs:
        return {"found": 0, "queued": 0, "skipped": 0, "waiting": 0,
                "ignored": [], "ignoredTotal": 0,
                "resettableTotal": 0, "resettableDirs": [],
                "message": "没有可扫描的目录（路径不存在或不是文件夹）",
                "details": []}

    raw_skips: list = []

    def _note_skip(path, reason):
        """
        记录一个「看到了但按规矩不处理」的文件。

        没有这一步，收集阶段被丢掉的文件连个数都不剩，用户看到
        「没有发现需要处理的视频」时无从判断是真没有还是被跳过了。
        这里只登记，分类留到遍历结束后统一做——判断一个原片的切片
        还在不在，得先看全整个目录。
        """
        raw_skips.append((Path(path), reason))

    files = engine.collect_files(valid_dirs, spec["exts"], spec["recursive"],
                                spec["archive_dirs"], on_skip=_note_skip)

    queued = waiting = skipped = 0
    details = []
    for f in files:
        status, reason = consider_file(f, spec, trigger, watchpoint_id,
                                       mark_override)
        if status == "queued":
            queued += 1
        elif status == "waiting":
            waiting += 1
            details.append(reason)
        else:
            skipped += 1

    tracker.prune()

    info = _analyze_skips(raw_skips)

    return {"found": len(files), "queued": queued, "skipped": skipped,
            "waiting": waiting,
            "message": _scan_message(len(files), queued, waiting, skipped, info),
            "ignored": info["detail"], "ignoredTotal": info["total"],
            "resettableTotal": info["resettable"],
            # 前端拿着目录列表逐目录去恢复；recursive 沿用本次扫描的范围，
            # 免得恢复时漏掉子目录里的原片
            "resettableDirs": [{"path": d, "recursive": spec["recursive"]}
                               for d in info["dirs"]],
            "details": sorted(set(details))[:5]}


def scan_watchpoint(watchpoint: dict, trigger: str = "manual",
                    mark_override: dict = None) -> dict:
    """扫描单个监控目录，并回写它的 lastScanAt / videoCount。"""
    result = scan_paths([watchpoint["path"]], trigger,
                        watchpoint_id=watchpoint.get("id"),
                        mark_override=mark_override)
    watchpoints = config.load_watchpoints()
    for wp in watchpoints:
        if wp.get("id") == watchpoint.get("id"):
            wp["lastScanAt"] = db.now_iso()
            wp["videoCount"] = result["found"]
            break
    config.save_watchpoints(watchpoints)
    return result


def scan_all(trigger: str = "manual", watchpoint_ids=None,
             mark_override: dict = None) -> dict:
    """
    扫描全部（或指定）监控目录 —— 这是「立即扫描」的入口。

    刻意不看 scanMode：用户已经亲手按下了按钮，就不该再被「仅手动」这类设置
    拦住——「仅手动」约束的是**自动**扫描，不是手动扫描。两者走两条独立的路，
    这正是早期版本把「停用」和「不自动扫」混成一个开关时踩过的坑。
    """
    watchpoints = list(config.load_watchpoints())
    if watchpoint_ids:
        wanted = set(watchpoint_ids)
        watchpoints = [w for w in watchpoints if w.get("id") in wanted]

    total = {"found": 0, "queued": 0, "skipped": 0, "waiting": 0,
             "ignored": [], "ignoredTotal": 0,
             "resettableTotal": 0, "resettableDirs": [], "details": []}
    for wp in watchpoints:
        r = scan_watchpoint(wp, trigger, mark_override=mark_override)
        for key in ("found", "queued", "skipped", "waiting",
                    "ignoredTotal", "resettableTotal"):
            total[key] += r.get(key, 0)
        total["ignored"].extend(r.get("ignored") or [])
        total["resettableDirs"].extend(r.get("resettableDirs") or [])
        total["details"].extend(r.get("details") or [])
    total["ignored"] = total["ignored"][:_MAX_IGNORED]

    if not watchpoints:
        total["message"] = "还没有配置监控目录，请先去「监控目录」页面添加"
        return total

    if total["found"]:
        head = "扫描完成：%d 个目录，%d 个视频，入队 %d 个" % (
            len(watchpoints), total["found"], total["queued"])
        if total["waiting"]:
            head += "，%d 个还在拷贝中需等待" % total["waiting"]
    else:
        # 一条都没入队，但确实看到了视频 —— 必须说清楚为什么没动它，
        # 否则用户只会看到「0 个视频」，然后开始怀疑扫描坏了
        head = "扫描完成：%d 个目录都没有需要处理的新视频" % len(watchpoints)

    # 跨目录汇总时不再逐条列原因（会太长），只讲两件最重要的事：
    # 有没有能补救的、以及有多少是正常跳过
    notes = _skip_notes({
        "total": total["ignoredTotal"],
        "resettable": total["resettableTotal"],
        "otherReasons": Counter(),
    })
    total["message"] = head + "。" + notes + "。" if notes else head
    return total

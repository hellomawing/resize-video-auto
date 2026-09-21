#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/services/runner.py —— 任务执行器（单并发 worker 线程）

为什么是单并发：切割是纯磁盘 IO 密集操作，同时跑多个任务只会让所有任务
一起变慢，还容易把 NAS 的机械盘打成瓶颈；而且多任务同时读同一个目录时
更容易触发权限/句柄问题。所以固定一个 worker 串行处理，队列负责排队。

worker 与引擎之间的桥：
    engine.set_hooks(log_sink, progress_cb, cancel_event)
引擎不直接碰数据库和 WebSocket，全部通过这两个回调交回给本模块处理。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .. import config, db
from ..services import queue as job_queue
from ..services.events import bus
from core import splitter as engine

_stop = threading.Event()
_thread: threading.Thread | None = None
_current_job_id: str | None = None
_current_lock = threading.Lock()

PRUNE_INTERVAL_SEC = 3600


def current_job_id() -> str | None:
    with _current_lock:
        return _current_job_id


def is_busy() -> bool:
    return current_job_id() is not None


def start_worker() -> threading.Thread:
    global _thread
    if _thread and _thread.is_alive():
        return _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="split-worker", daemon=True)
    _thread.start()
    return _thread


def stop_worker(timeout: float = 10.0) -> None:
    _stop.set()
    job_queue.notify()          # 让睡着的 worker 立刻醒来退出
    if _thread and _thread.is_alive():
        _thread.join(timeout=timeout)


# ---------------------------------------------------------------- 主循环

def _loop() -> None:
    last_prune = time.time()
    while not _stop.is_set():
        try:
            pending = db.queued_jobs()
        except Exception:
            pending = []

        if not pending:
            job_queue.wait_for_work(timeout=2.0)
        else:
            for job in pending:
                if _stop.is_set():
                    break
                if job["status"] != "queued":
                    continue
                _run_job(job)

        if time.time() - last_prune > PRUNE_INTERVAL_SEC:
            last_prune = time.time()
            try:
                removed = db.prune(30)
                if removed:
                    print("[prune] 清理了 %d 条超过保留期的任务记录" % removed,
                          flush=True)
            except Exception:
                pass


# ---------------------------------------------------------------- 单个任务

def _run_job(job: dict) -> None:
    global _current_job_id
    job_id = job["id"]

    # 入队到现在可能已经过了很久，重新确认它还在排队（可能已被取消）
    fresh = db.get_job(job_id)
    if fresh is None or fresh["status"] != "queued":
        return

    with _current_lock:
        _current_job_id = job_id

    src = Path(fresh["src"])
    settings = config.load_settings()
    split = settings["split"]

    flag = job_queue.register_running(job_id)
    started = time.time()

    db.update_job(job_id, status="running", started_at=db.now_iso(),
                  phase="probe", progress=0.0,
                  message="开始处理")
    bus.publish({"type": "job.updated", "job": db.get_job(job_id)})

    def on_log(line: str) -> None:
        db.append_log(job_id, line,
                      max_lines=int(settings["server"].get("jobLogLines") or 2000))
        bus.publish({"type": "job.log", "jobId": job_id, "line": line})

    def on_progress(payload: dict) -> None:
        fields = {}
        if "phase" in payload:
            fields["phase"] = payload["phase"]
        if "progress" in payload:
            fields["progress"] = payload["progress"]
        if "partsDone" in payload:
            fields["parts_done"] = payload["partsDone"]
        if "partsTotal" in payload:
            fields["parts_total"] = payload["partsTotal"]
        if "message" in payload:
            fields["message"] = payload["message"]
        if not fields:
            return
        try:
            db.update_job(job_id, **fields)
        except Exception:
            return
        bus.publish({
            "type": "job.progress", "jobId": job_id,
            "progress": fields.get("progress"),
            "partsDone": fields.get("parts_done"),
            "partsTotal": fields.get("parts_total"),
            "phase": fields.get("phase"),
            "message": fields.get("message"),
        })

    engine.set_hooks(log_sink=on_log, progress_cb=on_progress,
                     cancel_event=flag, debug=bool(split.get("debug")))

    try:
        threshold = job_queue.resolve_threshold(settings)
        outdir = job_queue.resolve_outdir(src, settings)
        ffmpeg = engine.find_bin("ffmpeg")
        ffprobe = engine.find_bin("ffprobe")
        # 用任务上固化的快照（入队那一刻已按「本次手动 > 该目录设置 > 系统默认」
        # 定好）。老任务没有这两个字段，回落到当前设置，行为与之前完全一致。
        mark_source = (fresh.get("markSource") or split.get("markSource")
                       or config.DEFAULT_MARK_SOURCE)
        source_dir = (fresh.get("sourceDir")
                      or config.normalize_source_dir(split.get("sourceDir")))
        delete_source = mark_source == "delete"

        try:
            size = src.stat().st_size
        except OSError as exc:
            raise RuntimeError("源文件已不可读（%s）" % exc)

        on_log("=" * 60)
        on_log("视频无损分割 · %s" % src.name)
        on_log("=" * 60)
        on_log("源文件     ：%s" % src)
        on_log("原大小     ：%s" % engine.human_size(size))
        if split.get("bySize"):
            on_log("切分方式   ：按大小，每片不超过 %s" % split.get("size"))
            seg_seconds = None
        else:
            seg_seconds = float(split.get("seconds") or 300)
            on_log("切分方式   ：按时间，每片 %.0f 秒" % seg_seconds)
        on_log("切割方式   ：ffmpeg 流拷贝（无损，每段可独立播放）")
        on_log("输出目录   ：%s" % outdir)
        on_log("原文件处理 ：%s%s" % (
            _mark_text(mark_source, source_dir),
            "　⚠️ 不可撤销" if delete_source else ""))
        on_log("ffmpeg     ：%s" % (ffmpeg or "未找到（将使用纯字节切割）"))
        on_log("-" * 60)

        result = engine.split_one(
            src,
            threshold=threshold,
            mode="copy",          # 只做无损流拷贝，不再有别的模式
            outdir=outdir,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            seg_seconds=seg_seconds,
            keep_metadata=bool(split.get("keepMetadata", True)),
            overwrite=bool(split.get("overwrite", False)),
            mark_source_mode="none" if delete_source else mark_source,
            source_dir=source_dir,
            delete_source=delete_source,
            dry_run=False,
        )

        for part in result["parts"]:
            on_log("   -> %s  %s" % (part["name"], engine.human_size(part["size"])))
        for warn in result["warnings"]:
            on_log("   注意：%s" % warn)
        on_log("-" * 60)
        on_log("完成：%d 段，共 %s" % (len(result["parts"]),
                                      engine.human_size(result["totalBytes"])))
        on_log(result["sourceAction"])

        elapsed = time.time() - started
        db.update_job(
            job_id,
            status="success", phase="done", progress=1.0,
            parts_done=len(result["parts"]), parts_total=len(result["parts"]),
            used_mode=result["usedMode"],
            produced=_json(result["parts"]),
            warnings=_json(result["warnings"]),
            duration_sec=round(elapsed, 1),
            message="完成：%d 段，%s" % (len(result["parts"]),
                                       engine.human_size(result["totalBytes"])),
            finished_at=db.now_iso(),
        )
        # 之前失败过的文件这次成了（多半是用户换了片源）：把失败记录清掉
        _forget_failure(src)

    except engine.Cancelled:
        on_log("任务已被取消，已生成的部分切片已清理。")
        db.update_job(job_id, status="canceled", phase="done",
                      message="已取消", finished_at=db.now_iso(),
                      duration_sec=round(time.time() - started, 1))
    except Exception as exc:                  # noqa: BLE001
        detail = str(exc) or exc.__class__.__name__
        on_log("处理失败：%s" % detail)
        # 记进「处理失败的文件」：任务页会列出来，自动扫描也不会再反复重试
        # 这个文件（源码文件本身原样保留，一个字都没动）
        _record_failure(src, job_id, detail)
        db.update_job(job_id, status="failed", phase="done", error=detail,
                      message="失败：%s" % detail, finished_at=db.now_iso(),
                      duration_sec=round(time.time() - started, 1))
        print("[worker] 任务 %s 失败：%s" % (job_id, detail), flush=True)
    finally:
        engine.clear_hooks()
        job_queue.unregister_running(job_id)
        with _current_lock:
            _current_job_id = None
        try:
            db.get_conn()  # 触发一次连接，确保上面的写入都已提交
        except Exception:
            pass
        bus.publish({"type": "job.updated", "job": db.get_job(job_id)})


def _record_failure(src: Path, job_id: str, reason: str) -> None:
    """
    把这次失败记进「处理失败的文件」。之后自动扫描看到同一个文件、
    大小与修改时间都没变，就会跳过它，不再反复重试。

    记录里带的是源文件的 size/mtime，所以用户换掉片源后会自动重新处理。
    连 stat 都失败就记 0/0 —— 那样的记录永远匹配不上任何文件，等于不拦，
    比误拦一个本来能处理的文件安全。
    """
    try:
        st = src.stat()
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0.0
    try:
        db.record_failure(src, src.name, size, mtime, reason, job_id)
    except Exception:
        # 记录失败不该把任务本身的错误盖掉
        pass


def _forget_failure(src: Path) -> None:
    try:
        db.forget_failure(src)
    except Exception:
        pass


def _mark_text(mark_source: str, source_dir: str) -> str:
    """把原片处理方式讲成人话。任务日志里直接显示，别让用户猜枚举值。"""
    if mark_source == "move":
        return "移动到 %s/ 子文件夹" % source_dir
    return {
        "rename": "重命名为「原名#origin.扩展名」，留在原文件夹",
        "none": "不处理原片（原地保留，下次扫描可能被再次切分）",
        "delete": "切分成功后删除原片",
    }.get(mark_source, mark_source)


def _json(payload) -> str:
    import json
    return json.dumps(payload, ensure_ascii=False)


def cancel_requested(job_id: str) -> bool:
    return job_queue.is_cancel_requested(job_id)

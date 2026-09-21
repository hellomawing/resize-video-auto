#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/api/failures.py —— 「处理失败的文件」列表与操作

为什么要单独有一份清单：切分失败时本工具**不会动用户的原片**（这是刻意的），
于是那个文件会原样留在监控目录里。没有这份清单，用户只能靠翻任务日志才知道
哪些文件没处理成；而自动扫描每次都会再看到它，没有失败记忆的话就会
「入队 → 失败 → 再入队」无限重复。

三个动作对应三种处置：
    GET   /api/failures          有哪些、为什么
    POST  /api/failures/retry    再试一次（忘掉记忆 + 重新入队）
    POST  /api/failures/clear    从列表移除（只删记录，不动文件）
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from .. import config, db
from ..models import FailureClearIn, FailureListOut, FailurePathIn
from ..services import queue as job_queue

router = APIRouter(tags=["failures"])


@router.get("/failures", response_model=FailureListOut)
def list_failures(limit: int = Query(default=500, ge=1, le=2000)) -> FailureListOut:
    """
    列出切不动的文件，最近失败的排前面。

    顺手清理「文件已经不在」的记录：用户自己删掉或改名之后，这条记录既没有
    可重试的对象，也没法再拦任何东西，挂着只会让人困惑。
    """
    items = db.list_failures(limit)
    alive, gone = [], []
    for item in items:
        (alive if Path(item["path"]).is_file() else gone).append(item)
    if gone:
        db.clear_failures([item["path"] for item in gone])
    return FailureListOut(total=len(alive), items=alive)


@router.post("/failures/retry")
def retry_failure(payload: FailurePathIn) -> dict:
    """
    再试一次：先忘掉失败记录（否则扫描器会继续拦着它），再重新入队。

    这是**手动**入口，所以不看 scanMode —— 与监控页的「扫描」同一条规矩：
    用户亲手点了，就不该再被「仅手动」这类设置挡着。
    原片处理方式按该文件所属监控目录的设置走，找不到就退回系统默认值。
    """
    path = Path(payload.path)
    if not path.is_file():
        db.forget_failure(path)
        raise HTTPException(status_code=404, detail="文件已不存在，已从列表里移除")

    db.forget_failure(path)
    job, why = job_queue.enqueue(path, trigger="manual",
                                 watchpoint_id=_guess_watchpoint(path))
    if job is None:
        raise HTTPException(status_code=400, detail=why)
    return {"ok": True, "message": "已重新加入队列", "path": str(path), "job": job}


@router.post("/failures/clear")
def clear_failures(payload: FailureClearIn | None = None) -> dict:
    """
    从列表里移除。**只删记录，不动磁盘上的任何文件。**

    清掉之后下次扫描会重新尝试它；如果还是切不动，它会再次回到这个列表 ——
    所以「忽略」不等于「永远不再处理」，只是把当前这条提示收起来。
    """
    paths = payload.paths if payload else None
    removed = db.clear_failures(paths)
    return {"ok": True, "removed": removed,
            "message": "已从列表移除 %d 条记录（磁盘文件未做任何改动）" % removed}


def _guess_watchpoint(path: Path) -> str | None:
    """
    这个文件属于哪个监控目录？取**最长匹配**的那个。

    监控目录可以嵌套（/media 与 /media/2026 都能覆盖同一个文件），这时应按
    更具体的那一个的设置来处理原片 —— 与人的直觉一致。找不到就返回 None，
    入队逻辑会自动退回系统默认值。
    """
    best_id, best_len = None, -1
    text = str(path)
    for wp in config.load_watchpoints():
        base = str(wp.get("path") or "").rstrip("/")
        if base and text.startswith(base + "/") and len(base) > best_len:
            best_id, best_len = wp.get("id"), len(base)
    return best_id

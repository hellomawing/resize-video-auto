#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/api/jobs.py —— 任务队列的查询与操作
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import config, db
from ..models import ClearJobsIn, ScanResult
from ..services import queue as job_queue
from ..services import runner, scanner

router = APIRouter(tags=["jobs"])


@router.get("/jobs")
def list_jobs(status: str = Query(default=None, description="按状态筛选，可逗号分隔"),
              q: str = Query(default=None, description="按文件名搜索"),
              limit: int = Query(default=50, ge=1, le=500),
              offset: int = Query(default=0, ge=0)) -> dict:
    total, items = db.list_jobs(status=status, query=q, limit=limit, offset=offset)
    return {"total": total, "items": items, "limit": limit, "offset": offset}


@router.post("/jobs/clear")
def clear_jobs(payload: ClearJobsIn) -> dict:
    """
    清理已结束的任务记录。只删数据库里的记录，**不会碰磁盘上的任何视频文件**。
    """
    removed = job_queue.clear_finished(payload.statuses)
    return {"ok": True, "removed": removed,
            "message": "已清理 %d 条任务记录（磁盘文件未做任何改动）" % removed}


@router.post("/scan", response_model=ScanResult)
def scan_all() -> ScanResult:
    """立即扫描全部启用的监控目录。"""
    result = scanner.scan_all(trigger="manual")
    return ScanResult(found=result["found"], queued=result["queued"],
                      skipped=result["skipped"], message=result["message"])


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.get("/jobs/{job_id}/log")
def get_job_log(job_id: str, tail: int = Query(default=2000, ge=1, le=20000)) -> dict:
    if db.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    max_lines = int(config.load_settings()["server"].get("jobLogLines") or 2000)
    lines, truncated = db.get_logs(job_id, tail=min(tail, max_lines))
    return {"jobId": job_id, "lines": lines, "truncated": truncated}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict:
    ok, message, job = job_queue.retry(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "job": job}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    ok, message = job_queue.request_cancel(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    if db.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if db.get_job(job_id)["status"] == "running":
        raise HTTPException(status_code=400, detail="任务正在运行，请先取消再删除记录")
    db.delete_job(job_id)
    return {"ok": True, "message": "已删除任务记录（磁盘文件未做任何改动）"}


@router.get("/jobs/current/running")
def current_running() -> dict:
    """概览页用：当前正在跑哪个任务。"""
    job_id = runner.current_job_id()
    return {"jobId": job_id, "job": db.get_job(job_id) if job_id else None}

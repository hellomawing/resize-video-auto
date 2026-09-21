#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/db.py —— SQLite 持久化（任务与任务日志）

为什么任务要落库而不是只放内存：
NAS 上容器随时可能被重启、被升级、被断电，队列如果只在内存里就全丢了。
落库之后重启还能看到历史任务，失败任务也能一键重试。

并发模型：全进程共用一个连接 + 一把锁。这个服务的写入量极小
（一个 worker 串行写 + 偶尔的 API 读），SQLite 完全够用，
没必要引入连接池把事情搞复杂。开启 WAL 让读不阻塞写。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

ACTIVE_STATUSES = ("queued", "running")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    src           TEXT NOT NULL,
    src_name      TEXT NOT NULL,
    src_size      INTEGER NOT NULL DEFAULT 0,
    outdir        TEXT,
    status        TEXT NOT NULL,
    phase         TEXT,
    progress      REAL NOT NULL DEFAULT 0,
    parts_total   INTEGER NOT NULL DEFAULT 0,
    parts_done    INTEGER NOT NULL DEFAULT 0,
    mode          TEXT,
    used_mode     TEXT,
    trigger       TEXT,
    watchpoint_id TEXT,
    mark_source   TEXT,
    source_dir    TEXT,
    message       TEXT,
    error         TEXT,
    produced      TEXT,
    warnings      TEXT,
    duration_sec  REAL,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_src
    ON jobs(src) WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS job_logs (
    job_id TEXT NOT NULL,
    seq    INTEGER NOT NULL,
    ts     TEXT NOT NULL,
    line   TEXT NOT NULL,
    PRIMARY KEY (job_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id, seq);
"""


def now_iso() -> str:
    """本地时区的 ISO 8601 时间戳（秒精度足够，前端只展示到秒）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            config.ensure_dirs()
            _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.execute("PRAGMA foreign_keys=ON")
        return _conn


# 建表语句（CREATE TABLE IF NOT EXISTS）只对**新库**生效：已经存在的老库
# 不会因为 SCHEMA 里多写了几列就跟着变，所以新增列必须在下面补一次迁移。
_JOB_ADDED_COLUMNS = (
    ("mark_source", "TEXT"),
    ("source_dir", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    """给老库补上新加的列。幂等，每次启动都能安全地跑一遍。"""
    have = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    for name, sql_type in _JOB_ADDED_COLUMNS:
        if name not in have:
            conn.execute("ALTER TABLE jobs ADD COLUMN %s %s" % (name, sql_type))


def init_db() -> None:
    with _lock:
        conn = get_conn()
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        # 上次进程是被强杀的，运行中的任务不可能还在跑，标记为中断
        conn.execute(
            "UPDATE jobs SET status='failed', error=?, finished_at=?, phase='done' "
            "WHERE status='running'",
            ("服务重启导致任务中断", now_iso()))
        conn.commit()


def close_db() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


# ---------------------------------------------------------------- 序列化

def row_to_job(row: sqlite3.Row) -> dict:
    """数据库行 -> 对外 JSON（字段名转 camelCase，与 docs/api.md 一致）。"""
    if row is None:
        return None
    job = {
        "id": row["id"],
        "src": row["src"],
        "srcName": row["src_name"],
        "srcSize": row["src_size"],
        "outdir": row["outdir"],
        "status": row["status"],
        "phase": row["phase"],
        "progress": row["progress"],
        "partsTotal": row["parts_total"],
        "partsDone": row["parts_done"],
        "mode": row["mode"],
        "usedMode": row["used_mode"],
        "trigger": row["trigger"],
        "watchpointId": row["watchpoint_id"],
        # 任务入队那一刻解析好的原片处理方式。是**快照**，不代表当前设置
        "markSource": row["mark_source"],
        "sourceDir": row["source_dir"],
        "message": row["message"],
        "error": row["error"],
        "produced": _loads(row["produced"], []),
        "warnings": _loads(row["warnings"], []),
        "durationSec": row["duration_sec"],
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
    }
    # 运行耗时对前端很有用，后端直接算好，避免各端各算一套
    if job["startedAt"] and job["finishedAt"]:
        try:
            start = datetime.fromisoformat(job["startedAt"])
            end = datetime.fromisoformat(job["finishedAt"])
            job["durationSec"] = round((end - start).total_seconds(), 1)
        except Exception:
            pass
    return job


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except Exception:
        return default


# ---------------------------------------------------------------- 任务

JOB_COLUMNS = (
    "id", "src", "src_name", "src_size", "outdir", "status", "phase", "progress",
    "parts_total", "parts_done", "mode", "used_mode", "trigger", "watchpoint_id",
    "mark_source", "source_dir",
    "message", "error", "produced", "warnings", "duration_sec",
    "created_at", "started_at", "finished_at",
)


def insert_job(job: dict) -> bool:
    """
    插入任务。如果同一条源文件已经有排队中/运行中的任务，返回 False。
    靠 idx_jobs_active_src 这个部分唯一索引兜底，比先查后插更可靠
    （监控和手动扫描可能同时触发同一个文件）。
    """
    cols = ", ".join(JOB_COLUMNS)
    marks = ", ".join("?" for _ in JOB_COLUMNS)
    values = [
        job.get("id"), job.get("src"), job.get("src_name"), job.get("src_size", 0),
        job.get("outdir"), job.get("status", "queued"), job.get("phase", "waiting"),
        job.get("progress", 0.0), job.get("parts_total", 0), job.get("parts_done", 0),
        job.get("mode"), job.get("used_mode"), job.get("trigger"),
        job.get("watchpoint_id"),
        job.get("mark_source"), job.get("source_dir"),
        job.get("message"), job.get("error"),
        json.dumps(job.get("produced") or [], ensure_ascii=False),
        json.dumps(job.get("warnings") or [], ensure_ascii=False),
        job.get("duration_sec"), job.get("created_at", now_iso()),
        job.get("started_at"), job.get("finished_at"),
    ]
    with _lock:
        conn = get_conn()
        try:
            conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", values)
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def update_job(job_id: str, **fields) -> None:
    """按需更新若干列（列名用数据库的 snake_case）。"""
    if not fields:
        return
    sets = ", ".join("%s=?" % k for k in fields)
    values = list(fields.values()) + [job_id]
    with _lock:
        conn = get_conn()
        conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", values)
        conn.commit()


def get_job(job_id: str) -> dict:
    with _lock:
        row = get_conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return row_to_job(row)


def list_jobs(status: str = None, query: str = None, limit: int = 50,
              offset: int = 0) -> tuple:
    where, params = [], []
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        where.append("status IN (%s)" % ", ".join("?" for _ in statuses))
        params.extend(statuses)
    if query:
        where.append("src_name LIKE ?")
        params.append("%%%s%%" % query)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with _lock:
        conn = get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM jobs {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM jobs {clause} ORDER BY created_at DESC, rowid DESC "
            f"LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    return total, [row_to_job(r) for r in rows]


def recent_job_dirs(limit: int = 300) -> list:
    """最近任务里出现过的目录，最近的在前。

    给网页上的目录选择器当「常用目录」用。为什么需要：fnOS 的存储池根
    （如 /vol1）权限位是 000 且不含扩展 ACL，内核拒绝对它 readdir ——
    也就是**「从根目录往下逐级浏览」这条路在那种机器上第一级就是死的**。
    但拿到完整路径就能正常访问，所以把「用户已经用过的目录」直接摆出来，
    绕开那一层。最近切过的目录恰恰是撤销和重新分割最可能回去的地方。

    只取目录本身：outdir 直接就是目录，src 要取它的父目录。
    去重按出现顺序保留第一次 —— 顺序本身就是要传达的信息。
    """
    with _lock:
        rows = get_conn().execute(
            "SELECT src, outdir FROM jobs ORDER BY created_at DESC, rowid DESC "
            "LIMIT ?", (int(limit),)).fetchall()
    seen, out = set(), []
    for row in rows:
        candidates = [row["outdir"], os.path.dirname(row["src"] or "")]
        for raw in candidates:
            raw = (raw or "").rstrip("/")
            if not raw or raw in seen:
                continue
            seen.add(raw)
            out.append(raw)
    return out


def active_job_for(src: str) -> dict:
    with _lock:
        row = get_conn().execute(
            "SELECT * FROM jobs WHERE src=? AND status IN ('queued','running')",
            (src,)).fetchone()
    return row_to_job(row)


def queued_jobs() -> list:
    """按入队顺序取出排队中的任务，供 worker 消费。"""
    with _lock:
        rows = get_conn().execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
    return [row_to_job(r) for r in rows]


def counts_by_status() -> dict:
    with _lock:
        rows = get_conn().execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
    out = {"queued": 0, "running": 0, "success": 0, "failed": 0, "canceled": 0,
           "skipped": 0}
    for r in rows:
        out[r["status"]] = r["n"]
    return out


def stats_summary() -> dict:
    """概览页要的汇总：今天的产出量、累计段数。"""
    counts = counts_by_status()
    with _lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(parts_done), 0) AS parts, "
            "COALESCE(SUM(src_size), 0) AS bytes "
            "FROM jobs WHERE status='success'").fetchone()
        today = datetime.now().astimezone().date().isoformat()
        today_row = conn.execute(
            "SELECT COALESCE(SUM(src_size), 0) AS bytes FROM jobs "
            "WHERE status='success' AND substr(finished_at, 1, 10)=?",
            (today,)).fetchone()
    return {
        "jobs": counts,
        "totalParts": int(row["parts"] or 0),
        "totalBytes": int(row["bytes"] or 0),
        "todayBytes": int(today_row["bytes"] or 0),
    }


def delete_job(job_id: str) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM job_logs WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.commit()


def clear_jobs(statuses: list) -> int:
    """批量清理指定状态的任务记录（不会动磁盘上的视频文件）。"""
    if not statuses:
        return 0
    marks = ", ".join("?" for _ in statuses)
    with _lock:
        conn = get_conn()
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM jobs WHERE status IN ({marks})", statuses).fetchall()]
        if ids:
            idmarks = ", ".join("?" for _ in ids)
            conn.execute(f"DELETE FROM job_logs WHERE job_id IN ({idmarks})", ids)
            conn.execute(f"DELETE FROM jobs WHERE id IN ({idmarks})", ids)
            conn.commit()
    return len(ids)


# ---------------------------------------------------------------- 任务日志

def append_log(job_id: str, line: str, max_lines: int = 2000) -> None:
    """追加一行日志，并在超过上限时裁掉最早的若干行，避免日志表无限膨胀。"""
    if line is None:
        return
    line = str(line).rstrip("\n")
    ts = now_iso()
    with _lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS s FROM job_logs WHERE job_id=?",
            (job_id,)).fetchone()
        seq = int(row["s"]) + 1
        conn.execute(
            "INSERT INTO job_logs (job_id, seq, ts, line) VALUES (?,?,?,?)",
            (job_id, seq, ts, line))
        if seq > max_lines:
            conn.execute(
                "DELETE FROM job_logs WHERE job_id=? AND seq<=?",
                (job_id, seq - max_lines))
        conn.commit()


def get_logs(job_id: str, tail: int = 2000) -> tuple:
    """返回 (lines, truncated)。"""
    with _lock:
        conn = get_conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM job_logs WHERE job_id=?", (job_id,)).fetchone()[0]
        rows = conn.execute(
            "SELECT line FROM job_logs WHERE job_id=? ORDER BY seq DESC LIMIT ?",
            (job_id, tail)).fetchall()
    lines = [r["line"] for r in reversed(rows)]
    return lines, total > len(lines)


def prune(retention_days: int = 30) -> int:
    """清理超过保留期的已完成任务及其日志。"""
    cutoff = (datetime.now().astimezone()
              - timedelta(days=max(1, retention_days))).isoformat(timespec="seconds")
    with _lock:
        conn = get_conn()
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status NOT IN ('queued','running') "
            "AND finished_at IS NOT NULL AND finished_at < ?", (cutoff,)).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            marks = ", ".join("?" for _ in ids)
            conn.execute(f"DELETE FROM job_logs WHERE job_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM jobs WHERE id IN ({marks})", ids)
            conn.commit()
    return len(ids)

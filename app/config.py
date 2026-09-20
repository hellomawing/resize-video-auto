#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/config.py —— 配置与数据文件的位置、读写

所有持久化都放在同一个数据目录里（容器里是 /data，本地开发是 <项目根>/data）：
    settings.json     切分/监控/服务参数
    watchpoints.json  监控目录列表
    schedules.json    定时任务列表
    video-splitter.db SQLite：任务与日志

为什么用 JSON 而不是全塞进 SQLite：这三个配置项都是「人能看懂、能手改、
能直接备份走」的东西，用 JSON 更友好；只有任务流水这种量大且需要按条件
查询的数据才值得进数据库。
"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path

APP_VERSION = "1.0.0"

PROJECT_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(
    os.environ.get("VS_DATA_DIR") or (PROJECT_DIR / "data")
).resolve()

SETTINGS_PATH = DATA_DIR / "settings.json"
WATCHPOINTS_PATH = DATA_DIR / "watchpoints.json"
SCHEDULES_PATH = DATA_DIR / "schedules.json"
DB_PATH = DATA_DIR / "video-splitter.db"

# ---------------------------------------------------------------- 默认设置

DEFAULT_SETTINGS = {
    "split": {
        "mode": "auto",             # auto | copy | bytes
        "bySize": True,             # True=按大小切，False=按时间切（用 seconds）
        "size": "3.9G",
        "seconds": 300,
        "all": False,               # True=不按大小筛选，所有视频都切
        "ext": [
            ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".flv",
            ".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".3gp", ".rmvb", ".vob",
        ],
        "recursive": True,
        "outdirMode": "same",       # same | custom
        "outdir": "",
        "markSource": "rename",     # rename | move | none | delete
        "sourceDir": "origin",
        "keepMetadata": True,
        "overwrite": False,
        "debug": False,
    },
    "watch": {
        "realtime": True,           # inotify 实时监听（网络共享目录会自动退化为轮询）
        "pollInterval": 30,         # 轮询间隔（秒）
        "settleSeconds": 60,        # 文件稳定检测：大小与修改时间连续多久不变才入队
        "minSize": "0",             # 小于该大小的文件忽略
        "ignoreSuffixes": [".tmp", ".part", ".crdownload", ".!qb", ".download"],
        # 可访问根目录白名单：约束网页上的目录浏览与监控目录添加。
        # 容器里通常把 NAS 的存储空间整盘挂进来，所以这里是 /vol1 … /vol4。
        "allowedRoots": ["/vol1", "/vol2", "/vol3", "/vol4"],
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8099,
        "jobLogLines": 2000,        # 单个任务最多保留多少行日志
    },
}

_lock = threading.RLock()


# ---------------------------------------------------------------- 通用读写

def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, payload) -> None:
    """
    先写临时文件再原子替换——NAS 上可能因为断电/容器被杀而在写入中途中断，
    直接覆盖会把配置文件写坏。os.replace 是原子的。
    """
    ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return deepcopy(default)
    except Exception:
        # 文件损坏时不能让服务起不来，退回默认值；原文件留在原地供人工排查
        return deepcopy(default)


def _deep_merge(base: dict, override: dict) -> dict:
    """把用户配置合并到默认配置上：只认默认里有定义的键，其余一律忽略。

    这样升级版本后如果新增了配置项，老配置文件也能自动补上默认值；
    同时用户手改坏了（比如把 pollInterval 写成字符串）也不会把结构带歪。
    """
    out = deepcopy(base)
    for key, value in (override or {}).items():
        if key not in out:
            continue
        if isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------- 设置

def load_settings() -> dict:
    with _lock:
        raw = _read_json(SETTINGS_PATH, DEFAULT_SETTINGS)
        return _normalize_settings(_deep_merge(DEFAULT_SETTINGS, raw))


def save_settings(data: dict) -> dict:
    with _lock:
        merged = _normalize_settings(_deep_merge(DEFAULT_SETTINGS, data))
        _atomic_write(SETTINGS_PATH, merged)
        return merged


def _normalize_settings(s: dict) -> dict:
    """把明显不合法的值纠正回可用范围，避免脏配置把服务卡住。"""
    split = s["split"]
    if split.get("mode") not in ("auto", "copy", "bytes"):
        split["mode"] = "auto"
    if split.get("markSource") not in ("rename", "move", "none", "delete"):
        split["markSource"] = "rename"
    if split.get("outdirMode") not in ("same", "custom"):
        split["outdirMode"] = "same"
    split["bySize"] = bool(split.get("bySize", True))
    split["all"] = bool(split.get("all", False))
    split["recursive"] = bool(split.get("recursive", True))
    split["keepMetadata"] = bool(split.get("keepMetadata", True))
    split["overwrite"] = bool(split.get("overwrite", False))
    split["debug"] = bool(split.get("debug", False))
    try:
        split["seconds"] = max(1.0, float(split.get("seconds") or 300))
    except (TypeError, ValueError):
        split["seconds"] = 300.0
    if not isinstance(split.get("ext"), list) or not split["ext"]:
        split["ext"] = list(DEFAULT_SETTINGS["split"]["ext"])
    if not isinstance(split.get("size"), str) or not split["size"].strip():
        split["size"] = DEFAULT_SETTINGS["split"]["size"]
    if not isinstance(split.get("outdir"), str):
        split["outdir"] = ""
    if not isinstance(split.get("sourceDir"), str) or not split["sourceDir"].strip():
        split["sourceDir"] = "origin"

    watch = s["watch"]
    watch["realtime"] = bool(watch.get("realtime", True))
    for key, lo, hi, fallback in (("pollInterval", 5, 86400, 30),
                                  ("settleSeconds", 0, 86400, 60)):
        try:
            watch[key] = min(hi, max(lo, int(watch.get(key, fallback))))
        except (TypeError, ValueError):
            watch[key] = fallback
    if not isinstance(watch.get("minSize"), str):
        watch["minSize"] = "0"
    if not isinstance(watch.get("ignoreSuffixes"), list):
        watch["ignoreSuffixes"] = list(DEFAULT_SETTINGS["watch"]["ignoreSuffixes"])
    if not isinstance(watch.get("allowedRoots"), list) or not watch["allowedRoots"]:
        watch["allowedRoots"] = list(DEFAULT_SETTINGS["watch"]["allowedRoots"])

    server = s["server"]
    try:
        server["port"] = min(65535, max(1, int(server.get("port", 8099))))
    except (TypeError, ValueError):
        server["port"] = 8099
    if not isinstance(server.get("host"), str) or not server["host"].strip():
        server["host"] = "0.0.0.0"
    try:
        server["jobLogLines"] = min(100000, max(100, int(server.get("jobLogLines", 2000))))
    except (TypeError, ValueError):
        server["jobLogLines"] = 2000
    return s


# ---------------------------------------------------------------- 监控目录 / 定时任务

def load_watchpoints() -> list:
    with _lock:
        data = _read_json(WATCHPOINTS_PATH, [])
        return data if isinstance(data, list) else []


def save_watchpoints(items: list) -> None:
    with _lock:
        _atomic_write(WATCHPOINTS_PATH, items)


def load_schedules() -> list:
    with _lock:
        data = _read_json(SCHEDULES_PATH, [])
        return data if isinstance(data, list) else []


def save_schedules(items: list) -> None:
    with _lock:
        _atomic_write(SCHEDULES_PATH, items)

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

# 监控目录的「扫描方式」。这一个字段同时回答两件事：要不要自动扫、多久扫一次。
# 之所以不用「启用 + 自动扫描」两个开关，是因为两个开关会组合出「启用了但不自动扫」
# 这种要停下来想一下的状态；一个下拉反而说得更清楚。
#
#   realtime  实时监听（inotify）+ 轮询兜底，文件一落盘就切
#   interval  每隔 N 小时扫一次，N 只能取 24 的约数（理由见 SCAN_INTERVALS）
#   daily     每天 HH:MM 扫一次
#   manual    不自动扫描，只在网页上点「扫描」时才扫
SCAN_MODES = ("realtime", "interval", "daily", "manual")

# 为什么限定这几档：定时扫描交给 cron 表达式表达（0 */N * * *），
# N 必须整除 24 才能让「每 N 小时」从 0 点起均匀铺满一天，
# 否则会出现 22:00 之后 2 小时的空档。固定档位换来的是
# 「下次扫描时间」能被准确算出来，而且容器重启不会打乱节奏。
SCAN_INTERVALS = (1, 2, 3, 4, 6, 8, 12, 24)

DEFAULT_SCAN_MODE = "realtime"
DEFAULT_SCAN_INTERVAL_HOURS = 6
DEFAULT_SCAN_TIME = "03:00"


def _normalize_scan_time(value) -> str:
    """把 'H:M' / 'HH:MM' 统一成 'HH:MM'，认不出来就退回默认值。"""
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) == 2:
        try:
            hour, minute = int(parts[0]), int(parts[1])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return "%02d:%02d" % (hour, minute)
        except ValueError:
            pass
    return DEFAULT_SCAN_TIME


def normalize_watchpoint(item: dict) -> dict:
    """
    补齐监控目录的字段并纠正脏值。

    这里同时承担**老数据迁移**：早期版本只有一个 enabled 布尔字段，
    语义上「启用」= 现在的 realtime，「停用」= 现在的 manual
    （当时用户停用它的真实意图就是「别自动扫了」，而不是「删掉这个目录」）。
    """
    out = dict(item or {})
    mode = out.get("scanMode")
    if mode not in SCAN_MODES:
        out["scanMode"] = (DEFAULT_SCAN_MODE if out.get("enabled", True)
                           else "manual")
    # enabled 已经被 scanMode 取代，从存储里彻底去掉，避免两份状态打架
    out.pop("enabled", None)

    try:
        hours = int(out.get("scanIntervalHours", DEFAULT_SCAN_INTERVAL_HOURS))
    except (TypeError, ValueError):
        hours = DEFAULT_SCAN_INTERVAL_HOURS
    if hours not in SCAN_INTERVALS:
        hours = min(SCAN_INTERVALS, key=lambda h: abs(h - hours))
    out["scanIntervalHours"] = hours

    out["scanTime"] = _normalize_scan_time(out.get("scanTime"))
    out["recursive"] = bool(out.get("recursive", True))
    if not isinstance(out.get("note"), str):
        out["note"] = ""
    if not isinstance(out.get("path"), str):
        out["path"] = ""
    out.setdefault("lastScanAt", None)
    out.setdefault("videoCount", 0)
    out.setdefault("createdAt", "")
    return out


def is_auto_scan(wp: dict) -> bool:
    """该目录是否参与「自动」扫描（实时 / 定时）。manual 与 manual 之外无关。"""
    return wp.get("scanMode") in ("realtime", "interval", "daily")


def scan_cron(wp: dict) -> str | None:
    """
    把扫描方式翻译成 5 段 cron；不需要定时扫描的返回 None。
    放在 config 这一层是为了让 scheduler（排schedule用）和 API（展示下次时间用）
    共用同一份规则，不会出现「显示的时间和实际执行的时间不一致」。
    """
    mode = wp.get("scanMode")
    if mode == "interval":
        return "0 */%d * * *" % int(wp.get("scanIntervalHours") or DEFAULT_SCAN_INTERVAL_HOURS)
    if mode == "daily":
        hour, minute = _normalize_scan_time(wp.get("scanTime")).split(":")
        return "%d %d * * *" % (int(minute), int(hour))
    return None


def load_watchpoints() -> list:
    with _lock:
        data = _read_json(WATCHPOINTS_PATH, [])
        if not isinstance(data, list):
            return []
        return [normalize_watchpoint(item) for item in data if isinstance(item, dict)]


def save_watchpoints(items: list) -> None:
    with _lock:
        _atomic_write(WATCHPOINTS_PATH,
                      [normalize_watchpoint(item) for item in (items or [])])


def load_schedules() -> list:
    with _lock:
        data = _read_json(SCHEDULES_PATH, [])
        return data if isinstance(data, list) else []


def save_schedules(items: list) -> None:
    with _lock:
        _atomic_write(SCHEDULES_PATH, items)

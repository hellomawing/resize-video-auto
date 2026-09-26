#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/config.py —— 配置与数据文件的位置、读写

所有持久化都放在同一个数据目录里（容器里是 /data，本地开发是 <项目根>/data）：
    settings.json     切分/监控/服务参数
    watchpoints.json  监控目录列表（含各自的扫描方式）
    video-splitter.db SQLite：任务与日志
    archive-dirs.json 用过的归档子目录名（只增不减，见 collect_archive_dirs）

为什么用 JSON 而不是全塞进 SQLite：这几个配置项都是「人能看懂、能手改、
能直接备份走」的东西，用 JSON 更友好；只有任务流水这种量大且需要按条件
查询的数据才值得进数据库。
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from copy import deepcopy
from pathlib import Path

from core import splitter as engine

APP_VERSION = "1.0.0"

PROJECT_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(
    os.environ.get("VS_DATA_DIR") or (PROJECT_DIR / "data")
).resolve()

SETTINGS_PATH = DATA_DIR / "settings.json"
WATCHPOINTS_PATH = DATA_DIR / "watchpoints.json"
DB_PATH = DATA_DIR / "video-splitter.db"
# 「用过的归档子目录名」的记录，见 collect_archive_dirs
ARCHIVE_DIRS_PATH = DATA_DIR / "archive-dirs.json"
# 安装引导写入的「待预置监控目录」种子文件。首次读到后会被合并进
# watchpoints.json 并删除（见 load_watchpoints），避免重复写入。
SEED_WATCHPOINTS_PATH = DATA_DIR / ".vs-seed-watchpoints.json"

# ---------------------------------------------------------------- 原片处理方式

# split.markSource 的四个取值，与 core/splitter.mark_source 的 mode 参数一致：
#   rename 给原片加 #origin 后缀，留在原文件夹
#   move   把原片移到同级的归档子目录（目录名见 DEFAULT_SOURCE_DIR）
#   none   原地不动（⚠️ 原片保持原名，持续监控下会被当成新视频再切一遍）
#   delete 切分成功后直接删除原片（不可恢复）
MARK_SOURCES = ("rename", "move", "none", "delete")
DEFAULT_MARK_SOURCE = "rename"

# 归档子目录的默认名。它必须是**单层目录名**——语义就是「当前文件夹下的
# 某个子文件夹」，所以带斜杠、`.`, `..` 这类写法都要被纠正掉。
DEFAULT_SOURCE_DIR = "resize-video-origin-file"

# 历史版本用过的默认归档目录名（旧默认值是 origin）。改名之后仍要参与
# 扫描排除：早先被 move 进去的原片还躺在这些目录里，一旦不排除，
# 它们就会被当成新视频重新切一遍。
LEGACY_SOURCE_DIRS = ("origin",)

# ---------------------------------------------------------------- 默认设置

DEFAULT_SETTINGS = {
    "split": {
        # 没有「切割模式」这一项：本工具只做 ffmpeg 无损流拷贝（-c copy）。
        # 曾经有个 auto/copy/bytes 三选一，bytes 那条路按字节硬劈，
        # 产物从第 2 段起播不了，却会让原片被改名成 #origin，用户以为切好了。
        # 与其让人在「一个字节都不丢」和「每段都能播」之间做选择，
        # 不如只保留唯一正确的那条：切不动就报错、保留原片、列进失败清单。
        "bySize": True,             # True=按大小切，False=按时间切（用 seconds）
        "size": "3.9G",
        "seconds": 300,
        # 派生字段：= not bySize（按时长切必须全量入队，按大小切必须看阈值），
        # 用户不可直接设置，见 _normalize_settings
        "all": False,
        # 支持处理的格式 = 引擎里能流拷贝的那几种（见 engine.SUPPORTED_EXTS）。
        # 这里不另写一份列表：新增/删除格式只改引擎里的 SEGMENT_FRIENDLY。
        "ext": list(engine.SUPPORTED_EXTS),
        "recursive": True,
        "outdirMode": "same",       # same | custom
        "outdir": "",
        "markSource": DEFAULT_MARK_SOURCE,   # 见上方 MARK_SOURCES
        "sourceDir": DEFAULT_SOURCE_DIR,
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
        # 没有「可访问根目录白名单」：网页上能浏览/添加的范围**只由容器挂载决定** ——
        # 你在 docker-compose 里挂进来的目录就是范围（见 app/api/system.py 的挂载探测）。
    },
    "server": {
        "host": "0.0.0.0",
        "port": 8099,
        "jobLogLines": 2000,        # 单个任务最多保留多少行日志
    },
}

_lock = threading.RLock()

# 「用过的归档目录名」的进程内缓存。None = 还没从磁盘读过一次；
# 之后以内存为准（所以手工改 archive-dirs.json 要重启才生效）。
_known_dirs = None


# ---------------------------------------------------------------- 通用读写

def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _log(msg: str) -> None:
    print("[config] %s" % msg, flush=True)


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
        # None 不是一份有效配置，只当「没写」处理。放过去会把整块结构替换成 None
        # （PATCH 里显式传 null、或手改 JSON 时写了个 null 都会走到这），
        # 后面的规范化逻辑再取字段就直接崩了。
        if value is None:
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


def patch_settings(partial: dict) -> dict:
    """字段级局部更新：只动传进来的那些字段，其余保持当前值。

    刻意不复用 save_settings —— 它的合并基准是 DEFAULT_SETTINGS，
    只传 {"watch": {"realtime": False}} 会把 split、server 整块打回默认值。
    这里以**当前设置**为基准，才是「改一个开关」该有的语义。
    """
    with _lock:
        # _lock 是 RLock，这里重入取一次当前配置是安全的；
        # 走同一条锁路径也保证了「读-改-写」不会被并发写插队。
        current = load_settings()
        merged = _normalize_settings(_deep_merge(current, partial))
        _atomic_write(SETTINGS_PATH, merged)
        return merged


def _normalize_settings(s: dict) -> dict:
    """把明显不合法的值纠正回可用范围，避免脏配置把服务卡住。"""
    split = s["split"]
    if split.get("markSource") not in MARK_SOURCES:
        split["markSource"] = DEFAULT_MARK_SOURCE
    if split.get("outdirMode") not in ("same", "custom"):
        split["outdirMode"] = "same"
    split["bySize"] = bool(split.get("bySize", True))
    # 「忽略大小，全部切分」不再由用户直接控制，随定段方式联动派生：
    # 按大小切（bySize=True）→ 关（不超过阈值的不切，否则小视频会被
    # 空忙一遍重封装成 1 段）；按时长切 → 开（扫描器不读时长、只按体积
    # 快速筛选，必须放行否则按时长切分对绝大多数视频无效）。
    split["all"] = not split["bySize"]
    split["recursive"] = bool(split.get("recursive", True))
    split["keepMetadata"] = bool(split.get("keepMetadata", True))
    split["overwrite"] = bool(split.get("overwrite", False))
    split["debug"] = bool(split.get("debug", False))
    try:
        split["seconds"] = max(1.0, float(split.get("seconds") or 300))
    except (TypeError, ValueError):
        split["seconds"] = 300.0
    # 支持格式：只认引擎能无损切分的那些。老配置里可能还留着
    # avi/wmv/flv/mpg/mpeg/3gp/rmvb/vob —— 它们是靠「copy 失败就偷偷按字节切」
    # 才处理得动的，那条路已经取消。留着它们只会让扫描反复把文件送进队列，
    # 然后在切割时报「该格式不支持无损切分」，不如在读取配置这一层就滤掉。
    supported = list(engine.SUPPORTED_EXTS)
    raw_exts = split.get("ext")
    if not isinstance(raw_exts, list):
        raw_exts = []
    kept = []
    for item in raw_exts:
        ext = str(item).strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        if ext in supported and ext not in kept:
            kept.append(ext)
    # 一个都没剩下（比如用户当初只填了 .avi）就回到默认全量：
    # 空列表在扫描器眼里等于「什么都不处理」，那才真的像坏了。
    split["ext"] = kept or supported
    if not isinstance(split.get("size"), str) or not split["size"].strip():
        split["size"] = DEFAULT_SETTINGS["split"]["size"]
    if not isinstance(split.get("outdir"), str):
        split["outdir"] = ""
    # 老配置里的 "origin" 是历史默认值（不是用户的刻意选择），统一迁到新默认名；
    # 顺带把带斜杠、`.`、`..` 这类非法写法一并纠正
    split["sourceDir"] = normalize_source_dir(split.get("sourceDir"))

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
    # 历史上的「可访问根目录白名单」已删除（范围改由容器挂载决定）。
    # 旧 settings.json 里可能还留着这个键，直接丢弃，别让它继续出现在设置里。
    watch.pop("allowedRoots", None)

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


# ---------------------------------------------------------------- 监控目录

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


# ---------------------------------------------------------------- 过滤规则

# 一条规则的两种写法，与 app/models.py 的 FilterMode 必须一致。
#   contains 包含某串（忽略大小写）—— 常用的那种，用户不必学正则
#   regex    正则表达式 —— 只有复杂规则才需要
FILTER_MODES = ("contains", "regex")


def _normalize_ext_list(raw) -> list:
    """文件类型列表：小写、补点、只留引擎能无损切分的那些，去重且保持顺序。

    「只留引擎支持的」是硬约束：库里写个 .avi 进去，扫描器放行、切分那一关
    仍然会拦下来（本工具只做无损流拷贝），规则就成了看得见却不生效的死配置。
    """
    supported = set(engine.SUPPORTED_EXTS)
    out = []
    for item in raw if isinstance(raw, list) else []:
        ext = str(item or "").strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        if ext in supported and ext not in out:
            out.append(ext)
    return out


def _normalize_rules(raw, strict: bool = False) -> list:
    """名字规则列表：丢掉空规则，正则必须能编译。

    strict=True 时正则编不过直接抛 ValueError（API 入口用，让用户当场看到
    是哪条写错了）；False 时静默丢掉那一条 —— 一个手改坏的正则不该让整个
    监控目录读不出来，更重要的是不能让扫描因为规则解析失败而报错。
    """
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        mode = str(item.get("mode") or "contains").strip().lower()
        if mode not in FILTER_MODES:
            mode = "contains"
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        if mode == "regex":
            try:
                re.compile(value)
            except re.error as exc:
                if strict:
                    raise ValueError("正则表达式写错了「%s」：%s" % (value, exc))
                continue
        out.append({"mode": mode, "value": value})
    return out


def normalize_filters(raw, strict: bool = False) -> dict:
    """归一化某个监控目录的过滤规则，返回落盘用的 dict（camelCase）。

    排除优先于仅限：同一个类型两边都写了，仅限那一侧永远不可能生效，
    这里直接从仅限里去掉 —— 留着只会让人对着界面想「为什么它没用」。
    """
    raw = raw if isinstance(raw, dict) else {}
    ext_include = _normalize_ext_list(raw.get("extInclude"))
    ext_exclude = _normalize_ext_list(raw.get("extExclude"))
    return {
        "extInclude": [e for e in ext_include if e not in ext_exclude],
        "extExclude": ext_exclude,
        "nameInclude": _normalize_rules(raw.get("nameInclude"), strict),
        "nameExclude": _normalize_rules(raw.get("nameExclude"), strict),
    }


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

    # 原片处理方式。空串 = 跟随系统设置，**刻意不在这里补默认值**：
    # 「跟随」本身是一个有意义的状态，补成具体值之后就再也升不了级了。
    if out.get("markSource") not in MARK_SOURCES:
        out["markSource"] = ""
    raw_dir = out.get("sourceDir")
    out["sourceDir"] = (normalize_source_dir(raw_dir)
                        if isinstance(raw_dir, str) and raw_dir.strip() else "")
    # 过滤规则：老监控目录没有这个键 -> 补成空规则 = 不过滤，
    # 升级后行为与升级前完全一致（这是刻意的，不改变任何已有目录的扫描结果）
    out["filters"] = normalize_filters(out.get("filters"))
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
    放在 config 这一层是为了让 scheduler（排计划用）和 API（展示下次时间用）
    共用同一份规则，不会出现「显示的时间和实际执行的时间不一致」。
    """
    mode = wp.get("scanMode")
    if mode == "interval":
        return "0 */%d * * *" % int(wp.get("scanIntervalHours") or DEFAULT_SCAN_INTERVAL_HOURS)
    if mode == "daily":
        hour, minute = _normalize_scan_time(wp.get("scanTime")).split(":")
        return "%d %d * * *" % (int(minute), int(hour))
    return None


def _ingest_seed_watchpoints() -> None:
    """把安装引导的种子文件合并进 watchpoints.json，合并后删除种子文件。

    种子文件由 fnos/cmd/install_callback 在安装时写入，是应用在容器被创建前
    无法自行写文件时的传递通道。首次读到即合并，避免下次启动重复写入；
    种子缺失或损坏时静默忽略（例如用户从未在安装向导里填扫描文件夹）。
    """
    seed = SEED_WATCHPOINTS_PATH
    if not seed.exists():
        return
    raw = _read_json(seed, [])
    try:
        seed.unlink()
    except OSError:
        pass
    if not isinstance(raw, list):
        return
    # 只留下路径落在容器已挂载目录、且当前配置里还没有的条目
    current = _read_json(WATCHPOINTS_PATH, [])
    if not isinstance(current, list):
        current = []
    exist = {str(w.get("path") or "").rstrip("/") for w in current
             if isinstance(w, dict) and w.get("path")}
    added = False
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        if path.rstrip("/") in exist:
            continue
        item["id"] = item.get("id") or ("wp_" + uuid.uuid4().hex[:8])
        item["createdAt"] = item.get("createdAt") or now_iso_shim()
        current.append(item)
        exist.add(path.rstrip("/"))
        added = True
    if added:
        try:
            _atomic_write(WATCHPOINTS_PATH, current)
        except OSError as exc:
            _log("写入预置监控目录失败：%s" % exc)


def now_iso_shim() -> str:
    # 延迟导入 db，避免 config ⇄ db 循环依赖
    from . import db  # noqa: PLC0415
    return db.now_iso()


def load_watchpoints() -> list:
    with _lock:
        _ingest_seed_watchpoints()
        data = _read_json(WATCHPOINTS_PATH, [])
        if not isinstance(data, list):
            return []
        return [normalize_watchpoint(item) for item in data if isinstance(item, dict)]


def save_watchpoints(items: list) -> None:
    with _lock:
        _atomic_write(WATCHPOINTS_PATH,
                      [normalize_watchpoint(item) for item in (items or [])])


# ---------------------------------------------------------------- 原片处理策略

def normalize_source_dir(value) -> str:
    """
    归档子目录名只允许是「单层目录名」。

    它的语义本来就是「当前文件夹下的某个子文件夹」，所以：
      * `origin/old`、`/abs/path` 这类多层的，只取最后一段；
      * `.` / `..` 这种会指回自身或上级的，直接退回默认名；
      * 空的也退回默认名；
      * 历史默认名（origin）也退回默认名 —— 那不是用户的刻意选择，
        留着只会让老配置永远停在旧名字上。

    这个目录名最终会拼进文件路径，在这里挡住比事后再补救靠前得多。
    """
    text = str(value or "").strip().replace("\\", "/").strip("/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1].strip()
    if not text or text in (".", ".."):
        return DEFAULT_SOURCE_DIR
    if text.lower() in LEGACY_SOURCE_DIRS:
        return DEFAULT_SOURCE_DIR
    return text


def resolve_mark_policy(settings: dict, watchpoint: dict = None,
                        override: dict = None) -> dict:
    """
    决定「这一个任务该怎么处理原片」。优先级：

        本次手动指定  >  该监控目录的设置  >  系统设置里的默认值

    返回 {"markSource": ..., "sourceDir": ...}，会被**快照进任务记录**。

    为什么不在执行时才去读设置：任务在队列里可能等很久，中途改一次设置就让
    排在后面的任务换一套行为，会出现「同一个目录扫出来的两批任务处理方式不
    一样」这种没法解释的结果；而且事后翻任务也看不出它当时用的是哪条规则。
    快照语义下，改设置只影响之后入队的任务。
    """
    split = (settings or {}).get("split") or {}
    fallback_mark = split.get("markSource")
    out = {
        "markSource": (fallback_mark if fallback_mark in MARK_SOURCES
                       else DEFAULT_MARK_SOURCE),
        "sourceDir": normalize_source_dir(split.get("sourceDir")),
    }

    # 两层覆盖依次叠上去，后一层（本次手动）自然压过前一层（该目录设置）
    for layer in (watchpoint, override):
        layer = layer or {}
        mark = layer.get("markSource")
        if mark in MARK_SOURCES:
            out["markSource"] = mark
        raw_dir = layer.get("sourceDir")
        if isinstance(raw_dir, str) and raw_dir.strip():
            out["sourceDir"] = normalize_source_dir(raw_dir)
    return out


def load_known_archive_dirs() -> set:
    """
    曾经被当作归档目录用过的名字，供扫描排除。

    名字在这里是**不透明字符串**：只去掉首尾空白，不做 normalize_source_dir。
    原因是归一化会把历史名 `origin` 折成新默认名，而 `origin/` 目录里的老原片
    正是靠这个名字才被排除的（见 normalize_source_dir 的说明）。
    归档目录名只参与**比较**、从不拼进路径，所以没有必须归一化的理由；
    真正要归一化的是配置里那些值，那一步在 collect_archive_dirs 里做。

    文件不存在或损坏时返回空集：这顶多是「这一次少排除几个名字」，
    不能让扫描因为一个辅助文件读不出来就卡住。原文件留在原地供人工排查。
    """
    global _known_dirs
    with _lock:
        if _known_dirs is None:
            raw = _read_json(ARCHIVE_DIRS_PATH, [])
            if not isinstance(raw, list):
                raw = []
            _known_dirs = {v.strip() for v in raw
                           if isinstance(v, str) and v.strip()}
        return set(_known_dirs)


def remember_archive_dirs(names) -> set:
    """
    记下归档目录名，持久化成**只增不减**的集合，返回合并后的全集。

    调用方直接拿返回值去排除即可，不必再读一次盘。名字按原样存，
    不归一化——理由见 load_known_archive_dirs。

    没有新名字时不写盘：这个函数落在扫描热路径上（每轮轮询、每次扫描都会走到），
    每次都重写一遍文件没有意义，只会让 mtime 不停变。
    """
    global _known_dirs
    with _lock:
        known = load_known_archive_dirs()
        fresh = {n.strip() for n in (names or ())
                 if isinstance(n, str) and n.strip()}
        if not fresh - known:
            return known
        merged = known | fresh
        try:
            _atomic_write(ARCHIVE_DIRS_PATH, sorted(merged))
        except OSError as exc:
            # 写不进去不致命，但会退回「改过名就不再排除」的老毛病（会重切原片），
            # 所以要说出来。**不更新内存缓存**，下次扫描还会再试一次。
            _log("归档目录使用记录写盘失败：%s" % exc)
            return merged
        _known_dirs = merged
        return merged


def collect_archive_dirs(settings: dict = None, watchpoints=None,
                         extra=None) -> set:
    """
    列出所有「可能被当作归档目录」的名字，供扫描排除使用。

    四个来源：
      1. 当前配置——系统设置里的默认名 + 各监控目录分别设置的名字
      2. 本次手动指定的名字（extra）
      3. 历史默认名（origin）
      4. **曾经用过的名字**（archive-dirs.json，只增不减）

    为什么是个集合而不是单个名字：归档目录可以按监控目录分别设置，
    A 目录用 `origin`、B 目录用别的名字；扫描 A 时如果不把 B 的名字也排除掉，
    那些被 move 走的原片就会被当成新视频再切一遍。

    为什么第 4 类要落盘：前 3 类都按**当前配置现算**，名字一改就掉出集合；
    而 move 归档过去的原片**保留原文件名**，掉出去看起来就是个新视频 ——
    会被重切一遍再标记一次（2026-09-21 真机可复现）。

    方向刻意偏向「多排除」：多排除顶多漏扫一个目录（扫描结果里会写明
    「位于原片归档目录」），漏排除却会让原片和切片一起报废。代价是集合只增不减 ——
    真想让某个名字放行，只能手工编辑 archive-dirs.json 并重启服务
    （进程内有一份缓存，改文件不会立刻生效）。
    """
    names = set(LEGACY_SOURCE_DIRS)
    if settings is None:
        settings = load_settings()
    names.add(normalize_source_dir((settings or {}).get("split", {}).get("sourceDir")))

    if watchpoints is None:
        watchpoints = load_watchpoints()
    for wp in watchpoints or ():
        raw = (wp or {}).get("sourceDir")
        if isinstance(raw, str) and raw.strip():
            names.add(normalize_source_dir(raw))

    for raw in (extra or ()):
        if isinstance(raw, str) and raw.strip():
            names.add(normalize_source_dir(raw))

    # 用过即记住：这一轮算出来的名字落盘，下一轮即便配置里已经没有它们了，
    # 旧归档目录里的原片也仍然会被排除
    return remember_archive_dirs({n for n in names if n})

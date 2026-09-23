#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/api/system.py —— 健康检查、统计、目录浏览
"""

from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from .. import config, db
from ..models import BrowseOut, DirItem, DirShortcut, HealthOut, StatsOut, ToolInfo
from core import splitter as engine

router = APIRouter(tags=["system"])
_STARTED = time.time()


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    ffmpeg = engine.find_bin("ffmpeg")
    ffprobe = engine.find_bin("ffprobe")
    return HealthOut(
        ok=True,
        version=config.APP_VERSION,
        python=platform.python_version(),
        ffmpeg=ToolInfo(path=ffmpeg, version=engine.tool_version(ffmpeg),
                        ok=bool(ffmpeg)),
        ffprobe=ToolInfo(path=ffprobe, version=engine.tool_version(ffprobe),
                         ok=bool(ffprobe)),
        time=db.now_iso(),
        uptime_sec=round(time.time() - _STARTED, 1),
    )


@router.get("/stats", response_model=StatsOut)
def stats() -> StatsOut:
    summary = db.stats_summary()
    return StatsOut(
        jobs=summary["jobs"],
        watchpoints=len(config.load_watchpoints()),
        today_bytes=summary["todayBytes"],
        total_bytes=summary["totalBytes"],
        total_parts=summary["totalParts"],
    )


# ---------------------------------------------------------------- 目录浏览

# 伪文件系统与容器自身部件：mountinfo 里这些是系统件，不是用户挂进来的数据目录
_PSEUDO_FSTYPES = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
    "overlay", "mqueue", "shm", "securityfs", "debugfs", "tracefs",
    "pstore", "bpf", "autofs", "binfmt_misc", "hugetlbfs", "configfs",
    "fusectl", "ramfs", "nsfs", "efivarfs",
}

# 容器自身的挂载点，绝不能当数据根暴露出去（/data 是整个程序状态所在）
_MOUNT_NEVER_ROOTS = ("/", "/data")

# Docker 存储驱动的容器私有镜像层。当 docker 数据根落在某个被 bind 挂进容器的
# 卷里（如本机 Docker Root Dir=/vol1/docker，而 /vol1 整体被挂进来）时，mountinfo
# 会出现 /vol1/docker/overlay2/<id>/merged/<...> 这类条目——它们是把容器**自身的
# 根文件系统/数据目录**镜像出来的，绝不是用户数据，绝不能当可选存储位置下发。
# Docker 的 overlay2 数据目录恒叫 overlay2，/overlay2/ 出现在路径里基本就能判定。
_MOUNT_CONTAINER_STORAGE = "/overlay2/"


def _unescape_mountpoint(text: str) -> str:
    # mountinfo 里空格等特殊字符按八进制转义（\040 等）
    return (text.replace(r"\040", " ").replace(r"\011", "\t")
                .replace(r"\012", "\n").replace(r"\134", "\\"))


def _parse_mountinfo(text: str) -> list:
    """从 /proc/self/mountinfo 文本里挑出「疑似用户挂载」的挂载点。

    容器里 mountinfo 的真实文件系统条目，排除伪文件系统与容器自部件
    （根 overlay、/data 卷）之后，剩下的就是 compose / docker run 里
    -v 显式挂进来的路径（如 /vol1、/test-dir）。
    """
    roots = []
    for line in text.splitlines():
        fields = line.split()
        if "-" not in fields:
            continue
        sep = fields.index("-")
        if len(fields) < sep + 2 or len(fields) < 5:
            continue
        mountpoint = _unescape_mountpoint(fields[4])
        fstype = fields[sep + 1]
        if fstype in _PSEUDO_FSTYPES or mountpoint in _MOUNT_NEVER_ROOTS:
            continue
        # Docker overlay2 容器私有镜像层（见上面的常量注释），不是用户数据挂载
        if _MOUNT_CONTAINER_STORAGE in mountpoint:
            continue
        roots.append(mountpoint)
    return roots


def _detect_mounted_roots() -> list:
    """读取本进程的 mountinfo，返回实际存在的数据目录挂载点。

    非 Linux（本机开发）没有 /proc，返回空表 —— 此时靠 VS_EXTRA_ROOTS 兜底。
    """
    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    found = []
    for mp in _parse_mountinfo(text):
        # 挂载文件（Docker 自动挂的 /etc/hosts 之类）不是目录，滤掉
        if os.path.isdir(mp):
            found.append(mp)
    return found


def _extra_roots_from_env() -> list:
    """开发/测试兜底：非容器环境（没有 /proc）可以用环境变量补几个根目录。

    只认环境变量，界面与文档主流程都不出现它 —— 正常部署时范围由挂载决定。
    """
    raw = (os.environ.get("VS_EXTRA_ROOTS") or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(os.pathsep) if p.strip()]
    # 与挂载探测保持一致：根目录恒为**实际存在**的目录（写错的不进下拉框）
    return [Path(p) for p in parts if os.path.isdir(p)]


def _accessible_roots() -> list:
    """网页上可浏览、可添加的范围 —— **只由容器挂载决定**。

    没有「白名单」这个概念了：你在 Docker 里挂进来的数据目录就是范围。
    挂载探测已排除根 overlay、/data 卷与系统伪文件系统（见 _parse_mountinfo），
    所以程序自己的数据库和代码目录不会出现在网页上。
    """
    roots, known = [], set()
    for path in _detect_mounted_roots() + [str(p) for p in _extra_roots_from_env()]:
        key = str(path)
        if key not in known:
            known.add(key)
            roots.append(Path(path))
    return roots


def _probe_enumerable_children(root: Path, limit: int = 8) -> list:
    """
    根目录不可枚举时，探测它下面有哪些「存在且能正常列出」的子目录。

    为什么只试数字名：fnOS 的存储空间布局是 /volX/<uid>（实测 /vol1/1000），
    uid 是数字且常见在 0~2999；@ 开头的是系统目录（@appdata…），不该引导用户进去。
    readdir 被拒只挡「列出 /vol1 有什么」，不挡「按名字直接走到 /vol1/1000」——
    这是内核权限模型的基本行为（x 与 r 是两回事），所以 stat 探测是可行的。
    """
    found = []
    try:
        for n in range(0, 3000):
            if len(found) >= limit:
                break
            child = root / str(n)
            try:
                if not child.is_dir():
                    continue
                # 能 stat 不代表能 readdir，再试一把列出
                with os.scandir(child) as it:
                    next(it, None)
            except PermissionError:
                continue
            except OSError:
                continue
            found.append(str(child))
    except OSError:
        return found
    return found


def _scandir_error(target: Path) -> list:
    """尝试列出 target；被拒时返回探测到的可直达子目录，能列则返回空表。"""
    try:
        with os.scandir(target):
            return []
    except PermissionError:
        return _probe_enumerable_children(target)
    except OSError:
        return []


def _within(path: Path, roots) -> bool:
    """路径是否落在某个可访问根目录之内（含根本身）。"""
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _parent_within(target: Path, roots) -> str | None:
    """
    上一级目录 —— 但只在它**仍落在可访问范围内**时才下发。

    挂进来的往往是某个深层子目录（真机：/vol1/1000/video-split-in → /test-video），
    它的父目录（`/` 或 `/vol1/1000`）本身并不在范围里；照直下发的话，前端会摆出
    一个点了必然 403 的「上一级」按钮。
    """
    up = target.parent
    if up == target:
        return None
    return str(up) if _within(up, roots) else None


def ensure_allowed(path: Path, roots=None) -> Path:
    """
    确认路径落在容器**已挂载**的目录内。这是网页上所有「用户给路径」的入口
    都必须过的一道闸——容器看不见的路径本来也用不了，这道闸只是把
    「路径拼错了 / 忘了挂载」变成一句明确的提示，而不是一个诡异的报错。
    """
    roots = roots if roots is not None else _accessible_roots()
    if not roots:
        raise HTTPException(
            status_code=400,
            detail="没有检测到任何已挂载的目录。请把要处理的目录挂进容器"
                   "（docker-compose 里的 volumes），再重新打开这个页面。")
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="路径无法解析：%s" % exc)
    if _within(resolved, roots):
        return resolved
    raise HTTPException(
        status_code=403,
        detail="路径 %s 不在容器已挂载的目录内（已挂载：%s）。"
               "要把这个目录纳入可访问范围，请在 docker-compose 的 volumes 里挂载它。"
               % (resolved, "、".join(str(r) for r in roots)))


def _collect_shortcuts(settings, roots) -> list:
    """目录选择器的「常用目录」。详见 models.DirShortcut 的注释。

    三个来源，按「用户最可能想去」排序：
      1. 已添加的监控目录 —— 撤销与重切的主战场，备注顺手当说明
      2. 最近任务出现过的目录 —— 手动扫过、切过的地方
      3. 系统设置里的输出目录 —— 哪怕只用过一次也得看得见

    可访问范围之外的目录不给入口：点了也是 403，摆出来只会让人白跑一趟。
    """
    items, seen = [], set()

    def add(raw, name, kind, note=""):
        raw = (raw or "").strip()
        if not raw:
            return
        try:
            resolved = Path(raw).resolve()
        except OSError:
            return
        key = str(resolved)
        if key in seen or not _within(resolved, roots):
            return
        seen.add(key)
        items.append(DirShortcut(name=name or resolved.name or key, path=key,
                                 kind=kind, note=note))

    for wp in config.load_watchpoints():
        path = wp.get("path") or ""
        add(path, Path(path).name, "watchpoint", wp.get("note") or "")
    # 读库失败不该让整个目录列表挂掉 —— 快捷入口是锦上添花，不是必需品
    try:
        for raw in db.recent_job_dirs():
            add(raw, Path(raw).name, "job", "最近处理过")
    except Exception:                                  # noqa: BLE001
        pass
    outdir = settings["split"].get("outdir") or ""
    add(outdir, Path(outdir).name, "setting", "系统设置里的输出目录")
    return items


@router.get("/browse", response_model=BrowseOut)
def browse(path: str = Query(default=None, description="要浏览的目录，省略则返回已挂载的根目录")):
    roots = _accessible_roots()
    settings = config.load_settings()
    shortcuts = _collect_shortcuts(settings, roots)

    # 根目录来自挂载探测，本身都是存在的目录，直接把路径文本下发
    root_texts = [str(r) for r in roots]

    if not path:
        dirs = [DirItem(name=Path(r).name or r, path=r) for r in root_texts]
        error = None
        if not dirs:
            error = ("没有检测到任何已挂载的目录。请在 docker-compose 的 volumes 里"
                     "把要处理的目录挂进容器，然后重新打开这个页面。")
        # 打开选择器的第一眼就探一遍：某个根不可枚举（fnOS 的 /vol1）时，
        # 直接把能进的层摆出来，省得用户点了根、看完一大段报错才知道有出路
        suggestions = []
        for r in root_texts:
            for hit in _scandir_error(Path(r)):
                if hit not in suggestions:
                    suggestions.append(hit)
        return BrowseOut(path="", parent=None, roots=root_texts, dirs=dirs,
                         shortcuts=shortcuts, suggested_roots=suggestions,
                         video_count=0, error=error)

    target = ensure_allowed(Path(path), roots)

    if not target.is_dir():
        return BrowseOut(path=str(target), parent=_parent_within(target, roots),
                         roots=root_texts, dirs=[], shortcuts=shortcuts,
                         video_count=0,
                         error="目录不存在，或者容器没有权限访问它"
                               "（确认路径拼写、以及它是否已挂载进容器）")

    exts = set(engine.normalize_exts(settings["split"].get("ext")))
    dirs, videos, error, suggestions = [], 0, None, []
    try:
        with os.scandir(target) as it:
            for entry in it:
                try:
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(DirItem(name=entry.name, path=entry.path))
                    elif Path(entry.name).suffix.lower() in exts:
                        videos += 1
                except OSError:
                    continue
    except PermissionError:
        # 这一条真机上必然踩到：fnOS 的存储池根 /vol1 权限位是 000、
        # 没有扩展 ACL，readdir 直接 EACCES，但**访问它下面的目录完全正常**。
        # 所以别说一句「没有权限」就完事，得给出路 —— 否则用户会以为
        # 是自己配置错了，或者以为整块盘都读不了。
        suggestions = _probe_enumerable_children(target)
        error = ("没有权限列出该目录的子目录 —— fnOS 这类系统把存储池根目录"
                 "（/vol1）故意设成不可枚举，这不代表下面的目录不可用。"
                 + ("下面已列出探测到的「可直接进入」目录，点一下即可继续。"
                    if suggestions else
                    "请用上方的「常用目录」直达已知目录，或改挂它下面的某个子目录。"))
    except OSError as exc:
        error = "读取目录失败：%s" % exc

    # @ 开头的是系统目录（@appdata、@appshare 之类），排到后面去
    dirs.sort(key=lambda d: (d.name.startswith("@"), d.name.lower()))
    return BrowseOut(path=str(target), parent=_parent_within(target, roots),
                     roots=root_texts, dirs=dirs, shortcuts=shortcuts,
                     suggested_roots=suggestions, video_count=videos, error=error)


@router.get("/env")
def env_info() -> dict:
    """调试用：看看容器里的关键环境，排查挂载/权限问题很有用。"""
    return {
        "python": sys.executable,
        "pythonVersion": platform.python_version(),
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "dataDir": str(config.DATA_DIR),
        "dataDirWritable": os.access(config.DATA_DIR, os.W_OK),
        "ffmpeg": engine.find_bin("ffmpeg"),
        "ffprobe": engine.find_bin("ffprobe"),
        "accessibleRoots": [
            {"path": str(r), "exists": r.is_dir(), "writable": os.access(r, os.W_OK)}
            for r in _accessible_roots()
        ],
        "uid": os.getuid() if hasattr(os, "getuid") else None,
        "gid": os.getgid() if hasattr(os, "getgid") else None,
        # 监听地址/端口是「环境变量优先于设置」（见 main.py 的启动逻辑），
        # 而容器的 Dockerfile 已经固定了这两个变量 —— 也就是说设置页里改它们
        # 根本不会生效。把变量的存在与取值透出去，界面据此把这两项标成只读，
        # 免得给用户一个改了没反应、还让他以为改坏了的输入框。
        "hostEnv": os.environ.get("VS_HOST") or None,
        "portEnv": os.environ.get("VS_PORT") or None,
    }

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/services/monitor.py —— 目录监控（实时监听 + 轮询兜底）

两条腿走路是刻意的，不是冗余：

* **实时监听（watchdog / inotify）**：本地磁盘上响应快，新文件几秒内就被发现。
* **轮询兜底**：inotify 在网络共享（SMB / NFS）上根本不工作——内核收不到
  远端服务器的变更通知。所以只要监控目录可能是网络挂载，就必须有轮询。
  另外它还能兜住「容器重启期间落进来的文件」这类漏网情况。

监控到新文件后不直接入队，而是交给 scanner.consider_file 做稳定检测；
检测不通过就延后几秒再看一次，直到文件不再变化。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .. import config
from ..services import filters as filter_rules
from ..services import scanner
from ..services.events import bus
from core import splitter as engine

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    HAS_WATCHDOG = True
except Exception:                      # noqa: BLE001
    HAS_WATCHDOG = False
    FileSystemEventHandler = object


def _log(msg: str) -> None:
    print("[monitor] %s" % msg, flush=True)


class _Handler(FileSystemEventHandler):
    """把 watchdog 事件收敛成「这个文件需要检查一下」。"""

    def __init__(self, service: "MonitorService", watchpoint_id: str,
                 exts: set) -> None:
        self._service = service
        self._wp_id = watchpoint_id
        self._exts = exts

    def _submit(self, path: str, is_directory: bool) -> None:
        if is_directory:
            return
        if engine.is_internal_temp(path):
            # 本工具自己切分中途写下的分段，扩展名也是 .mp4，不排除的话
            # 会被当成新视频入队（工作目录通常建在监控目录之外，这里是兜底）
            return
        if Path(path).suffix.lower() not in self._exts:
            return
        self._service.submit_file(path, self._wp_id)

    def on_created(self, event):                      # noqa: D102
        self._submit(event.src_path, event.is_directory)

    def on_modified(self, event):                     # noqa: D102
        self._submit(event.src_path, event.is_directory)

    def on_moved(self, event):                        # noqa: D102
        # 拷贝工具常常先写临时文件再改名，所以目的地才是真正要关心的
        dest = getattr(event, "dest_path", None)
        self._submit(dest or event.src_path, event.is_directory)


class MonitorService:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._observer = None
        self._lock = threading.Lock()
        # path -> (最早可检查时间, watchpoint_id)
        self._pending: dict[str, tuple[float, str]] = {}
        self._exts: set = set()
        self._realtime_active = False

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        self.reload()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="monitor",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._stop_observer()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def reload(self) -> None:
        """监控目录或设置变了之后重新装配监听。"""
        settings = config.load_settings()
        self._exts = set(engine.normalize_exts(settings["split"].get("ext")))

        self._stop_observer()
        want_realtime = bool(settings["watch"].get("realtime", True))
        if not want_realtime or not HAS_WATCHDOG:
            self._realtime_active = False
            if want_realtime and not HAS_WATCHDOG:
                _log("未安装 watchdog，实时监听不可用，已退化为纯轮询")
            return

        observer = Observer()
        scheduled = 0
        handler_cache: dict[tuple[str, tuple], _Handler] = {}
        for wp in config.load_watchpoints():
            # 只有「实时监听」模式才挂 inotify。改成「每隔 N 小时」「每天几点」
            # 或「仅手动」的目录，扫描由 scheduler 或用户点击驱动，这里一概不碰。
            if wp.get("scanMode") != "realtime":
                continue
            path = Path(wp["path"])
            if not path.is_dir():
                continue
            key = (wp["id"], tuple(sorted(self._exts)))
            if key not in handler_cache:
                handler_cache[key] = _Handler(self, wp["id"], self._exts)
            try:
                observer.schedule(handler_cache[key], str(path),
                                  recursive=bool(wp.get("recursive", True)))
                scheduled += 1
            except Exception as exc:                  # noqa: BLE001
                # 网络挂载、权限不足、inotify 句柄用尽都会走到这里，
                # 不该让整个监控服务挂掉——轮询还能兜住。
                _log("无法实时监听 %s：%s（该目录改由轮询兜底）" % (path, exc))

        if scheduled:
            try:
                observer.start()
                self._observer = observer
                self._realtime_active = True
                _log("实时监听已启动，共 %d 个目录" % scheduled)
            except Exception as exc:                  # noqa: BLE001
                _log("实时监听启动失败：%s（改用纯轮询）" % exc)
                self._realtime_active = False
        else:
            self._realtime_active = False

    def _stop_observer(self) -> None:
        observer, self._observer = self._observer, None
        if observer is None:
            return
        try:
            observer.stop()
            observer.join(timeout=5)
        except Exception:
            pass

    @property
    def realtime_active(self) -> bool:
        return self._realtime_active

    # ------------------------------------------------------------ 文件投递

    def submit_file(self, path: str, watchpoint_id: str) -> None:
        """
        登记一个待检查文件。延迟 1 秒再检查：拷贝过程中一个文件会连续触发
        很多次事件，攒一下能少做很多无用的 stat。
        """
        with self._lock:
            # 已经在待办里就别把时间往后推，否则持续写入会一直被推迟
            if path not in self._pending:
                self._pending[path] = (time.time() + 1.0, watchpoint_id)

    # ------------------------------------------------------------ 主循环

    def _loop(self) -> None:
        next_poll = 0.0        # 0 = 启动后立刻先全量扫一遍
        while not self._stop.is_set():
            try:
                settings = config.load_settings()
                spec = scanner.build_spec(settings)
                now = time.time()

                if now >= next_poll:
                    self._poll(spec)
                    next_poll = now + max(5, int(settings["watch"].get("pollInterval") or 30))

                self._drain_pending(spec)
            except Exception as exc:                  # noqa: BLE001
                _log("监控循环出错（已忽略，下一轮继续）：%s" % exc)

            self._stop.wait(1.0)

    def _poll(self, spec: dict) -> None:
        """
        轮询兜底：把「实时监听」模式的监控目录整体扫一遍。

        轮询是实时监听的替补（inotify 在 SMB/NFS 上不工作），所以两者绑在同一个
        模式下；定时扫描的目录由 scheduler 负责，这里扫会把「每隔 6 小时」
        变成「每 30 秒」，等于设置失效。
        """
        watchpoints = [w for w in config.load_watchpoints()
                       if w.get("scanMode") == "realtime"]
        if not watchpoints:
            return
        for wp in watchpoints:
            try:
                result = scanner.scan_watchpoint(wp, trigger="watch")
            except Exception as exc:                  # noqa: BLE001
                _log("扫描 %s 失败：%s" % (wp.get("path"), exc))
                continue
            # 只有真的发现了东西才推事件，否则每 30 秒刷一次前端会很吵
            if result.get("queued") or result.get("waiting"):
                bus.publish({
                    "type": "scan.finished",
                    "watchpointId": wp.get("id"),
                    "found": result.get("found", 0),
                    "queued": result.get("queued", 0),
                    "waiting": result.get("waiting", 0),
                    "message": result.get("message", ""),
                })

    def _drain_pending(self, spec: dict) -> None:
        now = time.time()
        with self._lock:
            due = [(p, wp) for p, (t, wp) in self._pending.items() if t <= now]
            for p, _ in due:
                self._pending.pop(p, None)

        # 归档目录判断要用「该文件属于哪个监控目录」当扫描根，把路径切成
        # 根之下的部分再比（见 engine.is_in_archive_dir）。过滤规则同理。
        # 待检队列通常只有个位数，每个 tick 读一遍配置就够了。
        wps = config.load_watchpoints()
        roots = {w.get("id"): w.get("path") for w in wps}
        # 规则每轮编译一次（条数很少），别在每个文件上重复编译同一批正则
        wfilters = {w.get("id"): filter_rules.compile_filters(w.get("filters"))
                    for w in wps}

        for path, wp_id in due:
            try:
                root = roots.get(wp_id)
                status, reason = scanner.consider_file(
                    path, spec, "watch", wp_id, roots=[root] if root else None,
                    compiled_filters=wfilters.get(wp_id))
            except Exception as exc:                  # noqa: BLE001
                _log("检查 %s 出错：%s" % (path, exc))
                continue
            if status == "waiting":
                # 还没拷完：过一会儿再看。间隔取稳定阈值的四分之一，
                # 既能及时跟上，又不会把 CPU 耗在反复 stat 上。
                delay = max(2.0, (spec.get("settle") or 60) / 4.0)
                with self._lock:
                    self._pending[path] = (time.time() + delay, wp_id)

        # 扫描过程中也可能攒出一批，交给下一个 tick 处理即可


monitor = MonitorService()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/services/events.py —— 进程内事件总线

用途：把 worker 线程里发生的事（任务状态变化、日志行、进度）推给所有
WebSocket 连接。worker 在普通线程里跑，WebSocket 在 asyncio 事件循环里跑，
所以跨线程投递必须走 call_soon_threadsafe，直接 put 到 asyncio.Queue 上
在别的线程里是不安全的。
"""

from __future__ import annotations

import asyncio
import threading

# 单个订阅者的队列上限。前端卡住或断网时不至于把内存撑爆——满了就丢最旧的，
# 丢掉的只是「进度刷新」这类可自愈的消息，前端重连后拉一次全量状态就补齐了。
QUEUE_SIZE = 500


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """服务启动时绑定主事件循环；绑定前 publish 会被丢弃。"""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: dict) -> None:
        """从任意线程发布事件。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def _deliver() -> None:
            with self._lock:
                targets = list(self._subscribers)
            for q in targets:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    # 丢掉最旧的一条再放新的，保证订阅者最终能看到最新状态
                    try:
                        q.get_nowait()
                        q.put_nowait(event)
                    except Exception:
                        pass

        try:
            loop.call_soon_threadsafe(_deliver)
        except RuntimeError:
            # 事件循环正在关闭，丢弃即可
            pass


bus = EventBus()

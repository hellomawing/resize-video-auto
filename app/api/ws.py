#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/api/ws.py —— WebSocket 事件推送

单向推送：服务端把任务状态、日志行、进度推给浏览器，浏览器不回消息
（前端要发指令走 REST，语义更清楚）。

用带超时的 get 而不是死等：容器里客户端可能因为休眠、断网、关标签页
而静默消失，此时不会触发 WebSocketDisconnect。定时发一条 ping，
发送失败就判定连接已断，把订阅者从总线里摘掉——
否则每关一次标签页就会在内存里留一个永远没人读的队列。
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import config, db
from ..services.events import bus

router = APIRouter(tags=["ws"])

HEARTBEAT_SEC = 20


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = bus.subscribe()
    try:
        await websocket.send_json({
            "type": "hello",
            "serverTime": db.now_iso(),
            "version": config.APP_VERSION,
        })
        while True:
            try:
                import asyncio
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SEC)
            except asyncio.TimeoutError:
                event = {"type": "ping", "serverTime": db.now_iso()}
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        # 客户端异常断开、发送失败等，都按断开处理
        pass
    finally:
        bus.unsubscribe(queue)

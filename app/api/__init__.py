#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app.api —— REST 与 WebSocket 路由"""

from fastapi import APIRouter

from . import failures, jobs, settings, system, undo, watchpoints, ws

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(settings.router)
api_router.include_router(watchpoints.router)
api_router.include_router(jobs.router)
api_router.include_router(failures.router)
api_router.include_router(undo.router)
api_router.include_router(ws.router)

__all__ = ["api_router"]

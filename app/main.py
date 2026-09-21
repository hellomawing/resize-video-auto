#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/main.py —— FastAPI 入口

启动时拉起三样东西，它们都是后台线程，互不阻塞：
    runner    任务执行器（单并发 worker）
    monitor   目录监控（实时监听 + 轮询兜底）
    scheduler 定时扫描（监控目录的「每隔 N 小时 / 每天 HH:MM」计划）

前端构建产物（web/dist）由本进程直接托管，所以整个服务只有一个端口、
一个容器，不需要额外的 nginx。前端没构建时也不报错，只给一句提示，
方便后端单独调试。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .api import api_router
from .services import runner, scheduler
from .services.events import bus
# 注意：要的是模块里那个单例，不是模块本身
from .services.monitor import monitor as monitor_service

PROJECT_DIR = Path(__file__).resolve().parent.parent
WEB_DIST = PROJECT_DIR / "web" / "dist"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init_db()
    # 事件总线必须拿到正在运行的事件循环，否则 worker 线程推的事件发不出去
    bus.bind_loop(asyncio.get_running_loop())

    runner.start_worker()
    monitor_service.start()
    scheduler.start_scheduler()
    print("[main] 服务已就绪，数据目录：%s" % config.DATA_DIR, flush=True)

    yield

    print("[main] 正在停止后台服务…", flush=True)
    monitor_service.stop()
    scheduler.stop_scheduler()
    runner.stop_worker()
    db.close_db()


app = FastAPI(
    title="video-splitter",
    description="大视频无损分割 · NAS 服务版",
    version=config.APP_VERSION,
    lifespan=lifespan,
)

app.include_router(api_router, prefix="/api")


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    """给 Docker healthcheck 用的极简探针，不查数据库。"""
    return {"ok": True}


if WEB_DIST.is_dir():
    _assets = WEB_DIST / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")
    _dist_root = WEB_DIST.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """前端是单页应用：任何非 /api 路径都回 index.html，由前端路由接管。"""
        if full_path.startswith(("api/", "docs", "redoc", "openapi.json", "healthz")):
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            candidate = (_dist_root / full_path).resolve()
            # 防目录穿越：必须确实落在 dist 内
            if _dist_root == candidate or _dist_root in candidate.parents:
                if candidate.is_file():
                    return FileResponse(candidate)
        index = _dist_root / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(status_code=404, detail="前端入口文件缺失")
else:
    @app.get("/", include_in_schema=False)
    async def no_frontend() -> JSONResponse:
        return JSONResponse({
            "message": "后端已启动，但前端还没有构建。",
            "howto": "在 web/ 目录执行 npm install && npm run build，然后重启服务。",
            "api": "/docs",
        })


def main() -> None:
    """命令行入口：python -m app.main"""
    import uvicorn

    settings = config.load_settings()
    host = os.environ.get("VS_HOST") or settings["server"]["host"]
    port = int(os.environ.get("VS_PORT") or settings["server"]["port"])
    log_level = os.environ.get("VS_LOG_LEVEL") or "info"

    print("[main] 监听 %s:%d" % (host, port), flush=True)
    uvicorn.run(app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    main()

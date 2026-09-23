#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「只保留 ffmpeg 无损切割」的回归验证。

背景 —— 这次做了两个决定：
  1. 取消「切割模式」设置项，固定 ffmpeg 无损流拷贝。
     曾经的三选一里 `bytes`（按字节硬劈）的产物从第 2 段起播不了，却会让原片
     被改名成 #origin，用户以为切好了；`auto` 更糟 —— copy 失败时静默回退 bytes。
     支持格式也随之收窄到能流拷贝的那 8 种，其余格式不再扫描。
  2. 切不动就**保留原片**并记进「处理失败的文件」，而不是悄悄换个方式切。

本脚本逐条验证：
  1. 设置里不再有 mode；老配置里的 mode 与不支持的后缀会被自动清理
  2. 不支持的后缀根本不会被扫描（连 found 都不算）
  3. 切分失败：源文件原样保留、不产生切片、记入失败清单
  4. 失败过的文件不会反复重试；替换文件后自动重新尝试
  5. 重试 / 忽略（只删记录，不动文件）两条路都通
  6. 引擎层面拒绝 bytes 模式

    python tools/verify_ffmpeg_only.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="vs-ffmpeg-only-"))
os.environ["VS_DATA_DIR"] = str(TMP / "data")

from app import config, db                                  # noqa: E402

config.ensure_dirs()
db.init_db()

INBOX = TMP / "inbox"
INBOX.mkdir(parents=True)

# 可访问范围只由容器挂载决定（白名单设置已删除）。本机没有 /proc，
# 用环境变量代替「挂载了哪些目录」。
os.environ["VS_EXTRA_ROOTS"] = str(INBOX)

# 一个「切不动」的 mp4：内容是垃圾字节，ffprobe 读不出时长。
# 用垃圾而不是真视频是刻意的 —— 本脚本只关心失败路径，不需要 ffmpeg 参与，
# 因此它在任何机器上都能跑（连装没装 ffmpeg 都不影响结论）。
BAD_MP4 = INBOX / "IMG_0001.mp4"
BAD_MP4.write_bytes(b"\x00" * 8192)
NOT_SUPPORTED = INBOX / "OLD_CLIP.avi"
NOT_SUPPORTED.write_bytes(b"\x00" * 8192)

# 整体替换式保存：没写的键会回落到默认值（save_settings 不是补丁）
config.save_settings({
    "split": {"size": "1K", "recursive": True, "markSource": "rename"},
    "watch": {"settleSeconds": 0, "realtime": False, "pollInterval": 3600},
})

from fastapi.testclient import TestClient                     # noqa: E402
from app.main import app                                      # noqa: E402
from app.services import scanner                              # noqa: E402
from core import splitter as engine                           # noqa: E402

ok = 0
bad = []


def check(label, got, want):
    global ok
    if got == want:
        ok += 1
        print("  [OK] %s = %r" % (label, got))
    else:
        bad.append(label)
        print("  [!!] %s = %r  （期望 %r）" % (label, got, want))


def has(label, text, needle):
    """子串断言：文案不适合用相等比较（标点一改就误报）。"""
    global ok
    if needle in (text or ""):
        ok += 1
        print("  [OK] %s 含 %r" % (label, needle))
    else:
        bad.append(label)
        print("  [!!] %s 不含 %r，实际：%r" % (label, needle, text))


def wait_job(client, timeout=90.0):
    """等最近一条任务跑完（worker 是独立线程，得轮询）。返回终态任务或 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get("/api/jobs", params={"limit": 5}).json()
        items = data.get("items") or []
        if items and items[0]["status"] not in ("queued", "running"):
            return items[0]
        time.sleep(0.2)
    return None


def scan_inbox(client):
    wps = client.get("/api/watchpoints").json()
    assert wps, "监控目录没建起来"
    return client.post("/api/watchpoints/%s/scan" % wps[0]["id"]).json()


with TestClient(app) as client:
    print("== 0. 造数据 ==")
    r = client.post("/api/watchpoints", json={
        "path": str(INBOX), "recursive": False, "scanMode": "manual",
        "scanIntervalHours": 6, "scanTime": "03:00", "note": "回归"})
    check("POST /api/watchpoints 状态码", r.status_code, 200)

    print("== 1. 设置里不该再出现「切割模式」==")
    split = client.get("/api/settings").json()["split"]
    check("split 里没有 mode", "mode" in split, False)
    r = client.put("/api/settings", json={"split": {"mode": "bytes"}})
    check("PUT 带 mode 不会被拒（多余键忽略）", r.status_code, 200)
    check("返回值里仍然没有 mode", "mode" in r.json()["split"], False)

    print("== 2. 支持格式收窄到能无损切分的 8 种 ==")
    check("引擎 SUPPORTED_EXTS 个数", len(engine.SUPPORTED_EXTS), 8)
    check("DEFAULT_EXTS 与之一致", engine.DEFAULT_EXTS, engine.SUPPORTED_EXTS)
    has("含 .mp4", ",".join(engine.SUPPORTED_EXTS), ".mp4")
    check(".avi 不在其中", ".avi" in engine.SUPPORTED_EXTS, False)
    check(".rmvb 不在其中", ".rmvb" in engine.SUPPORTED_EXTS, False)

    r = client.put("/api/settings",
                   json={"split": {"ext": [".avi", ".MP4", ".wmv", ".ts"]}})
    check("PUT 的状态码", r.status_code, 200)
    check("不支持的后缀被剔除、合法的保留", r.json()["split"]["ext"], [".mp4", ".ts"])

    print("== 3. 老配置文件（带 mode、avi 与已废弃的白名单）自动清理 ==")
    # 用 json.dumps 而不是手拼字符串：Windows 路径里的反斜杠会把 JSON 转义搞坏
    config.SETTINGS_PATH.write_text(json.dumps({
        "split": {"mode": "bytes", "ext": [".avi", ".mp4", ".mkv"]},
        "watch": {"allowedRoots": [str(INBOX)]},
    }, ensure_ascii=False), encoding="utf-8")
    loaded = config.load_settings()
    check("旧 mode 被丢掉", "mode" in loaded["split"], False)
    check("旧后缀被过滤", loaded["split"]["ext"], [".mp4", ".mkv"])
    check("已废弃的 allowedRoots 被清掉",
          "allowedRoots" in loaded["watch"], False)

    # 恢复成后续测试要用的配置
    config.save_settings({
        "split": {"size": "1K", "recursive": True, "markSource": "rename"},
        "watch": {"settleSeconds": 0, "realtime": False, "pollInterval": 3600},
    })

    print("== 4. 扫描只认支持格式：avi 根本不该被看到 ==")
    res = scan_inbox(client)
    check("found（只算 mp4）", res["found"], 1)
    check("入队数", res["queued"], 1)
    jobs = client.get("/api/jobs", params={"limit": 20}).json()["items"]
    check("任务数", len(jobs), 1)
    has("任务就是那个 mp4", jobs[0]["src"], "IMG_0001.mp4")
    check("avi 没有产生任务", any(j["src"].endswith(".avi") for j in jobs), False)

    print("== 5. 切不动 -> 保留原片 + 记入失败清单 ==")
    job = wait_job(client)
    check("任务跑完了", job is not None, True)
    check("任务状态", (job or {}).get("status"), "failed")
    check("任务里记了失败原因", bool((job or {}).get("error")), True)
    check("源文件还在原地", BAD_MP4.is_file(), True)
    check("源文件没被改名成 #origin",
          (INBOX / "IMG_0001#origin.mp4").exists(), False)
    check("没有产生任何切片",
          sorted(p.name for p in INBOX.glob("*#*")), [])

    fails = client.get("/api/failures").json()
    check("失败清单条数", fails["total"], 1)
    check("清单里的路径", fails["items"][0]["path"], str(BAD_MP4))
    check("清单里带了任务 id", fails["items"][0]["jobId"], job["id"] if job else None)
    check("清单里带了失败原因", bool(fails["items"][0]["reason"]), True)

    print("== 6. 失败过的文件不再反复重试 ==")
    spec = scanner.build_spec(config.load_settings())
    status, why = scanner.consider_file(
        BAD_MP4, spec, trigger="manual", watchpoint_id=None, roots=[INBOX])
    check("手动扫描也跳过", status, "skipped")
    has("跳过原因指向失败清单", why, "处理失败")

    before = client.get("/api/jobs", params={"limit": 20}).json()["total"]
    res = scan_inbox(client)
    check("再扫一次：入队 0 个", res["queued"], 0)
    check("任务总数没变",
          client.get("/api/jobs", params={"limit": 20}).json()["total"], before)

    print("== 7. 重试：忘掉记录并重新入队 ==")
    r = client.post("/api/failures/retry", json={"path": str(BAD_MP4)})
    check("POST /api/failures/retry 状态码", r.status_code, 200)
    check("已重新入队", bool(r.json().get("job")), True)
    job2 = wait_job(client)
    check("这一次也失败了（文件本身有问题）", (job2 or {}).get("status"), "failed")
    check("失败记录回到 1 条", client.get("/api/failures").json()["total"], 1)
    check("源文件依然完好", BAD_MP4.is_file(), True)

    print("== 8. 文件被替换 -> 自动重新尝试，不必人工清记录 ==")
    time.sleep(1.1)                       # 让 mtime 明确变化
    BAD_MP4.write_bytes(b"\x00" * 12288)
    spec = scanner.build_spec(config.load_settings())
    status, why = scanner.consider_file(
        BAD_MP4, spec, trigger="manual", watchpoint_id=None, roots=[INBOX])
    check("文件变了就该重新入队", status, "queued")
    wait_job(client)

    print("== 9. 「忽略」只删记录，不动文件 ==")
    r = client.post("/api/failures/clear")
    check("POST /api/failures/clear 状态码", r.status_code, 200)
    check("清掉的条数 >= 1", r.json()["removed"] >= 1, True)
    check("清单已空", client.get("/api/failures").json()["total"], 0)
    check("源文件还在", BAD_MP4.is_file(), True)
    # 忽略不等于永久拉黑：下次扫描仍会尝试
    res = scan_inbox(client)
    check("清掉记录后会再试一次", res["queued"], 1)

    print("== 10. 清单会顺手剔除「文件已不在」的记录 ==")
    wait_job(client)
    BAD_MP4.unlink()
    check("文件已删除", BAD_MP4.exists(), False)
    check("清理后清单为空", client.get("/api/failures").json()["total"], 0)

    print("== 11. 引擎层面拒绝 bytes 模式 ==")
    try:
        engine.split_one(BAD_MP4, threshold=1024, mode="bytes")
        check("应该抛异常", False, True)
    except RuntimeError as exc:
        has("报错文案", str(exc), "纯字节切割已不再支持")
    except Exception as exc:                       # noqa: BLE001
        check("异常类型", type(exc).__name__, "RuntimeError")

print()
if bad:
    print("失败 %d 项：%s" % (len(bad), "、".join(bad)))
    sys.exit(1)
print("全部通过（%d 项）" % ok)

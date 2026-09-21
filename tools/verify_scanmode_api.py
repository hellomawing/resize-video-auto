#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scanMode 改造的接口级端到端验证（真起 FastAPI，真发 HTTP）。

核心回归点：**把目录设成「仅手动」后，点「扫描」仍然要能发现视频。**
这正是用户反馈的那个坑 —— 旧版本取消「启用」后连手动扫描都点不动了。

    python tools/verify_scanmode_api.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="vs-scanmode-api-"))
DATA = TMP / "data"
MEDIA = TMP / "media"
os.environ["VS_DATA_DIR"] = str(DATA)

from app import config                                     # noqa: E402

config.ensure_dirs()
config.save_settings({"watch": {"allowedRoots": [str(TMP)]}})

# 每个模式一个目录，各放一个小视频文件。
# 故意用小文件：阈值 3.9G 下它不会入队，所以不会真的触发 ffmpeg 切割，
# 但 scan 返回的 `found` 会如实统计「发现了几个视频」—— 这正是要验证的东西。
MODES = ["realtime", "interval", "daily", "manual"]
for mode in MODES:
    d = MEDIA / mode
    d.mkdir(parents=True)
    (d / "DJI_0001.MP4").write_bytes(b"\x00" * 4096)

from fastapi.testclient import TestClient                     # noqa: E402
from app.main import app                                      # noqa: E402

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


created = {}
expected_next = {"realtime": False, "interval": True, "daily": True, "manual": False}

with TestClient(app) as client:
    print("== 1. 按四种扫描方式各建一个监控目录 ==")
    for mode in MODES:
        body = {"path": str(MEDIA / mode), "recursive": True, "scanMode": mode,
                "scanIntervalHours": 6, "scanTime": "03:00", "note": mode}
        r = client.post("/api/watchpoints", json=body)
        check("POST %s 状态码" % mode, r.status_code, 200)
        item = r.json()
        created[mode] = item
        check("  scanMode 回读", item["scanMode"], mode)
        check("  nextScanAt 是否为空", item["nextScanAt"] is None, not expected_next[mode])
        check("  响应里没有 enabled 字段", "enabled" in item, False)

    print("== 2. 列表接口 ==")
    items = client.get("/api/watchpoints").json()
    check("目录数量", len(items), 4)
    check("列表也带 nextScanAt", all("nextScanAt" in it for it in items), True)
    print("     interval 下次扫描：%s" % created["interval"]["nextScanAt"])
    print("     daily   下次扫描：%s" % created["daily"]["nextScanAt"])

    print("== 3. 核心回归：仅手动的目录，点「扫描」必须能发现视频 ==")
    r = client.post("/api/watchpoints/%s/scan" % created["manual"]["id"])
    check("扫描接口状态码", r.status_code, 200)
    body = r.json()
    check("  发现了视频", body["found"], 1)
    print("     后端原话：%s" % body["message"])

    print("== 4. 其余三种模式也都能手动扫 ==")
    for mode in ("realtime", "interval", "daily"):
        body = client.post("/api/watchpoints/%s/scan" % created[mode]["id"]).json()
        check("  %s 手动扫描 found" % mode, body["found"], 1)

    print("== 5. 全局「立即扫描」扫全部目录（含仅手动） ==")
    body = client.post("/api/scan").json()
    check("扫到的视频总数", body["found"], 4)
    print("     后端原话：%s" % body["message"])

    print("== 6. 改扫描方式后 nextScanAt 要跟着变 ==")
    mn_id = created["manual"]["id"]
    r = client.put("/api/watchpoints/%s" % mn_id,
                   json={"scanMode": "interval", "scanIntervalHours": 2})
    check("改成每 2 小时", r.json()["scanMode"], "interval")
    check("  有了下次扫描时间", r.json()["nextScanAt"] is not None, True)
    print("     下次扫描：%s" % r.json()["nextScanAt"])

    r = client.put("/api/watchpoints/%s" % mn_id, json={"scanMode": "manual"})
    check("改回仅手动后没有下次时间", r.json()["nextScanAt"], None)

    print("== 7. 非法值由后端纠正并回读 ==")
    iv_id = created["interval"]["id"]
    r = client.put("/api/watchpoints/%s" % iv_id, json={"scanIntervalHours": 7})
    check("间隔 7 小时被纠正", r.json()["scanIntervalHours"], 6)
    r = client.put("/api/watchpoints/%s" % iv_id, json={"scanTime": "25:99"})
    check("非法时间被纠正", r.json()["scanTime"], "03:00")
    r = client.put("/api/watchpoints/%s" % iv_id, json={"scanMode": "nonsense"})
    check("非法 scanMode 被拒", r.status_code, 422)

    print("== 8. 归档检查：磁盘上的 watchpoints.json ==")
    raw = json.loads((DATA / "watchpoints.json").read_text(encoding="utf-8"))
    check("存储里没有 enabled", any("enabled" in it for it in raw), False)
    check("存储里有 scanMode", all("scanMode" in it for it in raw), True)

print("\n通过 %d 项，失败 %d 项" % (ok, len(bad)))
if bad:
    print("失败：%s" % "、".join(bad))
sys.exit(1 if bad else 0)

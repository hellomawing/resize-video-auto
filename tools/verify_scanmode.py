#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scanMode 改造的逻辑验证：迁移、cron 翻译、非法值纠正、定时计划注册。

跑法（用同一套依赖）：
    python tools/verify_scanmode.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="vs-scanmode-")
os.environ["VS_DATA_DIR"] = TMP

from app import config                                        # noqa: E402
from app.services import scheduler                            # noqa: E402

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


print("== 1. 老数据迁移（只有一个 enabled 的旧 watchpoints.json） ==")
legacy = [
    {"id": "wp_a", "path": "/vol1/a", "recursive": True, "enabled": True,
     "note": "", "createdAt": "", "lastScanAt": None, "videoCount": 0},
    {"id": "wp_b", "path": "/vol1/b", "recursive": False, "enabled": False,
     "note": "", "createdAt": "", "lastScanAt": None, "videoCount": 0},
]
Path(TMP, "watchpoints.json").write_text(json.dumps(legacy), encoding="utf-8")
wps = config.load_watchpoints()
check("enabled=true  -> scanMode", wps[0]["scanMode"], "realtime")
check("enabled=false -> scanMode", wps[1]["scanMode"], "manual")
check("存储里不再留 enabled", "enabled" in wps[0], False)
# 只读操作不该有副作用，所以上面读完之后磁盘上仍是旧的原始内容
check("load 不改磁盘（只读无副作用）", "enabled" in config._read_json(
    Path(TMP, "watchpoints.json"), [{}])[0], True)
# 迁移落到磁盘发生在下一次写入时——任何一次 PUT/扫描回写都会顺手清掉老字段
config.save_watchpoints(wps)
check("写一次之后磁盘也干净了", "enabled" in config._read_json(
    Path(TMP, "watchpoints.json"), [{}])[0], False)

print("== 2. scanMode -> cron 翻译 ==")
check("每 6 小时", config.scan_cron({"scanMode": "interval", "scanIntervalHours": 6}), "0 */6 * * *")
check("每 1 小时", config.scan_cron({"scanMode": "interval", "scanIntervalHours": 1}), "0 */1 * * *")
check("每天 03:00", config.scan_cron({"scanMode": "daily", "scanTime": "03:00"}), "0 3 * * *")
check("每天 23:45（非整点）", config.scan_cron({"scanMode": "daily", "scanTime": "23:45"}), "45 23 * * *")
check("实时监听不排期", config.scan_cron({"scanMode": "realtime"}), None)
check("仅手动不排期", config.scan_cron({"scanMode": "manual"}), None)

print("== 3. 非法值纠正 ==")
check("间隔 7 小时 -> 就近档位", config.normalize_watchpoint(
    {"scanMode": "interval", "scanIntervalHours": 7})["scanIntervalHours"], 6)
check("间隔 999 -> 24", config.normalize_watchpoint(
    {"scanMode": "interval", "scanIntervalHours": 999})["scanIntervalHours"], 24)
check("间隔写成了字符串", config.normalize_watchpoint(
    {"scanMode": "interval", "scanIntervalHours": "4"})["scanIntervalHours"], 4)
check("时间 25:00 -> 默认", config.normalize_watchpoint(
    {"scanMode": "daily", "scanTime": "25:00"})["scanTime"], "03:00")
check("时间 9:5 -> 补零", config.normalize_watchpoint(
    {"scanMode": "daily", "scanTime": "9:5"})["scanTime"], "09:05")
check("未知 scanMode -> realtime", config.normalize_watchpoint(
    {"scanMode": "whatever"})["scanMode"], "realtime")

print("== 4. cron 中文描述（前端展示用） ==")
check("每 6 小时", scheduler.describe_cron("0 */6 * * *"), "每 6 小时（第 0 分）")
check("每天 03:00", scheduler.describe_cron("0 3 * * *"), "每天 03:00")

print("== 5. 定时计划真的注册进 APScheduler ==")
config.save_watchpoints([
    {"id": "wp_rt", "path": "/vol1/rt", "scanMode": "realtime",
     "recursive": True, "note": "", "createdAt": "", "lastScanAt": None, "videoCount": 0},
    {"id": "wp_iv", "path": "/vol1/iv", "scanMode": "interval",
     "scanIntervalHours": 6, "recursive": True, "note": "", "createdAt": "",
     "lastScanAt": None, "videoCount": 0},
    {"id": "wp_dy", "path": "/vol1/dy", "scanMode": "daily", "scanTime": "03:00",
     "recursive": True, "note": "", "createdAt": "", "lastScanAt": None, "videoCount": 0},
    {"id": "wp_mn", "path": "/vol1/mn", "scanMode": "manual",
     "recursive": True, "note": "", "createdAt": "", "lastScanAt": None, "videoCount": 0},
])
scheduler.start_scheduler()
jobs = sorted(j.id for j in scheduler._scheduler.get_jobs())
check("注册的 job", jobs, ["watchpoint:wp_dy", "watchpoint:wp_iv"])
check("实时目录没有下次时间", scheduler.next_scan_time("wp_rt"), None)
check("仅手动没有下次时间", scheduler.next_scan_time("wp_mn"), None)
check("每隔 6 小时有下次时间", bool(scheduler.next_scan_time("wp_iv")), True)
check("每天定时有下次时间", bool(scheduler.next_scan_time("wp_dy")), True)
print("     wp_iv 下次：%s" % scheduler.next_scan_time("wp_iv"))
print("     wp_dy 下次：%s" % scheduler.next_scan_time("wp_dy"))

print("== 6. 改扫描方式后计划要跟着变 ==")
items = config.load_watchpoints()
for it in items:
    if it["id"] == "wp_iv":
        it["scanMode"] = "manual"
    if it["id"] == "wp_mn":
        it["scanMode"] = "daily"
        it["scanTime"] = "12:30"
config.save_watchpoints(items)
scheduler.reload_watchpoint_jobs()
jobs = sorted(j.id for j in scheduler._scheduler.get_jobs())
check("重新装配后的 job", jobs, ["watchpoint:wp_dy", "watchpoint:wp_mn"])
check("原 interval 目录已撤销", scheduler.next_scan_time("wp_iv"), None)
check("新改的 daily 目录已排上", bool(scheduler.next_scan_time("wp_mn")), True)

print("== 7. 定时任务与目录扫描互不干扰 ==")
config.save_schedules([{"id": "sc_x", "name": "每周一全量", "cron": "0 9 * * 1",
                        "enabled": True, "watchpointIds": [],
                        "lastRunAt": None}])
scheduler.reload_schedules()
jobs = sorted(j.id for j in scheduler._scheduler.get_jobs())
check("两类 job 并存", jobs, ["schedule:sc_x", "watchpoint:wp_dy", "watchpoint:wp_mn"])
scheduler.reload_watchpoint_jobs()
jobs = sorted(j.id for j in scheduler._scheduler.get_jobs())
check("重排目录计划不会误删定时任务", jobs,
      ["schedule:sc_x", "watchpoint:wp_dy", "watchpoint:wp_mn"])
scheduler.stop_scheduler()

print("\n通过 %d 项，失败 %d 项" % (ok, len(bad)))
if bad:
    print("失败：%s" % "、".join(bad))
sys.exit(1 if bad else 0)

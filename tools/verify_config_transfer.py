#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置导入导出（GET /api/settings/export、POST /api/settings/import）的回归验证。

背景 —— 为什么要有这两个接口：
程序的状态（设置 / 监控目录 / 归档目录记录）存在 Docker 卷里，用户看不见也
不该操心。但「换台机器、重装一次，配置要重新配一遍」这件事没人能接受，
所以补一条不依赖卷路径的备份通道。

三块内容的合并语义刻意不同，本脚本逐条验证：
  1. 导出含三块内容；任务历史不在里面（那是运行数据，不是配置）
  2. 设置：整体替换（用户导的就是一台机器的完整设置）
  3. 监控目录：按**路径**合并，不按 id —— 换机器后 id 必然对不上，
     按 id 判重会把整份重复添加一遍；本机侧的 id 与运行时字段要保住
  4. 白名单外的路径被跳过并计数，不让整份导入失败
  5. 归档目录记录**只增不减**：少记一个名字就可能把归档原片重切一遍

    python tools/verify_config_transfer.py
"""

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="vs-transfer-"))
os.environ["VS_DATA_DIR"] = str(TMP / "data")

from app import config, db                                    # noqa: E402

config.ensure_dirs()
db.init_db()

MEDIA = TMP / "media"
INBOX = MEDIA / "inbox"
NEWDIR = MEDIA / "newdir"
OUTSIDE = TMP / "outside"          # 没挂载进来的目录
for d in (INBOX, NEWDIR, OUTSIDE):
    d.mkdir(parents=True)

# 可访问范围只由容器挂载决定（没有白名单设置了）。本机没有 /proc，
# 用环境变量代替「挂载了哪些目录」——第 4 节再把 OUTSIDE 摘掉，模拟换台机器挂载不同。
os.environ["VS_EXTRA_ROOTS"] = os.pathsep.join([str(MEDIA), str(OUTSIDE)])

# save_settings 是「基于默认值的整体替换」而不是补丁 —— 漏写的键会被打回默认值
config.save_settings({"split": {"outdir": ""}})

from fastapi.testclient import TestClient                     # noqa: E402
from app.main import app                                      # noqa: E402

client = TestClient(app)

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
    check(label, needle in (text or ""), True)


print("== 1. 造一个监控目录，导出一份配置 ==")
res = client.post("/api/watchpoints",
                  json={"path": str(INBOX), "scanMode": "manual"})
check("建目录成功", res.status_code, 200)
wp_id = res.json()["id"]
origin_size = client.get("/api/settings").json()["split"]["size"]

bundle = client.get("/api/settings/export").json()
check("导出 200", client.get("/api/settings/export").status_code, 200)
check("version", bundle["version"], 1)
is_true = isinstance(bundle["settings"], dict) and bool(bundle["settings"])
check("settings 非空", is_true, True)
check("监控目录条数", len(bundle["watchpoints"]), 1)
check("归档记录是列表", isinstance(bundle["archiveDirs"], list), True)
# 任务历史不在导出里：它是运行数据，换机器不该搬
check("不含任务历史", "jobs" in bundle, False)

print("== 2. 设置：整体替换 ==")
s = client.get("/api/settings").json()
s["split"]["size"] = "999M"
client.put("/api/settings", json=s)
check("改动已生效", client.get("/api/settings").json()["split"]["size"], "999M")
r = client.post("/api/settings/import", json=bundle).json()
check("设置被替换", r["settingsApplied"], True)
check("阈值已还原",
      client.get("/api/settings").json()["split"]["size"], origin_size)

print("== 3. 监控目录：按路径合并，不是按 id ==")
b2 = copy.deepcopy(bundle)
b2["watchpoints"] = [
    dict(bundle["watchpoints"][0], scanMode="daily", scanTime="03:00"),
    {"path": str(NEWDIR), "scanMode": "manual"},
]
r2 = client.post("/api/settings/import", json=b2).json()
check("新增 1", r2["watchpointsAdded"], 1)
check("更新 1", r2["watchpointsUpdated"], 1)
check("跳过 0", r2["watchpointsSkipped"], 0)

wps = client.get("/api/watchpoints").json()
check("总数 2", len(wps), 2)
check("原目录 id 未被顶掉", wps[0]["id"], wp_id)
check("原目录已按导入内容更新", wps[0]["scanMode"], "daily")
check("新目录用的是导入的路径", wps[1]["path"], str(NEWDIR))
# 用不同 id 导入同一路径，不能变成两条（换机器后 id 一定不同）
b3 = copy.deepcopy(bundle)
b3["watchpoints"] = [{"id": "wp_zzzzzzzz", "path": str(NEWDIR), "scanMode": "realtime"}]
r3 = client.post("/api/settings/import", json=b3).json()
check("换 id 仍算同一条", r3["watchpointsUpdated"], 1)
check("总数没变", len(client.get("/api/watchpoints").json()), 2)

print("== 4. 没挂载的路径跳过，不拖垮整份导入 ==")
# 模拟「换台机器后这个目录没挂进来」：把它从可访问范围里摘掉
os.environ["VS_EXTRA_ROOTS"] = str(MEDIA)
b4 = {"version": 1, "settings": {},
      "watchpoints": [{"path": str(OUTSIDE), "scanMode": "manual"}],
      "archiveDirs": []}
r4 = client.post("/api/settings/import", json=b4).json()
check("跳过 1", r4["watchpointsSkipped"], 1)
check("总数仍是 2", len(client.get("/api/watchpoints").json()), 2)
# 顺带：settings 为空时不该把现有设置冲掉
check("空 settings 不动设置", r4["settingsApplied"], False)

print("== 5. 归档目录记录只增不减 ==")
config.remember_archive_dirs(["origin"])
b5 = {"version": 1, "settings": {}, "watchpoints": [], "archiveDirs": ["travel2024"]}
r5 = client.post("/api/settings/import", json=b5).json()
check("新增 1", r5["archiveDirsAdded"], 1)
b6 = {"version": 1, "settings": {}, "watchpoints": [], "archiveDirs": []}
r6 = client.post("/api/settings/import", json=b6).json()
check("空列表不加不减", r6["archiveDirsAdded"], 0)
known = config.load_known_archive_dirs()
check("旧名字还在", "origin" in known, True)
check("导入的名字也还在", "travel2024" in known, True)

print("== 6. 导出/导入往返：导出的文件能原样喂回去 ==")
round_trip = client.get("/api/settings/export").json()
r7 = client.post("/api/settings/import", json=round_trip).json()
check("往返无新增无跳过", (r7["watchpointsAdded"], r7["watchpointsSkipped"]), (0, 0))
check("往返更新 2 条", r7["watchpointsUpdated"], 2)
check("归档记录无变化", r7["archiveDirsAdded"], 0)
check("JSON 可序列化", isinstance(json.dumps(round_trip), str), True)

print()

if bad:
    print("失败 %d 项 / 通过 %d 项：" % (len(bad), ok))
    for b in bad:
        print("  - %s" % b)
    sys.exit(1)
print("全部通过（%d 项）" % ok)

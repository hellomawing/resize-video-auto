#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设置「字段级更新」的接口级验证（真起 FastAPI，真发 HTTP）。

核心回归点：**PATCH 只能改传进来的字段。**

设置页改成「开关一拨就落盘」之后，自动保存走的是 PATCH。如果它的合并基准
写错成 DEFAULT_SETTINGS（也就是图省事直接复用 save_settings），那么拨一下
「实时监听」就会把切分参数、服务参数整块打回默认值 —— 用户的自定义设置会
不明不白地消失。这条最要命，所以放在最前面反复验。

    python tools/verify_settings_patch.py
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="vs-settings-patch-"))
os.environ["VS_DATA_DIR"] = str(TMP / "data")
# 可访问范围只由容器挂载决定，本机没有 /proc，用环境变量代替
os.environ["VS_EXTRA_ROOTS"] = str(TMP)

from app import config                                     # noqa: E402
from app.services.events import bus                        # noqa: E402

config.ensure_dirs()

from fastapi.testclient import TestClient                  # noqa: E402
from app.main import app                                   # noqa: E402

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


# 记下广播事件，验证「发起来源」被原样透传（前端据此忽略自己触发的通知）
events = []
bus.publish = lambda event: events.append(dict(event))     # type: ignore[assignment]

client = TestClient(app)

# ---------------------------------------------------------------- 造一组非默认设置

print("先把设置改成一组非默认值，后面所有「不变」断言都以它为基准")
CUSTOM = {
    "split": {
        "bySize": False, "size": "7.7G", "seconds": 123, "ext": [".mkv"],
        "recursive": False, "outdirMode": "custom", "outdir": "/tmp/split-out",
        "markSource": "move", "sourceDir": "archived-here",
        "keepMetadata": False, "overwrite": True,
    },
    "watch": {
        "realtime": False, "pollInterval": 99, "settleSeconds": 7,
        "minSize": "50M", "ignoreSuffixes": [".tmp"],
    },
    "server": {"host": "127.0.0.1", "port": 9999, "jobLogLines": 500},
}
r = client.put("/api/settings", json=CUSTOM)
check("PUT 造数据的状态码", r.status_code, 200)
base = r.json()
check("基准 split.size", base["split"]["size"], "7.7G")
check("基准 server.port", base["server"]["port"], 9999)

# ---------------------------------------------------------------- 核心：只改传入的字段

print()
print("PATCH 只改传进来的字段，同组与跨组都不许动")
r = client.patch("/api/settings", json={"watch": {"realtime": True}})
check("PATCH 状态码", r.status_code, 200)
got = r.json()
check("改动的字段生效 realtime", got["watch"]["realtime"], True)
check("同组字段不动 pollInterval", got["watch"]["pollInterval"], 99)
check("同组字段不动 settleSeconds", got["watch"]["settleSeconds"], 7)
check("同组字段不动 minSize", got["watch"]["minSize"], "50M")
check("跨组不动 split.size", got["split"]["size"], "7.7G")
check("跨组不动 split.bySize", got["split"]["bySize"], False)
check("跨组不动 split.seconds", got["split"]["seconds"], 123)
check("跨组不动 split.outdir", got["split"]["outdir"], "/tmp/split-out")
check("跨组不动 split.markSource", got["split"]["markSource"], "move")
check("跨组不动 split.overwrite", got["split"]["overwrite"], True)
check("跨组不动 server.port", got["server"]["port"], 9999)
check("跨组不动 server.jobLogLines", got["server"]["jobLogLines"], 500)

print()
print("PATCH 的结果要真的落到磁盘上（不只是响应体好看）")
disk = client.get("/api/settings").json()
check("已落盘 realtime", disk["watch"]["realtime"], True)
check("已落盘 split.seconds", disk["split"]["seconds"], 123)
check("已落盘 server.port", disk["server"]["port"], 9999)

print()
print("一次带多个字段的 PATCH（分栏底部保存按钮走的就是这条）")
r = client.patch("/api/settings", json={
    "watch": {"pollInterval": 45, "settleSeconds": 8, "minSize": "80M"},
})
got = r.json()
check("三项都改了 pollInterval", got["watch"]["pollInterval"], 45)
check("三项都改了 settleSeconds", got["watch"]["settleSeconds"], 8)
check("三项都改了 minSize", got["watch"]["minSize"], "80M")
check("没传的 realtime 保持", got["watch"]["realtime"], True)
check("没传的 ignoreSuffixes 保持", got["watch"]["ignoreSuffixes"], [".tmp"])
check("跨组仍不动 split.size", got["split"]["size"], "7.7G")

# ---------------------------------------------------------------- 派生字段

print()
print("bySize 决定派生字段 all：PATCH 它时必须同步算出 all")
r = client.patch("/api/settings", json={"split": {"bySize": True}})
check("bySize=True → all 派生为", r.json()["split"]["all"], False)
check("bySize=True 时 seconds 仍在", r.json()["split"]["seconds"], 123)
r = client.patch("/api/settings", json={"split": {"bySize": False}})
check("bySize=False → all 派生为", r.json()["split"]["all"], True)

# ---------------------------------------------------------------- 不该动的一律别动

print()
print("空 body、空对象、显式 null，都不许改动任何东西")
before = client.get("/api/settings").json()
r = client.patch("/api/settings", json={})
check("空 PATCH 状态码", r.status_code, 200)
check("空 PATCH 不改动", r.json(), before)
r = client.patch("/api/settings", json={"split": {}})
check("空 split PATCH 不改动", r.json(), before)
# 显式 null：不能让它把整块结构替换成 None，否则规范化逻辑取字段直接崩
r = client.patch("/api/settings", json={"split": None})
check("split=null 状态码", r.status_code, 200)
check("split=null 不改动", r.json(), before)
r = client.patch("/api/settings", json={"split": {"size": None}})
check("字段=null 状态码", r.status_code, 200)
check("字段=null 不改动", r.json()["split"]["size"], before["split"]["size"])
r = client.patch("/api/settings", json={"watch": {"minSize": None}})
check("minSize=null 不改动", r.json()["watch"]["minSize"], before["watch"]["minSize"])

print()
print("不认识的字段直接忽略，不要 422 也不要写进去")
r = client.patch("/api/settings", json={"split": {"nope": 1}, "unknown": {"a": 1}})
check("含未知键的状态码", r.status_code, 200)
check("含未知键没有改动", r.json(), before)
# all 是派生字段，不给外部直写
r = client.patch("/api/settings", json={"split": {"all": True, "bySize": True}})
check("all 不可直写（被忽略，由 bySize 派生）", r.json()["split"]["all"], False)

# ---------------------------------------------------------------- 非法值

print()
print("非法值要被规范化，而不是 500 或者原样存进去")
r = client.patch("/api/settings", json={"split": {"size": "   "}})
check("空白 size 状态码", r.status_code, 200)
check("空白 size 回落到默认",
      r.json()["split"]["size"], config.DEFAULT_SETTINGS["split"]["size"])
r = client.patch("/api/settings", json={"watch": {"pollInterval": 999999}})
check("pollInterval 超上限被夹住", r.json()["watch"]["pollInterval"], 86400)
r = client.patch("/api/settings", json={"watch": {"pollInterval": 1}})
check("pollInterval 低于下限被夹住", r.json()["watch"]["pollInterval"], 5)
r = client.patch("/api/settings", json={"server": {"jobLogLines": 1}})
check("jobLogLines 低于下限被夹住", r.json()["server"]["jobLogLines"], 100)
r = client.patch("/api/settings", json={"split": {"ext": [".avi"]}})
check("只填不支持的格式 → 回落到全量",
      r.json()["split"]["ext"], list(config.engine.SUPPORTED_EXTS))
# 归档目录名必须是单层目录名（它会被拼进文件路径）。多层只取最后一段，
# 指回自身/上级的、以及历史默认名 origin，都退回默认值。
r = client.patch("/api/settings", json={"split": {"sourceDir": "../坏名字"}})
check("归档目录名只取最后一段", r.json()["split"]["sourceDir"], "坏名字")
r = client.patch("/api/settings", json={"split": {"sourceDir": ".."}})
check("归档目录名是指回上级 → 回默认",
      r.json()["split"]["sourceDir"], config.DEFAULT_SOURCE_DIR)
r = client.patch("/api/settings", json={"split": {"sourceDir": "origin"}})
check("历史默认名 origin → 回默认",
      r.json()["split"]["sourceDir"], config.DEFAULT_SOURCE_DIR)
r = client.patch("/api/settings", json={"split": {"sourceDir": "  "}})
check("归档目录名为空 → 回默认",
      r.json()["split"]["sourceDir"], config.DEFAULT_SOURCE_DIR)

# ---------------------------------------------------------------- 与 PUT 的语义对照

print()
print("PUT 仍是整体替换 —— 这条对照用来证明两者确实不是一回事")
r = client.put("/api/settings", json={"split": {"size": "1.1G"}})
got = r.json()
check("PUT 传入的字段生效", got["split"]["size"], "1.1G")
check("PUT 没传的字段回默认 seconds", got["split"]["seconds"], 300.0)
check("PUT 没传的字段回默认 server.port", got["server"]["port"], 8099)
check("PUT 没传的字段回默认 watch.pollInterval", got["watch"]["pollInterval"], 30)

# ---------------------------------------------------------------- 广播带来源

print()
print("广播要带发起来源，发起端才能忽略自己触发的那条")
events.clear()
client.patch("/api/settings", json={"watch": {"realtime": True}},
             params={"source": "web-abc123"})
check("广播类型", events[-1].get("type"), "settings.updated")
check("广播带 source", events[-1].get("source"), "web-abc123")
events.clear()
client.patch("/api/settings", json={"watch": {"realtime": True}})
check("不带 source 时不塞该字段", "source" in events[-1], False)

# ---------------------------------------------------------------- 落盘后的收尾动作

print()
print("PATCH 也要让改动立刻生效（重装监控 + 重排扫描计划），不能只写文件")
import app.api.settings as settings_api                    # noqa: E402
from app.services import scheduler                         # noqa: E402
from app.services.monitor import monitor as real_monitor    # noqa: E402


class FakeMonitor:
    def __init__(self):
        self.calls = 0

    def reload(self):
        self.calls += 1


fake_monitor = FakeMonitor()
settings_api.monitor_service = fake_monitor
jobs_calls = []
scheduler.reload_watchpoint_jobs = lambda: jobs_calls.append(1)   # type: ignore[assignment]

client.patch("/api/settings", json={"watch": {"realtime": False}})
check("PATCH 后监控服务被重装", fake_monitor.calls, 1)
check("PATCH 后扫描计划被重排", len(jobs_calls), 1)

settings_api.monitor_service = real_monitor

# ---------------------------------------------------------------- 汇总

print()
if bad:
    print("失败 %d 项：%s" % (len(bad), "、".join(bad)))
    sys.exit(1)
print("全部通过：%d 项" % ok)

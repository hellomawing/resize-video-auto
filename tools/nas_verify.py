"""在真实 NAS 上跑一遍端到端验证。

用法：
    python tools/nas_verify.py [base_url]

做的事：
    1. 健康检查
    2. 把分割阈值调小、缩短稳定判定时间（不然 14MB 的测试片不会触发分割）
    3. 建监控目录指向 /vol1/1000/video-split-in
    4. 触发扫描 → 等任务跑完
    5. 打印任务日志与产出文件
    6. 试一次撤销预览（不实际执行）
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("NAS_BASE_URL", "")
TEST_DIR = os.environ.get("NAS_TEST_DIR", "/vol1/media/inbox")
if not BASE:
    print("用法：NAS_BASE_URL=http://NAS_IP:8099 python tools/nas_verify.py")
    sys.exit(2)


def call(method, path, body=None, timeout=60):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def step(title):
    print()
    print("=" * 62)
    print(title)
    print("=" * 62)


ok_all = True


def check(label, cond, extra=""):
    global ok_all
    mark = "OK  " if cond else "FAIL"
    if not cond:
        ok_all = False
    print("[%s] %s%s" % (mark, label, ("  " + extra) if extra else ""))
    return cond


# ---------------------------------------------------------------- 健康检查
step("1. 健康检查")
st, health = call("GET", "/api/health", timeout=20)
check("/api/health 返回 200", st == 200, "实际 %s" % st)
print(json.dumps(health, ensure_ascii=False, indent=2)[:600])
if st != 200:
    sys.exit(1)
check("ffmpeg 可用", bool(health.get("ffmpeg", {}).get("ok")), str(health.get("ffmpeg")))
check("ffprobe 可用", bool(health.get("ffprobe", {}).get("ok")), str(health.get("ffprobe")))

# ---------------------------------------------------------------- 调小阈值
step("2. 调整设置（把阈值调小以便真的触发分割）")
st, settings = call("GET", "/api/settings")
check("读取设置", st == 200)
settings["split"]["size"] = "4M"
settings["split"]["bySize"] = True
settings["split"]["all"] = False
settings["split"]["markSource"] = "rename"
settings["watch"]["settleSeconds"] = 8
settings["watch"]["realtime"] = True
settings["watch"]["minSize"] = "0"
st, saved = call("PUT", "/api/settings", settings)
check("保存设置", st == 200, "实际 %s" % st)
print("  split.size = %s    markSource = %s" % (saved["split"]["size"], saved["split"]["markSource"]))
print("  settleSeconds = %s  realtime = %s" % (saved["watch"]["settleSeconds"], saved["watch"]["realtime"]))

# ---------------------------------------------------------------- 监控目录
step("3. 添加监控目录")
st, wps = call("GET", "/api/watchpoints")
existing = [w for w in (wps or []) if w.get("path") == TEST_DIR]
if existing:
    wp = existing[0]
    print("  已存在：%s" % wp["id"])
else:
    st, wp = call("POST", "/api/watchpoints",
                  {"path": TEST_DIR, "recursive": False, "note": "NAS 部署验证"})
    check("创建监控目录", st == 200, "实际 %s / %s" % (st, wp))
print(json.dumps(wp, ensure_ascii=False, indent=2))

# ---------------------------------------------------------------- 扫描 + 入队
step("4. 触发扫描")
st, scan = call("POST", "/api/scan", timeout=120)
check("扫描接口返回 200", st == 200, str(scan))
print("  %s" % scan)

# ---------------------------------------------------------------- 等任务
step("5. 等待任务完成")
deadline = time.time() + 180
job = None
while time.time() < deadline:
    st, data = call("GET", "/api/jobs?limit=5")
    items = (data or {}).get("items", [])
    if items:
        job = items[0]
        print("  %-8s phase=%-9s progress=%s parts=%s/%s  %s"
              % (job["status"], job.get("phase"), job.get("progress"),
                 job.get("partsDone"), job.get("partsTotal"), job.get("message") or ""))
        if job["status"] in ("success", "failed", "canceled", "skipped"):
            break
    else:
        print("  (还没有任务)")
    time.sleep(5)

if job is None:
    check("产生了任务", False)
else:
    check("任务成功", job["status"] == "success",
          "status=%s error=%s" % (job["status"], job.get("error")))
    print()
    print(json.dumps(job, ensure_ascii=False, indent=2)[:1500])

    step("6. 任务日志")
    if job:
        st, log = call("GET", "/api/jobs/%s/log" % job["id"])
        for line in (log or {}).get("lines", []):
            print("  " + line)

# ---------------------------------------------------------------- 撤销预览
step("7. 撤销预览（只看不动）")
st, prev = call("POST", "/api/undo/preview", {"path": TEST_DIR, "recursive": False}, timeout=120)
check("撤销预览返回 200", st == 200, "实际 %s" % st)
if isinstance(prev, dict):
    print("  okCount=%s badCount=%s orphans=%s"
          % (prev.get("okCount"), prev.get("badCount"), prev.get("orphans")))
    for g in prev.get("groups", []):
        print("  - %s%s  原片 %s  切片合计 %s  可撤销=%s"
              % (g.get("base"), g.get("suffix"), g.get("originSize"),
                 g.get("sliceSum"), g.get("ok")))
        print("    %s" % g.get("reason"))

print()
print("=" * 62)
print("总结：%s" % ("全部通过" if ok_all else "有失败项，见上面 FAIL"))
print("=" * 62)
sys.exit(0 if ok_all else 1)

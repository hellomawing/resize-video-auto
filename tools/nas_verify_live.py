"""验证「运行中上传 → 实时监控自动接手 → 流拷贝模式」这条链路。

前面那轮用 4M 阈值验证时，阈值被压得太紧（关键帧对齐后单片必然超过），
引擎按设计回退成了纯字节切割。这里换成宽松阈值，走真实的流拷贝路径，
并且特意在容器运行期间才把文件放进去，确认是实时监听抓到的，
而不是靠手动扫描。
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
    print("用法：NAS_BASE_URL=http://NAS_IP:8099 python tools/nas_verify_live.py")
    sys.exit(2)


def call(method, path, body=None, timeout=90):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
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


print("1) 把阈值放宽到 6M，让关键帧对齐有余量（不然每段都会超阈值、反复重切）")
st, s = call("GET", "/api/settings")
s["split"]["size"] = "6M"
s["watch"]["settleSeconds"] = 8
st, s = call("PUT", "/api/settings", s)
print("   size=%s settle=%s -> HTTP %s" % (s["split"]["size"], s["watch"]["settleSeconds"], st))

print()
print("2) 记下当前任务数，然后往监控目录里放文件（不调扫描接口）")
st, before = call("GET", "/api/jobs?limit=50")
n_before = (before or {}).get("total", 0)
print("   放入前任务数：%s" % n_before)

print()
print("3) 等待实时监控自动发现（settleSeconds=8，给足余量轮询 90 秒）")
deadline = time.time() + 90
job = None
while time.time() < deadline:
    st, data = call("GET", "/api/jobs?limit=5")
    items = (data or {}).get("items", [])
    if items and (data or {}).get("total", 0) > n_before:
        job = items[0]
        print("   已自动入队：trigger=%s status=%s" % (job.get("trigger"), job.get("status")))
        break
    time.sleep(4)

if not job:
    print("   [FAIL] 90 秒内没等到实时监控接手")
    sys.exit(1)

print()
print("4) 等任务跑完")
deadline = time.time() + 180
while time.time() < deadline:
    st, job = call("GET", "/api/jobs/%s" % job["id"])
    print("   %-8s mode=%-6s usedMode=%-6s parts=%s/%s progress=%s"
          % (job["status"], job.get("mode"), job.get("usedMode"),
             job.get("partsDone"), job.get("partsTotal"), job.get("progress")))
    if job["status"] in ("success", "failed", "canceled", "skipped"):
        break
    time.sleep(4)

print()
print("=" * 62)
print("结果")
print("=" * 62)
print("  触发方式     : %s  （watch = 实时监控自动抓到）" % job.get("trigger"))
print("  状态         : %s" % job.get("status"))
print("  模式         : %s -> %s" % (job.get("mode"), job.get("usedMode")))
print("  段数         : %s" % job.get("partsTotal"))
print("  耗时         : %s 秒" % job.get("durationSec"))
print("  警告         : %s" % (job.get("warnings") or "无"))
print("  错误         : %s" % job.get("error"))
for p in job.get("produced") or []:
    print("    - %s  %s bytes" % (p["name"], p["size"]))

ok = (job.get("status") == "success"
      and job.get("trigger") == "watch"
      and job.get("usedMode") == "copy")
print()
print("核心断言（实时触发 + 流拷贝模式 + 成功）：%s" % ("通过" if ok else "不通过"))
sys.exit(0 if ok else 1)

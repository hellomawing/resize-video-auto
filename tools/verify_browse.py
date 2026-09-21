#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""目录选择器（/api/browse）的回归验证。

背景 —— 真机上暴露的缺陷：
fnOS 的存储池根目录 `/vol1` 权限位是 000，`getfacl` 连一条扩展 ACL 都没有，
所以内核拒绝对它 readdir，`/api/browse?path=/vol1` 直接回「没有权限读取该目录」。
但**访问它下面的目录完全正常**（/vol1/1000 能列出 11 个子目录）。
也就是说「从根往下逐级点」这条路在那种机器上第一级就是死的。

对应改了三件事，本脚本逐条验证：
  1. 白名单里不存在的根不再混进 roots（否则下拉里全是点了就报错的死入口）
  2. 新增 shortcuts（常用目录）：已添加的监控目录 / 最近任务目录 / 系统输出目录，
     点一下直达，绕开不可枚举的那一层
  3. 列不出来时的错误文案必须给出路，而不是一句「没有权限」把人堵死；
     文案也不再指引「直接输入完整路径」—— 选择器已去掉手动输入入口，
     撤销页更是直接换成「监控目录」下拉框（不走 /api/browse）

    python tools/verify_browse.py
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="vs-browse-"))
os.environ["VS_DATA_DIR"] = str(TMP / "data")

from app import config, db                                  # noqa: E402

config.ensure_dirs()

MEDIA = TMP / "media"
INBOX = MEDIA / "inbox"
OUT = MEDIA / "out"
OUT2 = MEDIA / "out2"
GHOST = TMP / "ghost"            # 配置里写了但根本不存在的根
OUTSIDE = TMP / "outside"        # 在白名单之外
for d in (INBOX, OUT, OUT2, OUTSIDE):
    d.mkdir(parents=True)
(INBOX / "sub").mkdir()

# 白名单：media 存在、ghost 不存在 —— 正好复现真机上 /vol1 与 /vol2~4 的关系。
# outside 先放进白名单（否则连监控目录都建不出来），造完数据再收窄，
# 用来验证「白名单收窄后，已失效的旧路径不再出现在快捷入口里」。
def save_roots(roots):
    """save_settings 是「基于默认值的整体替换」而不是补丁 ——
    漏写的键会被打回默认值。所以这里统一封装，免得把 outdir 冲掉。
    """
    config.save_settings({"watch": {"allowedRoots": [str(r) for r in roots]},
                          "split": {"outdir": str(OUT2)}})


save_roots([MEDIA, GHOST, OUTSIDE])

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


def has(label, text, needle):
    """子串断言：文案类的东西不适合用相等比较（会被标点微调搞挂）。"""
    global ok
    if needle in (text or ""):
        ok += 1
        print("  [OK] %s 含 %r" % (label, needle))
    else:
        bad.append(label)
        print("  [!!] %s 不含 %r，实际：%r" % (label, needle, text))


with TestClient(app) as client:
    print("== 0. 造数据：一个监控目录 + 一条历史任务 ==")
    r = client.post("/api/watchpoints", json={
        "path": str(INBOX), "recursive": False, "scanMode": "manual",
        "scanIntervalHours": 6, "scanTime": "03:00", "note": "相机导入目录"})
    check("POST /api/watchpoints 状态码", r.status_code, 200)
    # 白名单外的监控目录：不该出现在快捷入口里（点了也是 403）
    r = client.post("/api/watchpoints", json={
        "path": str(OUTSIDE), "recursive": False, "scanMode": "manual",
        "scanIntervalHours": 6, "scanTime": "03:00", "note": "白名单外"})
    check("POST 监控目录状态码（此时还在白名单内）", r.status_code, 200)

    # 收窄白名单：outside 掉出去。真机上「改了 allowedRoots」就是这个场景。
    save_roots([MEDIA, GHOST])

    check("插入历史任务", db.insert_job({
        "id": "job_verify_1", "src": str(INBOX / "DJI_0001.MP4"),
        "src_name": "DJI_0001.MP4", "src_size": 4096,
        "outdir": str(OUT), "status": "success",
        "created_at": db.now_iso()}), True)

    print("== 1. roots 只留存在的根，不存在的挪进 missingRoots ==")
    data = client.get("/api/browse").json()
    check("roots", data["roots"], [str(MEDIA)])
    check("missingRoots", data["missingRoots"], [str(GHOST)])
    check("dirs（就是存在的根）", [d["path"] for d in data["dirs"]], [str(MEDIA)])
    check("没有 error", data["error"], None)
    check("path 为空串", data["path"], "")

    print("== 2. 常用目录：三个来源、顺序、去重、白名单过滤 ==")
    items = data["shortcuts"]
    check("条目数", len(items), 3)
    check("第 1 条是监控目录", (items[0]["path"], items[0]["kind"]),
          (str(INBOX), "watchpoint"))
    check("  监控目录带 note", items[0]["note"], "相机导入目录")
    check("  名字取的是目录名", items[0]["name"], "inbox")
    check("第 2 条来自历史任务 outdir", (items[1]["path"], items[1]["kind"]),
          (str(OUT), "job"))
    check("第 3 条是系统输出目录", (items[2]["path"], items[2]["kind"]),
          (str(OUT2), "setting"))
    check("白名单外的监控目录被排除",
          [i for i in items if i["path"] == str(OUTSIDE)], [])
    # 历史任务的 src 父目录 == 监控目录，必须去重成一条而不是两条
    check("同路径已去重",
          len([i for i in items if i["path"] == str(INBOX)]), 1)

    print("== 3. 正常目录照旧能逐级浏览 ==")
    data = client.get("/api/browse", params={"path": str(MEDIA)}).json()
    check("子目录", sorted(d["path"] for d in data["dirs"]),
          sorted([str(INBOX), str(OUT), str(OUT2)]))
    check("parent", data["parent"], str(TMP))
    check("没有 error", data["error"], None)
    check("导航时也带 shortcuts", len(data["shortcuts"]), 3)

    print("== 4. 列不出来时的文案必须给出路（真机 /vol1 那种情况）==")
    with mock.patch("app.api.system.os.scandir",
                    side_effect=PermissionError(13, "Permission denied")):
        data = client.get("/api/browse", params={"path": str(INBOX)}).json()
    check("没有子目录", data["dirs"], [])
    has("error 说明只是不能枚举", data["error"], "不代表")
    has("error 提到 fnOS", data["error"], "fnOS")
    has("error 指向常用目录", data["error"], "常用目录")
    has("error 给出「加全新目录」的出路", data["error"], "可访问根目录白名单")
    # 选择器已经不再提供手动输入路径的入口，文案里就别再让人去敲了
    check("error 不再指向手动输入",
          "输入完整路径" in (data["error"] or ""), False)

    print("== 5. 路径不存在 / 越界 ==")
    data = client.get("/api/browse", params={"path": str(MEDIA / "nope")}).json()
    has("不存在时的 error", data["error"], "目录不存在")
    check("不存在时 dirs 为空", data["dirs"], [])
    r = client.get("/api/browse", params={"path": str(OUTSIDE)})
    check("白名单外返回 403", r.status_code, 403)
    has("403 文案说明允许范围", r.json().get("detail"), "不在可访问范围内")

    print("== 6. 白名单整个都不存在时的兜底 ==")
    save_roots([GHOST])
    data = client.get("/api/browse").json()
    check("roots 退化为原样返回", data["roots"], [str(GHOST)])
    has("error 提示没挂载进来", data["error"], "挂载")
    check("shortcuts 为空（全在白名单外）", data["shortcuts"], [])

    print("== 7. 不可枚举根的自动探测（suggestedRoots）==")
    from app.api.system import _probe_enumerable_children
    UID = MEDIA / "1000"          # 模拟 fnOS 的 /vol1/1000
    (UID / "video").mkdir(parents=True, exist_ok=True)
    (MEDIA / "hello").mkdir(exist_ok=True)   # 非数字名：不该被探测出来

    hits = _probe_enumerable_children(MEDIA)
    check("数字子目录被探测到", hits, [str(UID)])

    # 模拟 /vol1：对根的 readdir 被拒，但下面的路径一切正常
    real_scandir = os.scandir
    def fake_scandir(path):
        if Path(path) == MEDIA:
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    save_roots([MEDIA])
    with mock.patch("app.api.system.os.scandir", side_effect=fake_scandir):
        data = client.get("/api/browse").json()
        check("打开选择器第一眼就带探测结果",
              data["suggestedRoots"], [str(UID)])
        check("根本身没有 error（探测是静默的锦上添花）", data["error"], None)

        data = client.get("/api/browse", params={"path": str(MEDIA)}).json()
        check("点根进不去时也带探测结果",
              data["suggestedRoots"], [str(UID)])
        has("error 说明探测到了出路", data["error"], "点一下即可继续")
        has("error 仍提示白名单兜底", data["error"], "可访问根目录白名单")

    data = client.get("/api/browse", params={"path": str(INBOX)}).json()
    check("可正常枚举的目录没有多余建议", data["suggestedRoots"], [])

print()
if bad:
    print("失败 %d 项 / 通过 %d 项：" % (len(bad), ok))
    for b in bad:
        print("  - %s" % b)
    sys.exit(1)
print("全部通过（%d 项）" % ok)

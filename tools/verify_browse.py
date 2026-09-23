#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""目录选择器（/api/browse）的回归验证。

背景 —— 真机上暴露的缺陷：
fnOS 的存储池根目录 `/vol1` 权限位是 000，`getfacl` 连一条扩展 ACL 都没有，
所以内核拒绝对它 readdir，`/api/browse?path=/vol1` 直接回「没有权限读取该目录」。
但**访问它下面的目录完全正常**（/vol1/1000 能列出 11 个子目录）。
也就是说「从根往下逐级点」这条路在那种机器上第一级就是死的。

现在「可访问范围」已经**没有白名单这个设置**了：范围只由容器挂载决定
（读 `/proc/self/mountinfo` 自动探测）。本脚本跑在非 Linux 上（没有 /proc），
所以用环境变量 `VS_EXTRA_ROOTS` 代替「挂了哪些目录」—— 它与挂载探测走同一个出口。

逐条验证：
  1. 根目录恒为**实际存在**的目录（写错的路径不会变成点了就报错的死入口）
  2. 新增 shortcuts（常用目录）：已添加的监控目录 / 最近任务目录 / 系统输出目录，
     点一下直达，绕开不可枚举的那一层
  3. 列不出来时的错误文案必须给出路，而不是一句「没有权限」把人堵死；
     文案也不再指引「直接输入完整路径」—— 选择器已去掉手动输入入口，
     撤销页更是直接换成「监控目录」下拉框（不走 /api/browse）
  4. 范围之外（没挂载进容器）的路径一律 403，且文案指向「去 Docker 里挂载它」
  5. 挂载点解析：伪文件系统、根 overlay、`/data` 卷、挂载进来的文件都必须排除

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
GHOST = TMP / "ghost"            # 范围里写了但根本不存在的目录
OUTSIDE = TMP / "outside"        # 没挂载进来的目录
for d in (INBOX, OUT, OUT2, OUTSIDE):
    d.mkdir(parents=True)
(INBOX / "sub").mkdir()


def set_roots(roots):
    """模拟「容器里挂了哪些目录」。

    真机上范围来自 /proc/self/mountinfo 的挂载探测；本脚本在 Windows/macOS 上跑，
    改用环境变量 VS_EXTRA_ROOTS —— 两者在 _accessible_roots() 里汇合，
    所以这里改它等价于真机上改挂载。
    """
    os.environ["VS_EXTRA_ROOTS"] = os.pathsep.join(str(r) for r in roots)


# 初始挂三个：media 存在、ghost 不存在、outside 稍后要「取消挂载」。
# outside 先挂上（否则连监控目录都建不出来），造完数据再摘掉，
# 用来验证「没挂载的目录不再出现在快捷入口里」。
set_roots([MEDIA, GHOST, OUTSIDE])

# 设置侧只需要输出目录：save_settings 是「基于默认值的整体替换」，
# 漏写的键会被打回默认值，所以统一在这里给全。
config.save_settings({"split": {"outdir": str(OUT2)}})

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
    # 稍后要被「取消挂载」的监控目录：不该再出现在快捷入口里（点了也是 403）
    r = client.post("/api/watchpoints", json={
        "path": str(OUTSIDE), "recursive": False, "scanMode": "manual",
        "scanIntervalHours": 6, "scanTime": "03:00", "note": "未挂载"})
    check("POST 监控目录状态码（此刻还挂着）", r.status_code, 200)

    # 「取消挂载」outside，并附上一个不存在的 ghost：真机上写错挂载路径就是这个场景
    set_roots([MEDIA, GHOST])

    check("插入历史任务", db.insert_job({
        "id": "job_verify_1", "src": str(INBOX / "DJI_0001.MP4"),
        "src_name": "DJI_0001.MP4", "src_size": 4096,
        "outdir": str(OUT), "status": "success",
        "created_at": db.now_iso()}), True)

    print("== 1. roots 恒为实际存在的目录 ==")
    data = client.get("/api/browse").json()
    check("roots（ghost 不在其中）", data["roots"], [str(MEDIA)])
    check("dirs（就是可访问的根）", [d["path"] for d in data["dirs"]], [str(MEDIA)])
    check("没有 error", data["error"], None)
    check("path 为空串", data["path"], "")
    check("响应里不再有 missingRoots 字段", "missingRoots" in data, False)

    print("== 2. 常用目录：三个来源、顺序、去重、范围过滤 ==")
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
    check("没挂载的监控目录被排除",
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
    # 选择器已经不再提供手动输入路径的入口，文案里就别再让人去敲了
    check("error 不再指向手动输入",
          "输入完整路径" in (data["error"] or ""), False)

    print("== 5. 路径不存在 / 没挂载 ==")
    data = client.get("/api/browse", params={"path": str(MEDIA / "nope")}).json()
    has("不存在时的 error", data["error"], "目录不存在")
    check("不存在时 dirs 为空", data["dirs"], [])
    r = client.get("/api/browse", params={"path": str(OUTSIDE)})
    check("没挂载的路径返回 403", r.status_code, 403)
    has("403 文案说明不在已挂载范围", r.json().get("detail"), "不在容器已挂载的目录内")
    has("403 文案指向去挂载", r.json().get("detail"), "volumes")

    print("== 6. 一个挂载目录都没有时的兜底 ==")
    set_roots([GHOST])
    data = client.get("/api/browse").json()
    check("roots 为空", data["roots"], [])
    check("dirs 为空", data["dirs"], [])
    has("error 提示去挂载", data["error"], "挂载")
    check("shortcuts 为空（全在范围外）", data["shortcuts"], [])
    r = client.post("/api/watchpoints", json={"path": str(INBOX)})
    check("没有任何挂载时建监控目录返回 400", r.status_code, 400)

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

    set_roots([MEDIA])
    with mock.patch("app.api.system.os.scandir", side_effect=fake_scandir):
        data = client.get("/api/browse").json()
        check("打开选择器第一眼就带探测结果",
              data["suggestedRoots"], [str(UID)])
        check("根本身没有 error（探测是静默的锦上添花）", data["error"], None)

        data = client.get("/api/browse", params={"path": str(MEDIA)}).json()
        check("点根进不去时也带探测结果",
              data["suggestedRoots"], [str(UID)])
        has("error 说明探测到了出路", data["error"], "点一下即可继续")
        has("error 说明根的不可枚举是有意为之", data["error"], "不可枚举")

    data = client.get("/api/browse", params={"path": str(INBOX)}).json()
    check("可正常枚举的目录没有多余建议", data["suggestedRoots"], [])

    print("== 8. 挂载点解析：只认「用户挂进来的数据目录」==")
    from app.api.system import (_detect_mounted_roots,  # noqa: E402
                                _parse_mountinfo)
    # 一份尽量贴近真机的 mountinfo：根 overlay、伪文件系统、/data 卷、
    # Docker 自动挂的文件（/etc/hosts）、以及用户显式挂的两个数据目录
    SAMPLE_MOUNTINFO = "\n".join([
        "36 35 0:32 / / rw,relatime shared:1 - overlay overlay rw",
        "37 36 0:33 / /proc rw - proc proc rw",
        "38 36 0:34 / /sys rw - sysfs sysfs rw",
        "39 36 0:35 / /dev rw - tmpfs tmpfs rw",
        "40 36 259:1 /vol1/data /data rw,relatime - ext4 /dev/md0 rw",
        "41 36 259:1 / /vol1 rw,relatime - btrfs /dev/md0 rw",
        "42 36 259:1 /1000/video-split-in /test-dir rw,relatime - btrfs /dev/md0 rw",
        "43 36 259:1 /docker/x/hosts /etc/hosts rw,relatime - btrfs /dev/md0 rw",
        r"44 36 259:1 /dir\040with\040space /with-space rw,relatime - btrfs /dev/md0 rw",
    ])
    parsed = _parse_mountinfo(SAMPLE_MOUNTINFO)
    check("解析出用户挂载", parsed[:3], ["/vol1", "/test-dir", "/etc/hosts"])
    check("根 overlay 被排除", "/" in parsed, False)
    check("伪文件系统被排除",
          [p for p in parsed if p in ("/proc", "/sys", "/dev")], [])
    check("/data 卷被排除（程序状态不暴露）", "/data" in parsed, False)
    check("转义的空格被还原", "/with-space" in parsed, True)

    # _detect 层再按「真实存在的目录」过滤：/etc/hosts 是文件、GHOST 不存在
    MOUNTED_FILE = MEDIA / "mounted-file"
    MOUNTED_FILE.touch()
    sample = "\n".join([
        "41 36 259:1 / %s rw - btrfs /dev/md0 rw" % MEDIA,
        "42 36 259:1 / %s rw - btrfs /dev/md0 rw" % GHOST,
        "43 36 259:1 /f %s rw - btrfs /dev/md0 rw" % MOUNTED_FILE,
        "44 36 0:33 / /proc rw - proc proc rw",
    ])
    with mock.patch("builtins.open", mock.mock_open(read_data=sample)):
        check("只保留真实存在的目录挂载点",
              _detect_mounted_roots(), [str(MEDIA)])
    check("无 /proc 的环境探测不报错且返回列表",
          isinstance(_detect_mounted_roots(), list), True)

    # 集成：挂载探测与环境变量汇合，且「挂上的目录立刻可浏览、可加监控目录」
    set_roots([MEDIA])
    r = client.get("/api/browse", params={"path": str(OUTSIDE)})
    check("未挂载时 outside 是 403", r.status_code, 403)
    with mock.patch("app.api.system._detect_mounted_roots",
                    return_value=[str(OUTSIDE)]):
        data = client.get("/api/browse").json()
        check("探测到的挂载点进入 roots", data["roots"],
              [str(OUTSIDE), str(MEDIA)])
        r = client.get("/api/browse", params={"path": str(OUTSIDE)})
        check("挂载目录可浏览（不再 403）", r.status_code, 200)
        r = client.post("/api/watchpoints", json={
            "path": str(OUTSIDE), "recursive": False, "scanMode": "manual",
            "scanIntervalHours": 6, "scanTime": "03:00"})
        # 第 0 节已建过同路径监控目录，这里应撞「重复」409 而不是范围外 403
        # —— 409 恰恰证明 ensure_allowed 已经放行
        check("挂载目录可过范围闸（撞重复而非 403）", r.status_code, 409)
    r = client.get("/api/browse", params={"path": str(OUTSIDE)})
    check("探测恢复后 outside 回到范围外", r.status_code, 403)

print()
if bad:
    print("失败 %d 项 / 通过 %d 项：" % (len(bad), ok))
    for b in bad:
        print("  - %s" % b)
    sys.exit(1)
print("全部通过（%d 项）" % ok)

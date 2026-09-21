#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真机验证：原片处理方式的三级设置（系统默认 / 监控目录 / 手动扫描）。

跑在真实 NAS 上，用两个临时目录 + 三份几 MB 的小视频，把「这个设置到底
是谁说了算」按来源各走一遍，最后确认磁盘上原片真的被改名 / 搬走 / 删掉了：

  [1] 监控目录级 —— 该目录设成 move + 指定归档名，扫描后原片真的躺在那里
  [2] 手动级     —— 同一次扫描带 delete 覆盖，原片真的没了
  [3] 系统级     —— 把目录设置清空回「跟随」，落回系统默认的 rename
  [4] 防重切     —— 已经归档走的原片不会被当成新视频再切一遍
  [5] 老名字的排除 —— 归档目录名改掉/清空之后，老归档目录里的原片仍被排除
                     （2026-09-21 在这里抓到过重复入队，是这条把缺陷钉死的）

跑完会把系统设置、监控目录、临时文件、测试产生的任务记录都还原/清掉。

用法（接口地址按 参数 → 环境变量 → 凭据文件 的顺序取，仓库里不写死内网地址）：
    python tools/nas_verify_mark_live.py
    python tools/nas_verify_mark_live.py http://<NAS 地址>:8099
    NAS_BASE_URL=http://<NAS 地址>:8099 python tools/nas_verify_mark_live.py

前置：
  - 本机有 ffmpeg（用来造测试视频）
  - tools/ssh_run.py 能连上 NAS（凭据读 deploy/.nas-credentials）

为什么非要真机跑：本地那几套验证脚本用的都是内存里的假 spec，
证明不了「容器里以 PUID 降权跑起来的进程，真能把原片改名/搬走/删掉」
——权限位、挂载、ACL 这些东西只有真机上才现形（这台飞牛的 /vol1/1000/**
经典权限位就是 000，靠 ACL 才能读写）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import ssh_run                                # noqa: E402
from core import splitter as engine           # noqa: E402

def _resolve_base() -> str:
    """按 参数 → 环境变量 → 凭据文件 的顺序定出接口地址。

    刻意不在这里写死内网地址：本项目准备开源，源码里带着自己的网络指纹
    没有意义（而 NAT 之内它也帮不到别人）。凭据文件里本来就有 NAS 主机名，
    顺手就能拼出来，用户不必再记一个 URL。
    """
    if len(sys.argv) > 1:
        return sys.argv[1].rstrip("/")
    if os.environ.get("NAS_BASE_URL"):
        return os.environ["NAS_BASE_URL"].rstrip("/")
    host = ssh_run.cred("NAS_HOST")
    if host:
        return "http://%s:%s" % (host, ssh_run.cred("NAS_HTTP_PORT", "8099"))
    print("没拿到 NAS 地址，三种给法任选：\n"
          "  1) python tools/nas_verify_mark_live.py http://<地址>:8099\n"
          "  2) NAS_BASE_URL=http://<地址>:8099 python tools/nas_verify_mark_live.py\n"
          "  3) 在 deploy/.nas-credentials 里写 NAS_HOST=<地址>（可选 NAS_HTTP_PORT）")
    raise SystemExit(2)


BASE = _resolve_base()
#: 测试目录。放在 /vol1 下，容器里挂的就是 /vol1（路径与宿主机一致）
TEST_DIR = os.environ.get("NAS_TEST_DIR", "/vol1/1000/vs-live-verify")
#: 监控目录级指定的归档目录名（故意跟系统默认名不一样，才验得出是谁生效）。
#:
#: **每轮都换一个**：归档目录名是「用过就记住」的（只增不减），用固定名字的话
#: 第二轮开始它早就在记录里了，[5] 那条用例就不再依赖「本次刚刚记住」，
#: 会悄悄退化成恒真 —— 正是这类用例最容易骗过自己。带随机后缀时，
#: 它每轮都必须靠本次运行真正写进 archive-dirs.json 才能过。
WP_ARCHIVE = "live-archive-%s" % uuid.uuid4().hex[:6]

#: 切分阈值。必须明显小于测试视频体积，否则连入队都进不去——
#: 扫描器对「体积没超过阈值」的文件是按「无需切分」直接跳过的
#: （scanner.consider_file 里那条 `size <= threshold`），
#: 而「切完才处理原片」这个前提就不成立了，测了等于没测。
SPLIT_SIZE = "2M"
SETTLE = 5
#: [1][2][3] 三个用例各该产生一条任务记录；[4][5] 是「不该有新任务」的用例，
#: 所以本次跑下来总数就该是 3。多出来 = 有文件被重复入队了，按失败算。
EXPECTED_JOBS = 3

_passed = 0
_failed = 0
_wp_id = None
#: 开跑前的任务 id 集合。收尾时用它做差集，把本次跑出来的记录全清掉
_baseline_jobs = set()


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print("    OK   %s" % label)
    else:
        _failed += 1
        print("    FAIL %s\n         实际 %r\n         期望 %r" % (label, got, want))


def check_true(label, cond, detail=""):
    check(label + ("（%s）" % detail if detail else ""), bool(cond), True)


def call(method, path, body=None, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:                          # noqa: BLE001
            return e.code, raw


def ssh(cmd, timeout=180):
    """在 NAS 宿主机上跑一条命令，返回 (exit_code, stdout)。"""
    cli = _ssh_cli()
    code, out, err = ssh_run.run(cli, cmd, timeout=timeout)
    return code, out + err


_cli = None


def _ssh_cli():
    global _cli
    if _cli is None:
        _cli = ssh_run.connect()
    return _cli


def exists(remote_path) -> bool:
    return ssh("test -e %s && echo YES || echo NO" % _shq(remote_path))[1].strip().endswith("YES")


def _shq(s):
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def make_video(path: Path) -> bool:
    """造一个 12 秒、约 6MB、关键帧间隔 1 秒的小视频。

    两个参数都不能随便动：
    - 关键帧必须密（-g 30）：流拷贝的切点只能落在关键帧上，默认 250 帧的
      间隔会让每一段都超阈值，引擎就退化成纯字节切割了，测的不是同一条路。
    - 用 testsrc2 而不是 testsrc：testsrc 是几块纯色在动，压得极狠——
      实测 640x480 二十秒只有 0.9MB，比阈值还小，会被判成「无需切分」跳过。
    """
    if path.is_file():
        return True
    ffmpeg = engine.find_bin("ffmpeg")
    if not ffmpeg:
        print("本机没有 ffmpeg，造不出测试视频。")
        return False
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "12",
           "-c:v", "libx264", "-preset", "veryfast",
           "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
           "-b:v", "4000k", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    return path.is_file()


def upload(local: Path, name: str) -> str:
    dest = "%s/%s" % (TEST_DIR, name)
    cli = _ssh_cli()
    sftp = cli.open_sftp()
    try:
        sftp.put(str(local), dest)
    finally:
        sftp.close()
    return dest


def stage(local: Path, name: str) -> None:
    """传一个测试视频上去，并等稳定检测放行。

    刻意一次只传一个：三个一起摆上去的话，第一次扫描就把它们全入队了，
    后面「这一次扫描用哪种处理方式」的用例就分不清是谁的产物。
    """
    upload(local, name)
    time.sleep(SETTLE + 3)


def all_job_ids():
    """当前所有任务 id。收尾靠它做集合差，不靠脚本自己记账。

    为什么不用「扫描一次就记一个 id」：真机上跑出来过漏网的——某次扫描
    意外多入队了一个文件（那次是归档目录判定的缺陷），记下来的 id 只覆盖
    了预期的那个，剩下那个就留在 NAS 上了。改成前后取集合差，多出来的
    一律清掉，跟「预期有几个」无关。
    """
    _, data = call("GET", "/api/jobs?limit=200")
    return {j["id"] for j in (data or {}).get("items") or []}


def wait_job(job_id, timeout=240):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        _, job = call("GET", "/api/jobs/%s" % job_id)
        if job and job.get("status") in ("success", "failed", "canceled", "skipped"):
            return job
        time.sleep(3)
    return job


def scan_and_get_job(body=None, expect=None):
    """扫一次并返回新产生的那个任务。

    expect 传文件名时按 src 校准一次：任务列表的排序不归这个脚本管，
    宁可多找一下，也别把上一轮的任务当成这一轮的结果。
    """
    _, before = call("GET", "/api/jobs?limit=1")
    n_before = (before or {}).get("total", 0)
    st, res = call("POST", "/api/watchpoints/%s/scan" % _wp_id, body)
    if st != 200:
        print("    扫描接口返回 %s：%s" % (st, res))
        return None, res
    _, after = call("GET", "/api/jobs?limit=10")
    items = (after or {}).get("items") or []
    if (after or {}).get("total", 0) <= n_before:
        return None, res
    job = items[0]
    if expect:
        hit = [j for j in items if Path(j.get("src") or "").name == expect]
        if hit:
            job = hit[0]
    return job, res


def slices_of(name: str):
    """列出某原片切出来的切片文件名（形如 case1#3.mp4）。"""
    stem = Path(name).stem
    code, out = ssh("ls -1 %s | grep -E '^%s#[0-9]+\\.' || true"
                    % (_shq(TEST_DIR), stem))
    return sorted(x for x in out.split() if x.strip())


# ------------------------------------------------------------------ 主流程

def main() -> int:
    print("真机验证：原片处理方式的三级设置")
    print("  NAS   %s" % BASE)
    print("  目录  %s" % TEST_DIR)
    print()

    st, health = call("GET", "/api/health", timeout=20)
    if st != 200:
        print("NAS 没应答（%s），先确认容器活着。" % st)
        return 2
    print("  容器版本 %s ｜ 运行 %.0f 秒" % (health.get("version"), health.get("uptimeSec")))

    _, original = call("GET", "/api/settings")
    # 收尾里会给 _failed 加一（多入队 = 原片被重切），所以必须声明 global：
    # 不声明的话 Python 会把 _failed 当成 main 的局部变量，遮蔽掉 check() 里
    # 一直累加的那个全局计数，最后读它直接 UnboundLocalError。
    global _failed, _baseline_jobs
    _baseline_jobs = all_job_ids()
    print("  开跑前有 %d 条任务记录（本次新增的收尾会清掉）" % len(_baseline_jobs))
    tmp = ROOT / ".tmp-verify" / "live"
    tmp.mkdir(parents=True, exist_ok=True)
    created_wp = False
    #: 收尾是否清干净。同样在 main 里先赋值，理由见上面的 global
    leftover_ok = True
    try:
        # ------------------------------------------------------ 准备
        print("\n[0] 准备：临时目录、测试视频、临时设置")
        code, _ = ssh("mkdir -p %s && chmod 777 %s" % (_shq(TEST_DIR), _shq(TEST_DIR)))
        if code != 0:
            print("    建目录失败，先看 NAS 上的权限。")
            return 1
        print("    目录已就绪：%s" % TEST_DIR)

        videos = {}
        for name in ("case1.mp4", "case2.mp4", "case3.mp4"):
            local = tmp / name
            if not make_video(local):
                return 1
            videos[name] = local
        mb = videos["case1.mp4"].stat().st_size / 1024 / 1024
        if mb < 3:
            print("    测试视频只有 %.1f MB，跟阈值 %s 太接近，会被判成"
                  "「无需切分」而跳过。先把 make_video 的参数调大。" % (mb, SPLIT_SIZE))
            return 1
        print("    已备好 3 个测试视频（各约 %.1f MB，用到哪个才传哪个）" % mb)

        # 阈值调小 + 稳定检测调短：文件刚传上去要等一会儿才被认为「写完了」，
        # 默认 60 秒会让每一轮都白等
        s = dict(original)
        s["split"] = dict(original["split"], size=SPLIT_SIZE)
        s["watch"] = dict(original["watch"], settleSeconds=SETTLE)
        st, saved = call("PUT", "/api/settings", s)
        print("    临时设置：阈值 %s ｜ 稳定检测 %ss（收尾会还原）"
              % (saved["split"]["size"], saved["watch"]["settleSeconds"]))

        # 监控目录级：move + 指定归档名（与系统默认名不同，才看得出谁生效）
        st, wp = call("POST", "/api/watchpoints", {
            "path": TEST_DIR, "recursive": True, "scanMode": "manual",
            "scanIntervalHours": 6, "scanTime": "03:00",
            "note": "原片处理方式真机验证（跑完自删）",
            "markSource": "move", "sourceDir": WP_ARCHIVE,
        })
        if st != 200:
            print("    建监控目录失败：%s %s" % (st, wp))
            return 1
        created_wp = True
        global _wp_id
        _wp_id = wp["id"]
        print("    监控目录已建：%s（markSource=%s sourceDir=%s）"
              % (_wp_id, wp["markSource"], wp["sourceDir"]))
        check("接口如实回读了监控目录级设置",
              [wp["markSource"], wp["sourceDir"]], ["move", WP_ARCHIVE])

        time.sleep(SETTLE + 3)      # 等稳定检测放行

        # ------------------------------------------------ [1] 监控目录级
        print("\n[1] 监控目录级：该目录设成 move，扫描后原片应被搬进 %s/" % WP_ARCHIVE)
        stage(videos["case1.mp4"], "case1.mp4")
        job, res = scan_and_get_job(expect="case1.mp4")
        if not job:
            print("    没扫出任务：%s" % res)
            return 1
        check("任务快照取的是监控目录级的 move（而不是系统默认的 rename）",
              [job.get("markSource"), job.get("sourceDir")], ["move", WP_ARCHIVE])
        job = wait_job(job["id"])
        check("任务成功", job.get("status"), "success")
        check_true("原片已躺在 %s/ 里" % WP_ARCHIVE,
                   exists("%s/%s/case1.mp4" % (TEST_DIR, WP_ARCHIVE)))
        check_true("原处不再有 case1.mp4", not exists("%s/case1.mp4" % TEST_DIR))
        got = slices_of("case1.mp4")
        check_true("切片已产出（%d 段）" % len(got), len(got) >= 2, ", ".join(got))

        # ------------------------------------------------ [4] 防重切
        print("\n[4] 防重切：再扫一次，已经归档走的 case1.mp4 不该被当成新视频")
        _, before = call("GET", "/api/jobs?limit=1")
        n_before = (before or {}).get("total", 0)
        st, res = call("POST", "/api/watchpoints/%s/scan" % _wp_id)
        _, after = call("GET", "/api/jobs?limit=1")
        res = res or {}
        check("没有新增任务", (after or {}).get("total", 0), n_before)
        check("没有入队任何文件", res.get("queued"), 0)
        # 目录里只剩切片，收集阶段就把它们滤掉了，所以一个都不该进 found
        check("没有需要处理的新视频", res.get("found"), 0)
        # 关键的一条：归档走的那份必须被明确登记为「位于原片归档目录」。
        # 只断言 queued=0 是不够的——扫描器整棵目录都没扫到，结果也是 queued=0。
        reasons = [i.get("reason") for i in res.get("ignored") or []]
        check_true("归档走的原片被登记为「位于原片归档目录」",
                   "位于原片归档目录" in reasons,
                   "实际理由：%s" % (reasons or "（空）"))

        # ------------------------------------------------ [2] 手动级
        print("\n[2] 手动级：同一次扫描带 delete 覆盖，这次不该走目录设置")
        stage(videos["case2.mp4"], "case2.mp4")
        job, res = scan_and_get_job({"markSource": "delete"}, expect="case2.mp4")
        if not job:
            print("    没扫出任务：%s" % res)
            return 1
        check("任务快照取的是本次手动指定的 delete（盖过目录的 move）",
              job.get("markSource"), "delete")
        job = wait_job(job["id"])
        check("任务成功", job.get("status"), "success")
        check_true("原片已被删除", not exists("%s/case2.mp4" % TEST_DIR))
        check_true("切片已产出", len(slices_of("case2.mp4")) >= 2)
        check_true("这次的原始文件没有跑到归档目录去（用的是 delete，不是 move）",
                   not exists("%s/%s/case2.mp4" % (TEST_DIR, WP_ARCHIVE)))

        # ------------------------------------------------ [3] 系统级
        print("\n[3] 系统级：把目录设置清空回「跟随」，应落回系统默认的 rename")
        st, wp2 = call("PUT", "/api/watchpoints/%s" % _wp_id,
                       {"markSource": "", "sourceDir": ""})
        check("清空后接口回读为空串（= 跟随系统）",
              [wp2.get("markSource"), wp2.get("sourceDir")], ["", ""])
        stage(videos["case3.mp4"], "case3.mp4")
        job, res = scan_and_get_job(expect="case3.mp4")
        if not job:
            print("    没扫出任务：%s" % res)
            return 1
        check("任务快照落回系统默认", [job.get("markSource"), job.get("sourceDir")],
              [original["split"]["markSource"], original["split"]["sourceDir"]])
        job = wait_job(job["id"])
        check("任务成功", job.get("status"), "success")
        if original["split"]["markSource"] == "rename":
            check_true("原片已被加上 #origin 后缀留在原处（走的系统默认 rename）",
                       exists("%s/case3#origin.mp4" % TEST_DIR))
        else:
            print("    （系统默认是 %s，不是 rename，这条跳过）"
                  % original["split"]["markSource"])
        check_true("原处不再有 case3.mp4", not exists("%s/case3.mp4" % TEST_DIR))

        # ------------------------------------------------ [5] 老名字的排除
        # 归档目录名刚刚被清空回「跟随系统」，按当前配置现算的话，
        # live-archive 已经不在候选里了 —— 而 case1.mp4 就躺在里面且保留着原文件名。
        # 只按配置现算的实现，会在这一步把它重新切一遍（2026-09-21 实测如此）。
        print("\n[5] 老名字的排除：归档名清空后，%s/ 里的原片仍不该被重切" % WP_ARCHIVE)
        _, before = call("GET", "/api/jobs?limit=1")
        n_before = (before or {}).get("total", 0)
        st, res = call("POST", "/api/watchpoints/%s/scan" % _wp_id)
        _, after = call("GET", "/api/jobs?limit=1")
        res = res or {}
        check("没有新增任务", (after or {}).get("total", 0), n_before)
        check("没有入队任何文件", res.get("queued"), 0)
        reasons = [i.get("reason") for i in res.get("ignored") or []]
        check_true("老归档目录里的原片仍被登记为「位于原片归档目录」",
                   "位于原片归档目录" in reasons,
                   "实际理由：%s" % (reasons or "（空）"))
        check_true("%s/case1.mp4 还在原处（没被重切、也没被再标记一次）" % WP_ARCHIVE,
                   exists("%s/%s/case1.mp4" % (TEST_DIR, WP_ARCHIVE)))
    finally:
        # ------------------------------------------------------ 收尾
        print("\n[6] 收尾：还原设置、删监控目录、清测试文件与任务记录")
        try:
            call("PUT", "/api/settings", original)
            print("    设置已还原（阈值 %s ｜ 稳定检测 %ss）"
                  % (original["split"]["size"], original["watch"]["settleSeconds"]))
        except Exception as exc:                   # noqa: BLE001
            print("    还原设置失败，请手动检查：%s" % exc)
        if created_wp and _wp_id:
            call("DELETE", "/api/watchpoints/%s" % _wp_id)
            print("    监控目录 %s 已删" % _wp_id)
        ssh("rm -rf %s" % _shq(TEST_DIR))
        print("    测试目录已清空：%s" % TEST_DIR)
        # 归档目录的使用记录是「只增不减」的（config.collect_archive_dirs），
        # 所以本次用过的 %s 会留在容器的 /data/archive-dirs.json 里，删测试目录
        # 也带不走它。这**不是**脏数据：它只让「恰好也叫这个名字的子目录」被跳过，
        # 而且老原片还得靠它才不被重切；名字带随机后缀，每跑一轮多一条。
        print("    提示：/data/archive-dirs.json 会留下 %s 这条使用记录"
              "（设计如此：只增不减，跑一轮多一条）" % WP_ARCHIVE)
        # 按集合差清理，而不是按脚本记下的 id：多入队的、预期的，一视同仁
        leftover = all_job_ids() - _baseline_jobs
        # 三个用例各该产生一条，多出来的就是有文件被重复入队了 —— 那是**失败**，
        # 不是提醒：被重复入队意味着原片被重切了一遍（真机上撞到过：把监控目录的
        # 归档目录名清空后，原先那个名字不再被排除，躺在里面的原片被重新切了）。
        # 明细必须在**删之前**取 —— 删完再查就只剩 404 了。
        extra = []
        if len(leftover) > EXPECTED_JOBS:
            for jid in sorted(leftover):
                _, j = call("GET", "/api/jobs/%s" % jid)
                extra.append((jid, (j or {}).get("src")))
        gone = 0
        for jid in sorted(leftover):
            st, _ = call("DELETE", "/api/jobs/%s" % jid)
            gone += 1 if st in (200, 204) else 0
        print("    本次新增的 %d 条任务记录已清理" % gone)
        if extra:
            _failed += 1
            print("    [失败] 产生了 %d 条任务，比预期的 %d 条多 %d 条："
                  % (len(leftover), EXPECTED_JOBS, len(leftover) - EXPECTED_JOBS))
            for jid, src in extra:
                print("      %s  %s" % (jid, src))
            print("      多出来的就是「本该被排除的文件又被入队了」——原片被重切了一遍。"
                  "排查方向：collect_archive_dirs 的排除集里漏了某个**用过的**归档"
                  "目录名（data/archive-dirs.json 里记着）。")
        still = all_job_ids() - _baseline_jobs
        if still:
            print("    [注意] 还有 %d 条没删掉，请手动处理：%s"
                  % (len(still), ", ".join(sorted(still))))
            leftover_ok = False
        if _cli is not None:
            _cli.close()

    if not leftover_ok:
        print("\n收尾没清干净，NAS 上还留着本次生成的记录。")
    ok = not _failed and leftover_ok
    print("\n%s（%d 项通过，%d 项失败）"
          % ("真机验证通过" if ok else "真机验证**未通过**", _passed, _failed))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

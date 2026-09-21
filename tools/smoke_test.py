#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/smoke_test.py —— 端到端冒烟测试

真起一个服务进程，用真实 ffmpeg 造一个小视频，把整条链路跑一遍：

    健康检查 -> 改设置 -> 添加监控目录 -> 扫描入队 -> 等任务完成
    -> 查日志 -> 撤销预览 -> 真正撤销 -> 校验切片已删、原片改名已还原

它验证的是「各模块串起来能不能用」，不替代单元测试。
跑法：
    python tools/smoke_test.py

注意：会在项目下建 .tmp-test 目录，测试完可以整个删掉。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
TEST_ROOT = PROJECT_DIR / ".tmp-test"
INBOX = TEST_ROOT / "inbox"
DATA_DIR = TEST_ROOT / "data"
PORT = 8123
BASE = "http://127.0.0.1:%d" % PORT

PASSED = []
FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASSED.append(name)
        print("  [OK]   %s%s" % (name, ("  — " + detail) if detail else ""))
    else:
        FAILED.append(name)
        print("  [FAIL] %s%s" % (name, ("  — " + detail) if detail else ""))


def req(method: str, path: str, payload=None, timeout: float = 60.0):
    url = BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, {"detail": body}


def find_ffmpeg() -> str:
    sys.path.insert(0, str(PROJECT_DIR))
    from core import splitter
    return splitter.find_bin("ffmpeg")


def make_test_video(ffmpeg: str, path: Path) -> bool:
    """
    造一个 20 秒、约 3.5MB 的测试视频。

    关键是要把关键帧间隔强行设成 1 秒（-g 30）：分隔切点必须落在关键帧上，
    默认的 250 帧间隔（8 秒多）会让每一段都超出目标时长，
    触发引擎的自动重试，测试就失去了意义。
    """
    if path.exists():
        return True
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=640x480:rate=30",
        "-t", "20",
        "-c:v", "libx264", "-preset", "veryfast",
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        "-b:v", "1500k", "-pix_fmt", "yuv420p",
        str(path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        return path.is_file() and path.stat().st_size > 0
    except Exception as exc:                      # noqa: BLE001
        print("  造测试视频失败：%s" % exc)
        return False


def wait_for_server(deadline: float = 60.0) -> bool:
    end = time.time() + deadline
    while time.time() < end:
        try:
            status, _ = req("GET", "/api/health", timeout=3)
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    print("=" * 68)
    print("video-splitter 端到端冒烟测试")
    print("=" * 68)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("未找到 ffmpeg，无法测试流拷贝模式。请先安装 ffmpeg。")
        return 2
    print("ffmpeg: %s" % ffmpeg)

    # 每次从干净状态开始，避免上次的残留影响判断
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    video = INBOX / "test_clip.mp4"
    print("正在生成测试视频…")
    if not make_test_video(ffmpeg, video):
        print("测试视频生成失败，中止。")
        return 2
    size = video.stat().st_size
    print("测试视频：%s（%d 字节）" % (video.name, size))

    env = dict(os.environ)
    env["VS_DATA_DIR"] = str(DATA_DIR)
    env["VS_HOST"] = "127.0.0.1"
    env["VS_PORT"] = str(PORT)
    env["VS_LOG_LEVEL"] = "warning"
    env["PYTHONIOENCODING"] = "utf-8"

    server = subprocess.Popen(
        [sys.executable, "-m", "app.main"],
        cwd=str(PROJECT_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")

    try:
        print("\n启动服务…")
        if not wait_for_server():
            print("服务启动失败，输出如下：")
            server.terminate()
            print(server.stdout.read() if server.stdout else "")
            return 2
        print("服务已就绪\n")

        # ---------------------------------------------------------- 健康检查
        print("[1] 系统接口")
        status, health = req("GET", "/api/health")
        check("GET /api/health 返回 200", status == 200, str(health)[:120])
        check("识别到 ffmpeg", bool(health and health["ffmpeg"]["ok"]))
        check("识别到 ffprobe", bool(health and health["ffprobe"]["ok"]))

        # ---------------------------------------------------------- 设置
        print("\n[2] 设置")
        status, settings = req("GET", "/api/settings")
        check("GET /api/settings 返回 200", status == 200)

        # 切割方式已固定为 ffmpeg 流拷贝，不再有 mode 设置项
        settings["split"]["bySize"] = True
        settings["split"]["size"] = "400K"   # 合成画面压缩率高，20 秒才 ~1MB，取 400K 切成 3 段
        settings["split"]["markSource"] = "rename"
        settings["split"]["recursive"] = True
        settings["watch"]["settleSeconds"] = 0     # 测试里不等稳定检测
        settings["watch"]["minSize"] = "0"
        settings["watch"]["realtime"] = True
        settings["watch"]["allowedRoots"] = [str(INBOX)]

        status, saved = req("PUT", "/api/settings", settings)
        check("PUT /api/settings 返回 200", status == 200)
        check("设置已保存", bool(saved and saved["split"]["size"] == "400K"),
              (saved or {}).get("split", {}).get("size"))

        # 用仍存在的枚举字段验证「非法值被拒」（mode 设置项已随纯字节切割一起取消）
        status, bad = req("PUT", "/api/settings",
                          {"split": {"markSource": "不存在的模式"}})
        check("非法枚举被拒绝", status == 422, "HTTP %s" % status)

        # ---------------------------------------------------------- 目录浏览
        print("\n[3] 目录浏览")
        status, browse = req("GET", "/api/browse?path=%s" % str(INBOX))
        check("GET /api/browse 返回 200", status == 200)
        check("统计到 1 个视频文件", bool(browse and browse["videoCount"] == 1),
              "videoCount=%s" % (browse or {}).get("videoCount"))

        status, denied = req("GET", "/api/browse?path=%s" % str(PROJECT_DIR))
        check("白名单外的路径被拒绝", status == 403, "HTTP %s" % status)

        # ---------------------------------------------------------- 监控目录
        print("\n[4] 监控目录")
        status, wp = req("POST", "/api/watchpoints",
                         {"path": str(INBOX), "recursive": True, "note": "冒烟测试"})
        check("POST /api/watchpoints 返回 200", status == 200, str(wp)[:120])
        wp_id = (wp or {}).get("id")

        status, dup = req("POST", "/api/watchpoints", {"path": str(INBOX)})
        check("重复添加被拒绝", status == 409, "HTTP %s" % status)

        status, items = req("GET", "/api/watchpoints")
        check("GET /api/watchpoints 返回 1 条", status == 200 and len(items) == 1)

        status, outside = req("POST", "/api/watchpoints", {"path": str(PROJECT_DIR)})
        check("添加白名单外目录被拒绝", status == 403, "HTTP %s" % status)

        # ---------------------------------------------------------- 扫描入队
        print("\n[5] 扫描与入队")
        status, scan = req("POST", "/api/watchpoints/%s/scan" % wp_id)
        check("扫描接口返回 200", status == 200, str(scan)[:140])
        check("成功入队 1 个任务", bool(scan and scan["queued"] == 1),
              "queued=%s" % (scan or {}).get("queued"))

        # ---------------------------------------------------------- 等待完成
        print("\n[6] 执行任务")
        job = None
        deadline = time.time() + 180
        last_phase = None
        while time.time() < deadline:
            status, listing = req("GET", "/api/jobs?limit=10")
            if listing and listing["items"]:
                job = listing["items"][0]
                if job["phase"] != last_phase:
                    last_phase = job["phase"]
                    print("      阶段：%-10s 进度 %.0f%%  %s"
                          % (job["phase"], (job["progress"] or 0) * 100,
                             job["message"] or ""))
                if job["status"] in ("success", "failed", "canceled"):
                    break
            time.sleep(1)

        check("任务已完成", bool(job and job["status"] == "success"),
              "status=%s error=%s" % ((job or {}).get("status"), (job or {}).get("error")))
        if not job or job["status"] != "success":
            print("\n任务失败，服务日志：")
            server.terminate()
            print((server.stdout.read() or "")[-4000:])
            return 1

        check("记录为流拷贝模式", job["usedMode"] == "copy", str(job["usedMode"]))
        check("产生了多个切片", len(job["produced"]) > 1,
              "共 %d 段" % len(job["produced"]))
        check("进度走到 100%", abs((job["progress"] or 0) - 1.0) < 0.001,
              str(job["progress"]))

        # 每一段都不能超过阈值，这是这个工具存在的意义
        over = [p for p in job["produced"] if p["size"] > 400 * 1024]
        check("每个切片都不超过阈值 400K", not over,
              "超标 %d 个" % len(over))
        check("切片文件真实存在",
              all(Path(p["path"]).is_file() for p in job["produced"]))

        with open(str(video.with_name("test_clip#origin.mp4")), "rb") as f:
            head = f.read(4)
        check("原片已改名为 #origin", head != b"", "原片仍在原位")

        status, log = req("GET", "/api/jobs/%s/log" % job["id"])
        check("能取到任务日志", status == 200 and len(log["lines"]) > 3,
              "共 %d 行" % len((log or {}).get("lines") or []))

        # ---------------------------------------------------------- 撤销
        print("\n[7] 撤销分割")
        status, preview = req("POST", "/api/undo/preview",
                             {"path": str(INBOX), "recursive": True})
        check("撤销预览返回 200", status == 200, str(preview)[:140])
        check("识别出 1 组可撤销的分割",
              bool(preview and len(preview["groups"]) == 1),
              "groups=%s" % len((preview or {}).get("groups") or []))
        group = (preview or {}).get("groups", [{}])[0]
        check("校验通过（时长核对）", bool(group.get("ok")), group.get("reason", ""))
        check("判定为流拷贝产物", group.get("mode") == "copy", str(group.get("mode")))

        status, report = req("POST", "/api/undo/apply",
                             {"path": str(INBOX), "recursive": True,
                              "deleteSlices": True, "restoreOrigin": True,
                              "trash": True})
        check("执行撤销返回 200", status == 200, str(report)[:160])
        if report and (report.get("deleted", 0) == 0 or report.get("problems")):
            print("      撤销详情：%s" % json.dumps(report, ensure_ascii=False)[:1200])
        check("切片已被删除",
              bool(report and report["deleted"] == len(job["produced"])),
              "deleted=%s" % (report or {}).get("deleted"))
        check("原片名已还原", bool(report and report["restored"] == 1),
              "restored=%s" % (report or {}).get("restored"))
        check("目录里只剩原片",
              video.is_file() and not any(INBOX.glob("test_clip#*.mp4")))
        check("没有遗留问题", bool(report and not report["problems"]),
              str((report or {}).get("problems"))[:140])

        # ---------------------------------------------------------- 原片处理三级设置
        print("\n[8] 原片处理方式：三级设置")
        status, cur = req("GET", "/api/settings")
        check("设置里有 markSource 字段",
              status == 200 and "markSource" in (cur or {}).get("split", {}),
              str((cur or {}).get("split", {}).get("markSource")))
        check("设置里有 sourceDir 字段",
              "sourceDir" in (cur or {}).get("split", {}),
              str((cur or {}).get("split", {}).get("sourceDir")))

        # 一级 · 历史默认名 origin 要自动迁到新名字：那不是用户的刻意选择，
        # 留着只会让老配置永远停在旧名字上
        cur["split"]["sourceDir"] = "origin"
        status, migrated = req("PUT", "/api/settings", cur)
        check("历史归档目录名 origin 自动迁移",
              (migrated or {}).get("split", {}).get("sourceDir")
              == "resize-video-origin-file",
              str((migrated or {}).get("split", {}).get("sourceDir")))

        # 二级 · 给监控目录单独设一套
        status, wp_move = req("PUT", "/api/watchpoints/%s" % wp_id,
                              {"markSource": "move", "sourceDir": "wp-archive"})
        check("监控目录存得下自己的原片处理方式",
              status == 200 and (wp_move or {}).get("markSource") == "move"
              and (wp_move or {}).get("sourceDir") == "wp-archive",
              str(wp_move)[:160])

        # 空串是「跟随系统」，必须原样存回去：补成具体值就再也升不了级了
        status, wp_follow = req("PUT", "/api/watchpoints/%s" % wp_id,
                                {"markSource": "", "sourceDir": ""})
        check("留空表示跟随，原样存回空串",
              status == 200 and (wp_follow or {}).get("markSource") == ""
              and (wp_follow or {}).get("sourceDir") == "",
              str(wp_follow)[:160])

        # 三级 · 手动扫描时临时指定一次。用 move 跑完整条链路，最后看原片是不是
        # 真的躺进了指定的归档子文件夹 —— 这同时证明了执行阶段用的是入队那一刻
        # 的快照，而不是当时设置里的 rename
        status, scan2 = req("POST", "/api/scan",
                            {"markSource": "move", "sourceDir": "smoke-archive"})
        check("手动扫描可以带本次指定的处理方式", status == 200, str(scan2)[:160])
        check("又入队 1 个任务", bool(scan2 and scan2["queued"] == 1),
              "queued=%s" % (scan2 or {}).get("queued"))

        job2 = None
        deadline = time.time() + 180
        while time.time() < deadline:
            status, listing = req("GET", "/api/jobs?limit=5")
            for item in (listing or {}).get("items", []):
                if item["id"] != job["id"]:
                    job2 = item
                    break
            if job2 and job2["status"] in ("success", "failed", "canceled"):
                break
            time.sleep(1)

        check("第二次任务完成", bool(job2 and job2["status"] == "success"),
              "status=%s error=%s" % ((job2 or {}).get("status"),
                                      (job2 or {}).get("error")))
        if job2:
            check("任务快照记下了本次指定的处理方式",
                  [job2["markSource"], job2["sourceDir"]]
                  == ["move", "smoke-archive"],
                  "markSource=%s sourceDir=%s"
                  % (job2["markSource"], job2["sourceDir"]))
        check("原片真的进了本次指定的归档子文件夹",
              (INBOX / "smoke-archive" / "test_clip.mp4").is_file())
        status, after = req("GET", "/api/settings")
        check("临时指定没有改掉系统设置",
              (after or {}).get("split", {}).get("markSource") == "rename",
              str((after or {}).get("split", {}).get("markSource")))

        # ---------------------------------------------------------- 清理接口
        print("\n[9] 任务记录清理")
        status, cleared = req("POST", "/api/jobs/clear",
                              {"statuses": ["success", "failed", "canceled"]})
        check("清理已完成任务返回 200", status == 200, str(cleared)[:120])
        status, listing = req("GET", "/api/jobs")
        check("任务列表已清空", bool(listing and listing["total"] == 0),
              "total=%s" % (listing or {}).get("total"))

    finally:
        try:
            server.terminate()
            server.wait(timeout=10)
        except Exception:
            try:
                server.kill()
            except Exception:
                pass

    print("\n" + "=" * 68)
    print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
    if FAILED:
        for name in FAILED:
            print("  失败：%s" % name)
    print("=" * 68)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

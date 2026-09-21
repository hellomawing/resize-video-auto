#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证「临时工作目录与输出目录同文件系统」这组改动。

跑法：
    python tools/verify_workdir.py

背景：分段是先写进临时工作目录、最后再 shutil.move 成最终文件名的。
跨文件系统的 move 不是改名而是真正的拷贝，整套切片会白搬一次——实测在这块
NAS 上让一次 6.5G 的切分从约 2 分钟变成 4 分半。所以工作目录必须和输出目录
在同一个文件系统上，并且不能被扫描器/实时监听当成新视频。

用例都在临时目录里造场景，不碰项目数据；真实切分用的是几秒钟的小视频。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import splitter as engine          # noqa: E402
from app.services import scanner             # noqa: E402

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  [OK]   %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s\n         期望：%r\n         实际：%r" % (name, want, got))


def check_true(name, cond, detail=""):
    check(name + (("　（%s）" % detail) if detail else ""), bool(cond), True)


def check_workdir_selection(tmp: Path) -> None:
    print("\n[1] make_workdir：工作目录必须和输出目录同一个文件系统")
    outdir = tmp / "输出目录"
    outdir.mkdir(parents=True)
    d = engine.make_workdir(outdir)
    check("和输出目录同一文件系统（决定 move 是改名还是拷贝）",
          os.stat(d).st_dev, os.stat(outdir).st_dev)
    check("落在输出目录的上一级（通常已在监控目录之外）", Path(d).parent, outdir.parent)
    check_true("名字带上了工作目录前缀",
               Path(d).name.startswith(engine.TMP_PREFIX), Path(d).name)

    sys_dev = os.stat(tempfile.gettempdir()).st_dev
    print("         （参考：系统临时目录 st_dev=%s，输出目录 st_dev=%s%s）"
          % (sys_dev, os.stat(outdir).st_dev,
             "　← 老做法跨了文件系统，这就是白搬一次的原因"
             if sys_dev != os.stat(outdir).st_dev else ""))
    shutil.rmtree(d, ignore_errors=True)


def check_fallback(tmp: Path) -> None:
    print("\n[2] 选址降级链：上一级不可写 -> 输出目录本身 -> 系统临时目录")
    outdir = tmp / "降级输出"
    outdir.mkdir(parents=True)
    real = engine.tempfile.mkdtemp
    created = []

    try:
        def no_parent(prefix=None, dir=None, **kw):
            if dir is not None and Path(dir) == outdir.parent:
                raise PermissionError("模拟上一级不可写")
            return real(prefix=prefix, dir=dir, **kw)

        engine.tempfile.mkdtemp = no_parent
        d = engine.make_workdir(outdir)
        created.append(d)
        check("上一级不可写时落到输出目录本身", Path(d).parent, outdir)
        check("仍然是同一文件系统", os.stat(d).st_dev, os.stat(outdir).st_dev)
    finally:
        engine.tempfile.mkdtemp = real

    try:
        def nowhere(prefix=None, dir=None, **kw):
            if dir is not None:
                raise PermissionError("模拟哪里都不可写")
            return real(prefix=prefix)

        engine.tempfile.mkdtemp = nowhere
        d = engine.make_workdir(outdir)
        created.append(d)
        check_true("两个候选都不可写时退回系统临时目录（与老版本一致）",
                   Path(d).name.startswith(engine.TMP_PREFIX), str(d))
        check("没有落在输出目录里", str(d).startswith(str(outdir)), False)
    finally:
        engine.tempfile.mkdtemp = real
        for d in created:
            shutil.rmtree(d, ignore_errors=True)


def check_sweep(tmp: Path) -> None:
    print("\n[3] 硬中断留下的旧工作目录要被清掉，正在用的不能碰")
    base = tmp / "清扫测试"
    outdir = base / "out"
    outdir.mkdir(parents=True)
    stale = base / (engine.TMP_PREFIX + "stale")
    fresh = base / (engine.TMP_PREFIX + "fresh")
    stale.mkdir()
    fresh.mkdir()
    (stale / "part_00001.mp4").write_bytes(b"\x00" * 4096)
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    d = engine.make_workdir(outdir)
    check("超过一天的残留被清掉", stale.exists(), False)
    check("不动的那些必须留着（可能是别的进程在用）", fresh.exists(), True)
    check("新工作目录建好了", Path(d).parent, base)
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(fresh, ignore_errors=True)


def check_identification(tmp: Path) -> None:
    print("\n[4] is_internal_temp：认得出工作目录里的临时分段")
    pre = engine.TMP_PREFIX
    check("工作目录里的分段", engine.is_internal_temp(
        tmp / "监控目录" / (pre + "ab12") / "part_00001.mp4"), True)
    check("普通视频不受影响", engine.is_internal_temp(tmp / "监控目录" / "真视频.mp4"), False)
    check("名字像但不是（少了点号）",
          engine.is_internal_temp(tmp / "监控目录" / ("vsplit-tmp-ab12") / "a.mp4"), False)
    check("切片的 #1 不受影响", engine.is_internal_temp(tmp / "a#1.mp4"), False)


def check_scanner_ignores(tmp: Path) -> None:
    print("\n[5] collect_files / consider_file 都不该理它")
    watch = tmp / "监控目录"
    work = watch / (engine.TMP_PREFIX + "ab12")
    work.mkdir(parents=True)
    (work / "part_00001.mp4").write_bytes(b"\x00" * 1024)
    (work / "part_00002.mp4").write_bytes(b"\x00" * 1024)
    (watch / "真视频.mp4").write_bytes(b"\x00" * 1024)

    seen: list = []
    files = engine.collect_files([str(watch)], {".mp4"}, True, set(),
                                 on_skip=lambda p, r: seen.append((p.name, r)))
    check("候选里只剩真视频", [p.name for p in files], ["真视频.mp4"])
    check("临时分段不该产生「跳过」汇报（那不是用户的文件）", seen, [])

    spec = {"exts": {".mp4"}, "min_size": 0, "all": True, "recursive": True,
            "settle": 0, "ignore_suffixes": (), "source_dir": "origin",
            "threshold": 1}
    status, reason = scanner.consider_file(work / "part_00001.mp4", spec, "manual")
    check("consider_file 的结果", status, "skipped")
    check_true("理由写明是临时分段", "临时分段" in reason, reason)
    status2, _ = scanner.consider_file(watch / "真视频.mp4", spec, "manual")
    check_true("普通视频不会被误挡（会被稳定检测拦下，属正常）",
               status2 in ("waiting", "queued"), status2)


def check_real_split(tmp: Path) -> None:
    print("\n[6] 真实切分：走一遍 ffmpeg，确认产物、命名与清理")
    ffmpeg = engine.find_bin("ffmpeg")
    ffprobe = engine.find_bin("ffprobe")
    if not (ffmpeg and ffprobe):
        print("  [跳过] 本机没有 ffmpeg / ffprobe")
        return

    outdir = tmp / "真实切分"
    outdir.mkdir(parents=True)
    src = outdir / "clip.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=25",
                    "-c:v", "mpeg4", str(src)], check=True)

    size = src.stat().st_size
    threshold = max(1, size // 2)
    produced, mode = engine.split_by_ffmpeg(
        src, threshold, outdir, False, False, ffmpeg, ffprobe, True)

    check("走的是流拷贝模式", mode, "copy")
    check_true("至少切出 2 段", len(produced) >= 2, "实际 %d 段" % len(produced))
    check("命名是 原名#序号", sorted(p.name for p, _ in produced)[0], "clip#1.mp4")
    check_true("每段都不超过阈值", all(s <= threshold for _, s in produced))
    check_true("切片合计接近原文件（差掉的是被跳过的附加流）",
               abs(sum(s for _, s in produced) - size) < size * 0.2,
               "%d / %d" % (sum(s for _, s in produced), size))
    check("切完后工作目录已清理干净",
          sorted(p.name for p in outdir.parent.glob(engine.TMP_PREFIX + "*")), [])
    check("输出目录里也没有残留",
          sorted(p.name for p in outdir.glob(engine.TMP_PREFIX + "*")), [])


def main() -> int:
    tmp = ROOT / ".tmp-verify-workdir"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        check_workdir_selection(tmp)
        check_fallback(tmp)
        check_sweep(tmp)
        check_identification(tmp)
        check_scanner_ignores(tmp)
        check_real_split(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 52)
    print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

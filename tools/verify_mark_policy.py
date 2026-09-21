#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证「原片处理方式」的三级设置。

跑法：
    python tools/verify_mark_policy.py

需求原话：原片处理方式（加 #origin 后缀留在原处 / 移到归档子文件夹 / 直接删除）
要能在三个地方设：系统设置里当默认值、自动扫描（监控目录）里按目录覆盖、
手动扫描时临时指定一次。

覆盖的内容：
  [1] normalize_source_dir：只允许单层目录名，历史默认名 origin 迁到新名字
  [2] resolve_mark_policy：本次手动 > 该监控目录 > 系统默认
  [3] collect_archive_dirs：把所有候选归档目录名收成一个集合
  [4] normalize_watchpoint：空串是「跟随」，不会被补成具体值
  [5] 老库迁移：没有这两列的历史库要能自动补上，老任务照样读得出
  [6] 入队快照：三级各自生效；入队之后再改设置不影响已排队的任务
  [7] retry：沿用原任务的快照，不因为中途改了设置而换一套行为
  [8] 扫描排除：归档目录里的原片不再被当成新视频（这是防重切的关键一环）
  [9] 归档目录判断只看「相对扫描根」的路径段：归档名撞上根之上的段名不误伤

全程只在临时目录里造场景，数据目录也指向临时区，不碰项目数据。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 数据目录必须在导入 app.config **之前**指走：DATA_DIR 是导入时算好的常量，
# 晚一步就会落到开发机的 data/ 上，把真实配置和任务表搅进去。
os.environ["VS_DATA_DIR"] = tempfile.mkdtemp(prefix="vs-mark-data-")

from app import config                       # noqa: E402
from app import db                           # noqa: E402
from app.services import queue as job_queue  # noqa: E402
from app.services import scanner             # noqa: E402
from core import splitter as engine          # noqa: E402

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


# ---------------------------------------------------------------- [1]

def check_normalize_dir() -> None:
    print("\n[1] normalize_source_dir：归档目录名只允许是单层目录名")
    d = config.DEFAULT_SOURCE_DIR
    check("默认名本身不变", config.normalize_source_dir(d), d)
    check("空值退回默认名", config.normalize_source_dir(""), d)
    check("None 退回默认名", config.normalize_source_dir(None), d)
    check("只有空白也退回默认名", config.normalize_source_dir("   "), d)
    check("历史默认名 origin 迁到新名", config.normalize_source_dir("origin"), d)
    check("大小写变体同样迁走", config.normalize_source_dir("Origin"), d)
    check("首尾斜杠被剥掉", config.normalize_source_dir("/归档/"), "归档")
    check("反斜杠也当分隔符", config.normalize_source_dir("a\\b"), "b")
    check("多层只取最后一段", config.normalize_source_dir("origin/old"), "old")
    check("指回自身就退回默认名", config.normalize_source_dir("."), d)
    check("指回上级也退回默认名", config.normalize_source_dir(".."), d)
    check("自定义名原样保留", config.normalize_source_dir("my-archive"), "my-archive")


# ---------------------------------------------------------------- [2]

def check_resolve_priority() -> None:
    print("\n[2] resolve_mark_policy：本次手动 > 该监控目录 > 系统默认")
    settings = {"split": {"markSource": "rename", "sourceDir": "sys-archive"}}
    wp = {"markSource": "move", "sourceDir": "wp-archive"}

    check("什么都没有 -> 系统默认",
          config.resolve_mark_policy(settings), 
          {"markSource": "rename", "sourceDir": "sys-archive"})
    check("只有监控目录 -> 目录压过系统",
          config.resolve_mark_policy(settings, wp),
          {"markSource": "move", "sourceDir": "wp-archive"})
    check("本次手动压过监控目录",
          config.resolve_mark_policy(settings, wp, {"markSource": "delete"}),
          {"markSource": "delete", "sourceDir": "wp-archive"})
    check("本次手动也能单独改归档目录名",
          config.resolve_mark_policy(settings, wp,
                                     {"markSource": "move",
                                      "sourceDir": "once-archive"}),
          {"markSource": "move", "sourceDir": "once-archive"})

    # 「跟随」是空串：该层不表态，就往下一层要，不能把空串当成一个取值
    follow = {"markSource": "", "sourceDir": ""}
    check("监控目录留空串 -> 退回系统默认",
          config.resolve_mark_policy(settings, follow),
          {"markSource": "rename", "sourceDir": "sys-archive"})
    check("本次留空 -> 退回监控目录",
          config.resolve_mark_policy(settings, wp, follow),
          {"markSource": "move", "sourceDir": "wp-archive"})

    # 老任务的快照可能是 None（新增列之前入的队），必须安全退到设置
    check("两级都是 None 也不炸",
          config.resolve_mark_policy(settings, {"markSource": None,
                                                "sourceDir": None},
                                     {"markSource": None, "sourceDir": None}),
          {"markSource": "rename", "sourceDir": "sys-archive"})
    check("系统设置被写坏时退回内置默认",
          config.resolve_mark_policy({"split": {"markSource": "乱写的",
                                                "sourceDir": ""}}),
          {"markSource": config.DEFAULT_MARK_SOURCE,
           "sourceDir": config.DEFAULT_SOURCE_DIR})


# ---------------------------------------------------------------- [3]

def check_archive_dirs() -> None:
    print("\n[3] collect_archive_dirs：候选归档目录名要收成一个集合")
    settings = {"split": {"sourceDir": "sys-archive"}}
    wps = [{"sourceDir": "wp-a"}, {"sourceDir": ""}, {"sourceDir": "wp-b"}]

    got = config.collect_archive_dirs(settings, wps)
    check("系统默认名在里面", "sys-archive" in got, True)
    check("各监控目录的名字都在里面", {"wp-a", "wp-b"} <= got, True)
    check("历史默认名 origin 也算候选", list(config.LEGACY_SOURCE_DIRS)[0] in got, True)
    check("空串不会被当成一个目录名", "" in got, False)
    check("本次覆盖指定的也要算进去",
          "once-x" in config.collect_archive_dirs(settings, wps, ["once-x"]), True)

    # 一个目录用 origin、另一个用别的名字时，扫 A 也得排除 B 的名字，
    # 否则被 B 归档走的原片会在 A 里被当成新视频再切一遍
    legacy = config.collect_archive_dirs({"split": {"sourceDir": "origin"}}, [])
    check_true("历史名 origin 会被换成新默认名",
               config.DEFAULT_SOURCE_DIR in legacy, sorted(legacy))
    check("非法名字按规则纠正后再入集合",
          config.collect_archive_dirs({"split": {"sourceDir": "/a/b/"}}, []) >= {"b"}, True)


# ---------------------------------------------------------------- [4]

def check_normalize_watchpoint() -> None:
    print("\n[4] normalize_watchpoint：空串是「跟随」，不能被补成具体值")
    wp = config.normalize_watchpoint({"id": "w1", "path": "/x"})
    check("没写 markSource 就是空串（跟随）", wp["markSource"], "")
    check("没写 sourceDir 也是空串（跟随）", wp["sourceDir"], "")

    wp2 = config.normalize_watchpoint({"id": "w2", "path": "/x",
                                       "markSource": "delete",
                                       "sourceDir": "origin"})
    check("写了就照实留着", wp2["markSource"], "delete")
    check("归档目录名顺手做了迁移", wp2["sourceDir"], config.DEFAULT_SOURCE_DIR)

    wp3 = config.normalize_watchpoint({"id": "w3", "path": "/x",
                                       "markSource": "乱写"})
    check("非法取值退回空串而不是默认值", wp3["markSource"], "")


# ---------------------------------------------------------------- [5]

def check_legacy_db_migration(tmp: Path) -> None:
    print("\n[5] 老库迁移：新增列要能自动补上")
    config.ensure_dirs()
    db.close_db()
    db_path = config.DB_PATH
    if db_path.exists():
        db_path.unlink()

    # 从当前 SCHEMA 里去掉这次新增的两列，还原出「升级前」的建表语句。
    # 这样做的好处是别的列以后再有变动，这个用例也不会失真。
    old_schema = db.SCHEMA
    for col in ("mark_source", "source_dir"):
        line = "    %s" % col
        hit = [ln for ln in old_schema.splitlines() if ln.strip().startswith(col)]
        check_true("SCHEMA 里找得到 %s 那一行" % col, bool(hit))
        if hit:
            old_schema = old_schema.replace(hit[0] + "\n", "")

    conn = sqlite3.connect(str(db_path))
    conn.executescript(old_schema)
    conn.execute(
        "INSERT INTO jobs (id, src, src_name, src_size, status, progress,"
        " parts_total, parts_done, created_at)"
        " VALUES ('old-1','/x/a.mp4','a.mp4',100,'failed',0,0,0,'2020-01-01T00:00:00')")
    conn.commit()
    before = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    conn.close()
    check("老库确实没有新列", "mark_source" in before, False)

    db.init_db()

    after = {r[1] for r in db.get_conn().execute("PRAGMA table_info(jobs)")}
    check("迁移补上了 mark_source", "mark_source" in after, True)
    check("迁移补上了 source_dir", "source_dir" in after, True)

    old_job = db.get_job("old-1")
    check("老任务读得出来", old_job["id"], "old-1")
    check("老任务的处理方式是空的（当时还没有这个字段）",
          [old_job["markSource"], old_job["sourceDir"]], [None, None])

    # 幂等：再跑一遍不能报错，也不能把已有数据弄坏
    db._migrate(db.get_conn())
    check("再跑一次迁移也没事", db.get_job("old-1")["status"], "failed")


# ---------------------------------------------------------------- [6]

def check_enqueue_snapshot(tmp: Path) -> None:
    print("\n[6] 入队快照：三级各自生效，改设置不影响已排队的任务")
    config.save_settings({"split": {"markSource": "rename", "sourceDir": "sys-archive"}})
    config.save_watchpoints([{"id": "wp1", "path": str(tmp), "scanMode": "manual",
                              "markSource": "move", "sourceDir": "wp-archive"}])

    def make(name: str) -> Path:
        p = tmp / name
        p.write_bytes(b"\x00" * 2048)
        return p

    # 一级：没有监控目录、没有本次指定 -> 系统默认
    j1, why1 = job_queue.enqueue(make("a.mp4"), trigger="manual")
    check_true("入队成功（系统默认那一路）", j1 is not None, why1)
    check("走系统默认", [j1["markSource"], j1["sourceDir"]], ["rename", "sys-archive"])

    # 二级：该监控目录自己的设置
    j2, why2 = job_queue.enqueue(make("b.mp4"), trigger="manual", watchpoint_id="wp1")
    check_true("入队成功（监控目录那一路）", j2 is not None, why2)
    check("走监控目录设置", [j2["markSource"], j2["sourceDir"]], ["move", "wp-archive"])

    # 三级：本次手动指定
    j3, why3 = job_queue.enqueue(make("c.mp4"), trigger="manual", watchpoint_id="wp1",
                                 mark_override={"markSource": "delete"})
    check_true("入队成功（本次指定那一路）", j3 is not None, why3)
    check("本次指定压过监控目录", [j3["markSource"], j3["sourceDir"]],
          ["delete", "wp-archive"])

    # 改系统设置：已入队的任务必须守住入队时那一刻的取值
    config.save_settings({"split": {"markSource": "delete", "sourceDir": "改过的"}})
    check("改系统设置后，老任务快照不变",
          [db.get_job(j1["id"])["markSource"], db.get_job(j1["id"])["sourceDir"]],
          ["rename", "sys-archive"])
    # 但之后新入队的要跟着新设置走
    j4, _ = job_queue.enqueue(make("d.mp4"), trigger="manual")
    check("之后新入队的按新设置",
          [j4["markSource"], j4["sourceDir"]], ["delete", "改过的"])

    # 快照必须真的落进数据库，不能只活在内存里
    row = db.get_conn().execute(
        "SELECT mark_source, source_dir FROM jobs WHERE id=?", (j3["id"],)).fetchone()
    check("快照写进了 SQLite", [row[0], row[1]], ["delete", "wp-archive"])
    check("对外 JSON 是 camelCase",
          [db.get_job(j3["id"])["markSource"], db.get_job(j3["id"])["sourceDir"]],
          ["delete", "wp-archive"])

    return j3


# ---------------------------------------------------------------- [7]

def check_retry_keeps_snapshot(job: dict) -> None:
    print("\n[7] retry：沿用原任务的快照，不按现在的设置换一套行为")
    db.update_job(job["id"], status="failed")
    config.save_settings({"split": {"markSource": "rename", "sourceDir": "又改过"}})

    ok, msg, new_job = job_queue.retry(job["id"])
    check_true("重试成功", ok, msg)
    check("沿用原来的处理方式（而不是用新设置）",
          [new_job["markSource"], new_job["sourceDir"]], ["delete", "wp-archive"])
    check("重试是新建一条记录，历史还在", new_job["id"] != job["id"], True)
    check("原记录没被动", db.get_job(job["id"])["id"], job["id"])

    # 老任务（快照为 NULL）重试时要能安全退回设置，不能报错
    old_src = Path(job["src"]).parent / "e.mp4"
    old_src.write_bytes(b"\x00" * 2048)
    db.update_job("old-1", status="failed", src=str(old_src))
    ok2, msg2, job2 = job_queue.retry("old-1")
    check_true("快照为空的老任务也能重试", ok2, msg2)
    check("退回当时的设置", [job2["markSource"], job2["sourceDir"]],
          ["rename", "又改过"])


# ---------------------------------------------------------------- [8]

def check_scan_excludes_archive(tmp: Path) -> None:
    print("\n[8] 扫描排除：归档目录里的原片不该被当成新视频")
    # 先固定一套设置，别让前面用例改来改去的取值影响这一组判断
    config.save_settings({"split": {"markSource": "move", "sourceDir": "sys-archive"}})
    config.save_watchpoints([{"id": "wp1", "path": str(tmp), "scanMode": "manual",
                              "markSource": "move", "sourceDir": "wp-archive"}])
    spec = scanner.build_spec(config.load_settings())
    # 用例造的是几 KB 的小文件，把阈值压到 1 字节：这样才能把「没被排除」
    # 的文件推到入队分支上去。否则它会被「未超过大小阈值」挡下，
    # 排除是否真的生效就根本测不出来。
    spec["threshold"] = 1
    spec["min_size"] = 0

    arch = spec["archive_dirs"]
    check_true("spec 带上了 archive_dirs", isinstance(arch, set), type(arch).__name__)
    check_true("系统设置里的名字在排除集里", "sys-archive" in arch, sorted(arch))
    check_true("监控目录用的名字也在（换个目录扫时同样要排除）",
               "wp-archive" in arch, sorted(arch))
    # 排除集里还混着「用过的名字」的历史记录（见 [10]），所以上面两条单独看已经
    # 证明不了「按当前配置现算」这条路还通——用一个全新的名字单独钉一下
    check_true("当前配置里新写的名字会立刻进排除集",
               "sys-archive-8" in config.collect_archive_dirs(
                   {"split": {"sourceDir": "sys-archive-8"}}, []))

    # move 模式下原片会带着原文件名躺进归档目录
    (tmp / "wp-archive").mkdir(exist_ok=True)
    (tmp / "sys-archive").mkdir(exist_ok=True)
    (tmp / "wp-archive" / "b.mp4").write_bytes(b"\x00" * 4096)
    (tmp / "sys-archive" / "a.mp4").write_bytes(b"\x00" * 4096)

    check("监控目录的归档子目录被排除",
          scanner.consider_file(tmp / "wp-archive" / "b.mp4", spec, "manual"),
          ("skipped", "位于原片归档目录"))
    check("系统默认的归档目录同样被排除",
          scanner.consider_file(tmp / "sys-archive" / "a.mp4", spec, "manual"),
          ("skipped", "位于原片归档目录"))

    # 本次手动指定的归档目录名也要一并排除，
    # 否则「这一次」就会把刚归档过去的原片又切一遍
    spec2 = scanner.build_spec(config.load_settings(),
                               extra_archive_dirs=["once-archive"])
    spec2["threshold"] = 1
    (tmp / "once-archive").mkdir(exist_ok=True)
    (tmp / "once-archive" / "z.mp4").write_bytes(b"\x00" * 4096)
    check("本次指定的归档目录也被排除",
          scanner.consider_file(tmp / "once-archive" / "z.mp4", spec2, "manual")[0],
          "skipped")

    # 归档目录之外的文件照常处理：别为了防重切把正常文件也一起挡了
    (tmp / "normal.mp4").write_bytes(b"\x00" * 4096)
    normal_status = scanner.consider_file(tmp / "normal.mp4", spec, "manual")[0]
    check_true("归档目录之外的文件不受影响",
               normal_status in ("queued", "waiting"), normal_status)


def check_relative_archive_scope(tmp: Path) -> None:
    """
    [9] 归档目录判断只看「相对扫描根」的路径段。

    老写法拿绝对路径的**全部**段去比，于是 `/vol1/1000/...` 里的 `1000`
    会被当成归档目录名。归档目录一旦取成这类普通字眼，整棵目录树都会被判为
    「位于原片归档目录」，表现是「扫描完说没有视频」——而给的理由看着还挺
    合理，很难联想到是名字撞的。

    这里把扫描根埋在 `outmost/mid/inbox` 三层之下，再拿上层的段名当归档名，
    正对着这个坑测。
    """
    print("\n[9] 归档目录判断只看相对扫描根的路径段")
    root = tmp / "scope" / "outmost" / "mid" / "inbox"
    (root / "origin" / "nested").mkdir(parents=True, exist_ok=True)
    (root / "sub" / "origin").mkdir(parents=True, exist_ok=True)
    (root / "demo.mp4").write_bytes(b"\x00" * 4096)
    (root / "origin" / "archived.mp4").write_bytes(b"\x00" * 4096)
    (root / "origin" / "nested" / "x.mp4").write_bytes(b"\x00" * 4096)
    (root / "sub" / "inner.mp4").write_bytes(b"\x00" * 4096)
    (root / "sub" / "origin" / "deep.mp4").write_bytes(b"\x00" * 4096)

    hit = engine.is_in_archive_dir
    check_true("根下的普通文件：不算归档", not hit(root / "demo.mp4", root, {"origin"}))
    check_true("根下的归档子目录：算",
               hit(root / "origin" / "archived.mp4", root, {"origin"}))
    check_true("归档目录再深几层：仍算",
               hit(root / "origin" / "nested" / "x.mp4", root, {"origin"}))
    check_true("子目录里的归档目录：算",
               hit(root / "sub" / "origin" / "deep.mp4", root, {"origin"}))
    check_true("普通子目录里的文件：不算",
               not hit(root / "sub" / "inner.mp4", root, {"origin"}))
    check_true("文件名本身撞上归档名：不算",
               not hit(root / "origin.mp4", root, {"origin.mp4"}))
    check_true("路径不在这个根之下：不算",
               not hit(tmp / "elsewhere.mp4", root, {"origin"}))

    # ★ 核心回归：归档名撞上「扫描根之上」的路径段，不能再误伤
    check_true("归档名 = 根之上的一层（outmost）：不误伤",
               not hit(root / "demo.mp4", root, {"outmost"}))
    check_true("归档名 = 根之上的一层（mid）：不误伤",
               not hit(root / "demo.mp4", root, {"mid"}))
    check_true("归档名 = 扫描根自己的名字：不误伤",
               not hit(root / "demo.mp4", root, {"inbox"}))
    # 对照：同一份数据上，老写法就是会把这些普通文件判成归档
    check_true("（对照）老写法会把 demo.mp4 判成归档",
               any(p in {"outmost", "mid", "inbox"} for p in (root / "demo.mp4").parts))

    # collect_files 这条真实路径也要跟着变
    spec = scanner.build_spec(config.load_settings())
    spec["archive_dirs"] = set(spec["archive_dirs"]) | {"origin", "outmost", "mid"}
    files = engine.collect_files([root], {".mp4"}, True, spec["archive_dirs"])
    check("collect_files 只放过该处理的",
          sorted(p.name for p in files), ["demo.mp4", "inner.mp4"])

    # consider_file 带上 roots 之后同样生效（扫描侧会把扫描根传进来）
    spec["threshold"] = 1
    spec["min_size"] = 0
    check_true("带 roots：普通文件不被误伤",
               scanner.consider_file(root / "demo.mp4", spec, "manual",
                                     roots=[root])[0] in ("queued", "waiting"))
    check("带 roots：归档目录里的照旧排除",
          scanner.consider_file(root / "origin" / "archived.mp4", spec, "manual",
                                roots=[root]),
          ("skipped", "位于原片归档目录"))
    check("带 roots：子目录里的归档目录也排除",
          scanner.consider_file(root / "sub" / "origin" / "deep.mp4", spec,
                                "manual", roots=[root]),
          ("skipped", "位于原片归档目录"))


def check_archive_dirs_persist(tmp: Path) -> None:
    """
    [10] 用过的归档目录名要落盘、只增不减。

    `move` 模式归档过去的原片**保留原文件名**。排除集如果只按当前配置现算，
    名字一改（或清空回「跟随系统」）它就从集合里掉出去，躺在旧归档目录里的原片
    会被当成新视频重切一遍再标记一次 —— 2026-09-21 真机可稳定复现：
    清空监控目录的 sourceDir 后，本次扫描多入队一条 src 指向 live-archive/ 的任务。
    """
    print("\n[10] 用过的归档目录名：落盘、只增不减")

    sfile = config.ARCHIVE_DIRS_PATH
    check_true("记录文件落在数据目录里",
               str(sfile).startswith(str(config.DATA_DIR)), str(sfile))

    # 只在这一节里出现的名字，免得被前面对用例写进去的记录干扰
    used, newer = "once-move-a", "once-move-b"
    base = {"split": {"sourceDir": config.DEFAULT_SOURCE_DIR}}
    wp_with = lambda name: [{"id": "w", "sourceDir": name}]      # noqa: E731

    first = config.collect_archive_dirs(base, wp_with(used))
    check_true("用过的名字进了排除集", used in first, sorted(first))

    second = config.collect_archive_dirs(base, wp_with(newer))
    check_true("改名字之后，老名字仍被排除", used in second, sorted(second))
    check_true("新名字也在", newer in second, sorted(second))

    third = config.collect_archive_dirs(base, wp_with(""))
    check_true("清空回「跟随系统」后，老名字仍被排除", used in third, sorted(third))

    saved = json.loads(sfile.read_text(encoding="utf-8"))
    check_true("已落盘成 archive-dirs.json",
               isinstance(saved, list) and used in saved and newer in saved, saved[:8])

    # 这个函数在扫描热路径上（每轮轮询、每次扫描都会走到），没新名字就别动文件
    before = os.stat(sfile).st_mtime_ns
    config.collect_archive_dirs(base, wp_with(used))
    check("没有新名字时不动文件", os.stat(sfile).st_mtime_ns, before)

    # 对照：当初的缺陷正是「只按当前配置现算」，老名字根本不在候选里
    from_current = set(config.LEGACY_SOURCE_DIRS) | {
        config.normalize_source_dir(base["split"]["sourceDir"])}
    check_true("（对照）只按当前配置现算，老名字已经不在候选里",
               used not in from_current, sorted(from_current))

    # 反过来也要保证：持久化没有把「按当前配置现算」这条路盖掉
    fresh_name = "brand-new-archive"
    check_true("配置里新写的名字立刻就生效",
               fresh_name in config.collect_archive_dirs(
                   {"split": {"sourceDir": fresh_name}}, []))

    # 落到磁盘上的真实效果：旧归档目录里的原片不再被当成新视频
    root = tmp / "persist" / "inbox"
    (root / used).mkdir(parents=True, exist_ok=True)
    (root / "demo.mp4").write_bytes(b"\x00" * 4096)
    (root / used / "archived.mp4").write_bytes(b"\x00" * 4096)
    config.save_settings({"split": {"markSource": "move",
                                    "sourceDir": config.DEFAULT_SOURCE_DIR}})
    config.save_watchpoints([{"id": "wp-persist", "path": str(root),
                              "scanMode": "manual"}])
    spec = scanner.build_spec(config.load_settings())
    spec["threshold"] = 1        # 用例造的是几 KB 的小文件，见 [8] 的说明
    spec["min_size"] = 0
    check("旧归档目录里的原片被排除（不会再重切一遍）",
          scanner.consider_file(root / used / "archived.mp4", spec, "manual",
                                roots=[root]),
          ("skipped", "位于原片归档目录"))
    check_true("同一个目录下的普通文件照常处理",
               scanner.consider_file(root / "demo.mp4", spec, "manual",
                                     roots=[root])[0] in ("queued", "waiting"))


def main() -> int:
    db.init_db()
    tmp = Path(tempfile.mkdtemp(prefix="vs-mark-"))
    try:
        check_normalize_dir()
        check_resolve_priority()
        check_archive_dirs()
        check_normalize_watchpoint()
        check_legacy_db_migration(tmp)
        job = check_enqueue_snapshot(tmp)
        check_retry_keeps_snapshot(job)
        check_scan_excludes_archive(tmp)
        check_relative_archive_scope(tmp)
        check_archive_dirs_persist(tmp)
    finally:
        db.close_db()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 52)
    print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

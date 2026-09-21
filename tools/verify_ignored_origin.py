#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证「已处理文件如实报告」与「孤儿原片可见可恢复」两组改动。

跑法：
    python tools/verify_ignored_origin.py

全部在临时目录里造场景，不碰项目数据，也不写任何真实配置。
用例都造得远小于切分阈值，所以不会有文件真被入队。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import splitter as engine          # noqa: E402
from core import undo                        # noqa: E402
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


def build_scene(tmp: Path) -> None:
    """造一个「该被跳过的、该被处理的、只剩原片的」都有的目录。"""
    for name in ("A#origin.MP4", "A#1.MP4", "A#2.MP4"):
        (tmp / name).write_bytes(b"\x00" * 1024)     # 完整的一组，可撤销
    (tmp / "B#origin.MP4").write_bytes(b"\x00" * 1024)   # 切片被删光了
    (tmp / "C.MP4").write_bytes(b"\x00" * 1024)          # 正常待处理
    (tmp / "空目录").mkdir(exist_ok=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vs-verify-"))
    try:
        build_scene(tmp)
        print("\n临时场景：%s" % tmp)

        # --------------------------------------------------------- 收集阶段
        print("\n[1] collect_files：老行为必须一字不变")
        plain = engine.collect_files([tmp], {".mp4"}, True, {"origin"})
        check("不传 on_skip 时只返回待处理文件", [p.name for p in plain], ["C.MP4"])

        seen = []
        same = engine.collect_files([tmp], {".mp4"}, True, {"origin"},
                                    on_skip=lambda p, r: seen.append(p.name))
        check("传了 on_skip 也不改变返回结果", [p.name for p in same], ["C.MP4"])
        check("on_skip 报出的数量", len(seen), 4)
        check_true("报出了已标记的原片", "A#origin.MP4" in seen)
        check_true("报出了切片", "A#1.MP4" in seen and "A#2.MP4" in seen)
        check_true("报出了只剩原片的那个", "B#origin.MP4" in seen)

        # --------------------------------------------------- 切片 vs 原片
        print("\n[1b] classify_own_product：切片和原片不是一回事")
        check("切片", engine.classify_own_product(Path("A#1.MP4")), "slice")
        check("原片", engine.classify_own_product(Path("A#origin.MP4")), "origin")
        check("普通文件", engine.classify_own_product(Path("A.MP4")), None)
        check("大小写不敏感（SMB 上 #origin 可能变 #ORIGIN）",
              engine.classify_own_product(Path("A#ORIGIN.MP4")), "origin")
        check("名字里带 # 但结尾不是索引，不算产物",
              engine.classify_own_product(Path("A#b.MP4")), None)
        check("多段 # 看最后一个", engine.classify_own_product(Path("a#b#1.MP4")), "slice")
        check("is_slice_or_origin 与分类同源（行为不变）",
              (engine.is_slice_or_origin(Path("A#1.MP4")),
               engine.is_slice_or_origin(Path("A.MP4"))), (True, False))

        print("\n[1c] product_base：把切片和它的原片对上号")
        check("切片的原名", engine.product_base(Path("A#1.MP4")), ("A", ".MP4"))
        check("原片的原名", engine.product_base(Path("A#origin.MP4")), ("A", ".MP4"))
        check("多段 # 的切片", engine.product_base(Path("a#b#2.MP4")), ("a#b", ".MP4"))
        check("普通文件没有原名", engine.product_base(Path("A.MP4")), None)

        # --------------------------------------------------------- 扫描结果
        print("\n[2] scan_paths：跳过要说清楚，而不是装作没看见")
        r = scanner.scan_paths([str(tmp)], "manual")
        check("found 仍是候选文件数", r["found"], 1)
        check("ignoredTotal 统计了全部被跳过项", r["ignoredTotal"], 4)
        check("ignored 明细条数", len(r["ignored"]), 4)
        check("每条都带名字/原因/类型/能否重切",
              sorted(r["ignored"][0].keys()),
              ["kind", "name", "reason", "resettable"])
        check("切片和原片分得开",
              sorted(i["kind"] for i in r["ignored"]),
              ["origin", "origin", "slice", "slice"])
        check("只有 B 的原片被判为可重切",
              [i["name"] for i in r["ignored"] if i["resettable"]], ["B#origin.MP4"])
        check("resettableTotal", r["resettableTotal"], 1)
        check("resettableDirs 带着目录与扫描范围",
              r["resettableDirs"], [{"path": str(tmp), "recursive": True}])
        check_true("提示语点明了可以重新分割",
                   "可以重新分割" in r["message"], r["message"])
        check_true("正常跳过的仍按「已切分完成」措辞",
                   "另有 3 个是已切分完成的产物" in r["message"], r["message"])
        check("汇总文案把同类原因合并",
              scanner.summarize_ignored(
                  [i for i in r["ignored"] if i["kind"] == "slice"]),
              "2 个是本工具切出来的切片")
        print("         提示语全文：%s" % r["message"])

        print("\n[2b] 切片已不在的原片，不该只说「跳过」")
        marked = tmp / "只有原片"
        marked.mkdir()
        (marked / "X#origin.MP4").write_bytes(b"\x00" * 1024)
        rm = scanner.scan_paths([str(marked)], "manual")
        check("found=0", rm["found"], 0)
        check("但如实报出跳过了 1 个", rm["ignoredTotal"], 1)
        check("认出了它其实可以重切", rm["resettableTotal"], 1)
        check_true("说清了原因——切片已不在",
                   "切片被删除或移走" in rm["message"], rm["message"])
        check_true("也说了它能救回来",
                   "可以重新分割" in rm["message"], rm["message"])

        print("\n[2b2] 切片还在的原片，仍按「已完成」跳过（关键回归）")
        intact = tmp / "完整组"
        intact.mkdir()
        for n in ("Y#origin.MP4", "Y#1.MP4"):
            (intact / n).write_bytes(b"\x00" * 1024)
        ri = scanner.scan_paths([str(intact)], "manual")
        check("跳过了 2 个", ri["ignoredTotal"], 2)
        check("但没有一个被误判成可重切", ri["resettableTotal"], 0)
        check("resettableDirs 为空", ri["resettableDirs"], [])
        check_true("提示语里不该出现「可以重新分割」",
                   "可以重新分割" not in ri["message"], ri["message"])

        print("\n[2c] 目录里真的什么都没有时，不该提跳过")
        empty = tmp / "空目录"
        r2 = scanner.scan_paths([str(empty)], "manual")
        check("found=0 且 ignoredTotal=0", (r2["found"], r2["ignoredTotal"]), (0, 0))
        check("文案回到老样子", r2["message"], "扫描完成：没有需要处理的新视频")

        # 上面几个临时目录用完就拆，免得被后面的 scan_groups 一起扫进去
        shutil.rmtree(marked)
        shutil.rmtree(empty)
        shutil.rmtree(intact)

        # --------------------------------------------------------- 撤销侧
        print("\n[3] scan_groups：孤立的原片必须被发现")
        groups, orphans, origin_only = undo.scan_groups(tmp, True, "origin", None)
        check("可撤销的组数", len(groups), 1)
        check("组名", groups[0]["base"], "A")
        check("孤儿切片数", len(orphans), 0)
        check("只剩原片的数量", len(origin_only), 1)
        check("它叫什么", origin_only[0]["name"], "B#origin.MP4")
        check("报出了原始名（恢复时要用）", origin_only[0]["base"], "B")
        check_true("带了体积", origin_only[0]["size"] == 1024)
        check_true("带了时间", bool(origin_only[0]["mtime"]))

        print("\n[4] 躺在归档目录里的原片，不该被当成孤儿")
        arch = tmp / "归档"
        (arch / "origin").mkdir(parents=True)
        (arch / "origin" / "D.MP4").write_bytes(b"\x00" * 512)
        g2, o2, lo2 = undo.scan_groups(arch, True, "origin", None)
        check("归档原片不进 origin_only", len(lo2), 0)

        # --------------------------------------------------------- 恢复动作
        print("\n[5] apply_undo：默认不该擅自动它们")
        rep = undo.apply_undo(tmp, recursive=True, source_dir="origin",
                              delete_slices=False, restore=False,
                              restore_origin_only=False, trash=False, ffprobe=None)
        check("没恢复任何孤儿", rep["restoredOrphans"], 0)
        check_true("原片还带着标记", (tmp / "B#origin.MP4").exists())

        print("\n[6] apply_undo：明确要求时才恢复原名")
        rep = undo.apply_undo(tmp, recursive=True, source_dir="origin",
                              delete_slices=False, restore=False,
                              restore_origin_only=True, trash=False, ffprobe=None)
        check("恢复了 1 个", rep["restoredOrphans"], 1)
        check("详情里不留空记录（正常组不该出现 noop）",
              [d for d in rep["details"] if not d["message"]], [])
        check_true("文件已改名回原名", (tmp / "B.MP4").exists())
        check_true("带标记的那个已经不在了", not (tmp / "B#origin.MP4").exists())
        check("恢复后目录里两个待处理文件",
              sorted(p.name for p in engine.collect_files(
                  [tmp], {".mp4"}, True, {"origin"})), ["B.MP4", "C.MP4"])

        print("\n[7] 恢复时绝不覆盖同名文件")
        (tmp / "E#origin.MP4").write_bytes(b"\x00" * 256)
        (tmp / "E.MP4").write_bytes(b"\x00" * 256)
        rep = undo.apply_undo(tmp, recursive=True, source_dir="origin",
                              delete_slices=False, restore=False,
                              restore_origin_only=True, trash=False, ffprobe=None)
        check("同名时拒绝恢复", rep["restoredOrphans"], 0)
        check_true("原来的 E.MP4 没被动过", (tmp / "E.MP4").stat().st_size == 256)
        check_true("问题被如实报出",
                   any("E" in p and "已存在" in p for p in rep["problems"]),
                   str(rep["problems"]))

        # --------------------------------------------------------- 模型转换
        print("\n[8] ScanResult.from_engine 两个入口共用")
        from app.models import ScanResult
        m = ScanResult.from_engine(r)
        check("字段对得上", (m.found, m.ignored_total, len(m.ignored)), (1, 4, 4))
        check("可重切的信息也转过去了",
              (m.resettable_total, len(m.resettable_dirs)), (1, 1))
        check("明细转成了模型",
              (m.ignored[0].name, bool(m.ignored[0].reason))[1], True)
        check("可重切的项排在明细最前面（否则会被切片名挤掉）",
              m.ignored[0].resettable, True)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 52)
    print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监控目录「过滤规则」的回归验证。

每个监控目录可以配一套自己的规则：只处理某几种类型、不要某几种类型、
只处理（文件名或某一级文件夹名）命中某些写法的、以及排除命中的。
写法有两种 —— 「包含某串」（忽略大小写）与「正则表达式」。

这份脚本盯住的是几件容易做错的事：

  1. 类型收窄到「引擎能无损切分的格式」，连写错格式的入口都要堵死
     （库里躺着 .avi 这种扫描放行、切分照样拦下的死配置最坑人）
  2. 正则在 API 入口严格报错、在读盘时静默剔除 —— 两层各管一件事：
     入口要让人当场知道写错了，读盘不能让一条手改坏的规则搞挂扫描
  3. 比对口径是「文件自身名字 + 监控目录之下各级文件夹名」，不含更上游的
     路径（否则规则里写个 1000 会把 /vol1/1000/... 下的一切都命中）
  4. 排除优先于仅限
  5. 命中排除规则的目录整棵不进入（纯性能优化，但结果必须与「进去后再逐个
     判断」完全一致 —— 所以还要验证被剪掉的文件在跳过明细里**不出现**）
  6. 空规则 = 不过滤：老监控目录升级后行为一字不变

    python tools/verify_filters.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="vs-filters-"))
os.environ["VS_DATA_DIR"] = str(TMP / "data")

from app import config, db                                  # noqa: E402

config.ensure_dirs()
db.init_db()

from core import splitter as engine                         # noqa: E402
from app.services import filters as fmod                    # noqa: E402

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


# ---------------------------------------------------------------- 素材

MEDIA = TMP / "media"
CAM = MEDIA / "相机导入" / "2026"
BLOOPERS = MEDIA / "花絮"
OTHER = MEDIA / "其他"
for d in (CAM, BLOOPERS, OTHER):
    d.mkdir(parents=True)

FILES = {
    CAM / "a.mp4": b"x" * 100,
    MEDIA / "相机导入" / "b.ts": b"x" * 100,
    BLOOPERS / "c.mp4": b"x" * 100,
    OTHER / "d.mov": b"x" * 100,
    MEDIA / "e.mp4": b"x" * 100,
    MEDIA / "SONY-相机-2.mkv": b"x" * 100,
}
for p, content in FILES.items():
    p.write_bytes(content)

RULES = {
    "extInclude": [".mp4", ".mkv"],
    "extExclude": [".ts"],
    "nameInclude": [{"mode": "contains", "value": "相机"}],
    "nameExclude": [{"mode": "contains", "value": "花絮"}],
}

# ---------------------------------------------------------------- 1. 归一化

print("== 1. 规则归一化（config.normalize_filters）==")
nf = config.normalize_filters

check("小写并补点", nf({"extInclude": ["MP4", ".mkv"]})["extInclude"], [".mp4", ".mkv"])
check("引擎不支持的格式被剔除", nf({"extInclude": [".avi", ".mp4"]})["extInclude"], [".mp4"])
check("去重且保序", nf({"extExclude": [".ts", ".TS", ".mov"]})["extExclude"], [".ts", ".mov"])
check("仅限里去掉已排除的",
      nf({"extInclude": [".mp4", ".mkv"], "extExclude": [".mkv"]})["extInclude"], [".mp4"])
check("空值的规则被丢掉", nf({"nameInclude": [{"mode": "contains", "value": "   "}]})["nameInclude"], [])
check("认不出的 mode 落回 contains",
      nf({"nameInclude": [{"mode": "wildcard", "value": "x"}]})["nameInclude"],
      [{"mode": "contains", "value": "x"}])
check("规则值两端空白被去掉",
      nf({"nameExclude": [{"mode": "regex", "value": " ^tmp "}]})["nameExclude"],
      [{"mode": "regex", "value": "^tmp"}])
check("非法入参（None）也返回完整结构",
      sorted(nf(None).keys()),
      ["extExclude", "extInclude", "nameExclude", "nameInclude"])

try:
    nf({"nameInclude": [{"mode": "regex", "value": "([未闭合"}]}, strict=True)
    check("严格模式：非法正则抛错", False, True)
except ValueError:
    check("严格模式：非法正则抛错", True, True)

check("非严格模式：坏正则静默剔除，好规则保留",
      nf({"nameInclude": [{"mode": "regex", "value": "([坏"},
                          {"mode": "contains", "value": "好"}]})["nameInclude"],
      [{"mode": "contains", "value": "好"}])

# ---------------------------------------------------------------- 2. 判定

print("== 2. 规则判定（filters.explain）==")
cmp = fmod.compile_filters(RULES)
check("一套规则编译成一份", cmp is not None, True)
check("空规则编译成 None（= 完全不过滤）", fmod.compile_filters({}), None)
check("字段缺失也当空规则", fmod.compile_filters({"extInclude": []}), None)

check("文件名命中「仅限」", fmod.explain(cmp, "/m/SONY-相机-2.mkv", ("SONY-相机-2.mkv",)), None)
check("父文件夹命中「仅限」", fmod.explain(cmp, "/m/相机导入/2026/a.mp4", ("相机导入", "2026", "a.mp4")), None)
check("类型被排除", fmod.explain(cmp, "/m/相机导入/b.ts", ("相机导入", "b.ts")),
      "命中排除的文件类型 .ts")
check("不在仅限类型内", fmod.explain(cmp, "/m/其他/d.mov", ("其他", "d.mov")),
      "不在仅限的文件类型内（.mp4、.mkv）")
check("哪一段都没命中「仅限」", fmod.explain(cmp, "/m/e.mp4", ("e.mp4",)),
      "不符合本目录的「仅限」规则")
check("排除规则命中文件夹名", fmod.explain(cmp, "/m/花絮/c.mp4", ("花絮", "c.mp4")),
      "命中排除规则「花絮」")
check("拿不到相对段时只用文件名（不误伤上游）",
      fmod.explain(cmp, "/vol1/1000/e.mp4"), "不符合本目录的「仅限」规则")

# 「包含」忽略大小写；正则按用户所写（要忽略就自己写 (?i)）。
# 注意比对的是**完整名字、含扩展名** —— 所以 ^DJI_\d+$ 这种「没算上 .mp4」的
# 写法是不命中的，界面上必须给出例子提醒（这正是最容易踩的一脚）
lower = fmod.compile_filters({"nameInclude": [{"mode": "contains", "value": "dji"}]})
check("包含规则忽略大小写", fmod.explain(lower, "/m/DJI_0002.mp4", ("DJI_0002.mp4",)), None)
rx = fmod.compile_filters({"nameInclude": [{"mode": "regex", "value": r"^DJI_\d{4}\.mp4$"}]})
check("正则命中（含扩展名）", fmod.explain(rx, "/m/DJI_0002.mp4", ("DJI_0002.mp4",)), None)
check("正则没算上扩展名 -> 不命中", fmod.explain(
    fmod.compile_filters({"nameInclude": [{"mode": "regex", "value": r"^DJI_\d{4}$"}]}),
    "/m/DJI_0002.mp4", ("DJI_0002.mp4",)), "不符合本目录的「仅限」规则")
check("正则默认区分大小写", fmod.explain(rx, "/m/dji_0002.mp4", ("dji_0002.mp4",)),
      "不符合本目录的「仅限」规则")

# 排除优先于仅限：同一个文件两类规则都命中时，结论必须是「排除」
both = fmod.compile_filters({
    "nameInclude": [{"mode": "contains", "value": "拍摄"}],
    "nameExclude": [{"mode": "contains", "value": "花絮"}],
})
check("排除优先于仅限",
      fmod.explain(both, "/m/花絮拍摄/b.mp4", ("花絮拍摄", "b.mp4")),
      "命中排除规则「花絮」")

# 只有类型规则、没有名字规则时，名字不参与判断
only_ext = fmod.compile_filters({"extInclude": [".mp4"]})
check("只配类型时名字不影响", fmod.explain(only_ext, "/m/随便什么.mp4", ("随便什么.mp4",)), None)

# ---------------------------------------------------------------- 3. 剪枝

print("== 3. 目录剪枝（filters.dir_pruned）==")
check("命中排除规则的目录整棵不进", fmod.dir_pruned(cmp, ("花絮",)), True)
check("更深一层命中也要剪", fmod.dir_pruned(cmp, ("素材", "花絮")), True)
check("没命中的目录照常进入", fmod.dir_pruned(cmp, ("相机导入",)), False)
check("「仅限」规则不剪枝（子目录里可能还有命中的文件）",
      fmod.dir_pruned(fmod.compile_filters(
          {"nameInclude": [{"mode": "contains", "value": "相机"}]}), ("其他",)), False)
check("没有规则时的兜底", fmod.dir_pruned({"exclude": ()}, ("任意",)), False)

# ---------------------------------------------------------------- 4. 收集集成

print("== 4. 扫描收集（core.splitter.collect_files）==")
plain = engine.collect_files([MEDIA], engine.SUPPORTED_EXTS, True, ())
check("不传规则：收集到全部视频", len(plain), 6)

skips = []
ff, df = fmod.build_matchers(RULES)
kept = engine.collect_files([MEDIA], engine.SUPPORTED_EXTS, True, (),
                            on_skip=lambda p, r: skips.append((p.name, r)),
                            file_filter=ff, dir_filter=df)
check("传规则：只剩通过的那几个", sorted(p.name for p in kept), ["SONY-相机-2.mkv", "a.mp4"])

skip_names = sorted(n for n, _ in skips)
check("被挡下的文件都报出原因（按名字排序）", skip_names, ["b.ts", "d.mov", "e.mp4"])
check("  b.ts 的原因", dict(skips)["b.ts"], "命中排除的文件类型 .ts")
check("  d.mov 的原因", dict(skips)["d.mov"], "不在仅限的文件类型内（.mp4、.mkv）")
check("  e.mp4 的原因", dict(skips)["e.mp4"], "不符合本目录的「仅限」规则")
check("被剪掉的目录里的文件连报都不报（整棵没进）",
      "c.mp4" in skip_names, False)

check("无规则时两个回调都是 None", fmod.build_matchers({}), (None, None))

# ---------------------------------------------------------------- 5. API

print("== 5. 接口（POST/PUT /api/watchpoints）==")
os.environ["VS_EXTRA_ROOTS"] = str(MEDIA)
config.save_settings({
    "split": {"size": "100G"},          # 阈值拉大 -> 一律不入队，本次只关心「收集到哪些」
    "watch": {"realtime": False, "pollInterval": 3600, "settleSeconds": 0},
})

from fastapi.testclient import TestClient                    # noqa: E402
from app.main import app                                     # noqa: E402

with TestClient(app) as client:
    base_body = {
        "path": str(MEDIA), "recursive": True, "scanMode": "manual",
        "scanIntervalHours": 6, "scanTime": "03:00", "note": "规则验证",
    }
    r = client.post("/api/watchpoints", json=dict(base_body, filters=RULES))
    check("带规则新建", r.status_code, 200)
    created = r.json()
    check("  extInclude 回读", created["filters"]["extInclude"], [".mp4", ".mkv"])
    check("  nameExclude 回读", created["filters"]["nameExclude"],
          [{"mode": "contains", "value": "花絮"}])
    wp_id = created["id"]

    r = client.post("/api/watchpoints", json={
        "path": str(OTHER), "note": "坏正则",
        "filters": {"nameInclude": [{"mode": "regex", "value": "([坏"}]}})
    check("非法正则直接 400", r.status_code, 400)
    check("  文案点出是正则的问题", "正则" in r.json()["detail"], True)

    r = client.post("/api/watchpoints", json={
        "path": str(BLOOPERS), "note": "非法类型",
        "filters": {"extInclude": [".avi", ".mp4"]}})
    check("非法类型不报错但被剔除", r.json()["filters"]["extInclude"], [".mp4"])
    client.delete("/api/watchpoints/" + r.json()["id"])

    # 扫描：规则的最终效果体现在 found / ignored 上
    r = client.post("/api/watchpoints/%s/scan" % wp_id)
    check("扫描状态码", r.status_code, 200)
    result = r.json()
    check("found = 通过规则的 2 个", result["found"], 2)
    check("queued = 0（阈值拉大，都超过不了）", result["queued"], 0)
    reasons = sorted(i["reason"] for i in result["ignored"])
    check("跳过明细里三条规则原因都在", reasons,
          sorted(["命中排除的文件类型 .ts",
                  "不在仅限的文件类型内（.mp4、.mkv）",
                  "不符合本目录的「仅限」规则"]))
    check("被剪掉的目录不算进任何统计",
          any(i["name"] == "c.mp4" for i in result["ignored"]), False)

    # 更新：整块替换，传空对象 = 清空规则
    r = client.put("/api/watchpoints/%s" % wp_id, json={"filters": {}})
    check("PUT 清空规则", r.json()["filters"]["nameInclude"], [])
    r = client.post("/api/watchpoints/%s/scan" % wp_id)
    check("清空后 collected 回到 6 个", r.json()["found"], 6)

    r = client.put("/api/watchpoints/%s" % wp_id, json={
        "filters": {"nameExclude": [{"mode": "regex", "value": "([坏"}]}})
    check("PUT 坏正则也 400", r.status_code, 400)
    check("  400 不会把已有规则改坏",
          client.get("/api/watchpoints").json()[0]["filters"]["nameInclude"], [])

    client.delete("/api/watchpoints/" + wp_id)

# ---------------------------------------------------------------- 6. 老数据

print("== 6. 老监控目录迁移（没有 filters 键）==")
config.WATCHPOINTS_PATH.write_text(json.dumps([{
    "id": "wp_old", "path": str(MEDIA), "scanMode": "manual",
}], ensure_ascii=False), encoding="utf-8")
loaded = config.load_watchpoints()[0]
check("补出空规则 = 不过滤", loaded["filters"],
      {"extInclude": [], "extExclude": [], "nameInclude": [], "nameExclude": []})

config.WATCHPOINTS_PATH.write_text(json.dumps([{
    "id": "wp_dirty", "path": str(MEDIA), "scanMode": "manual",
    "filters": {"extInclude": [".avi"], "nameExclude": [{"mode": "regex", "value": "([坏"}]},
}], ensure_ascii=False), encoding="utf-8")
loaded = config.load_watchpoints()[0]
check("手改坏的配置被清理干净", loaded["filters"]["extInclude"], [])
check("  坏正则被丢掉", loaded["filters"]["nameExclude"], [])

# ---------------------------------------------------------------- 收尾

print()
if bad:
    print("失败 %d 项，通过 %d 项" % (len(bad), ok))
    for b in bad:
        print("  - %s" % b)
    sys.exit(1)
print("全部通过（%d 项）" % ok)

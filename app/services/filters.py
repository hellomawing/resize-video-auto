#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/services/filters.py —— 监控目录自己的「只看这些 / 不看这些」规则

## 匹配口径

规则比对的是**文件自身名字 + 监控目录之下各级文件夹名**这每一段，
不含监控目录以上的路径。理由和「归档目录排除」完全相同：拿整条绝对路径
去比，`/vol1/1000/...` 里的 `1000` 会被当成规则命中的内容，用户写个
「包含 1000」就能把所有东西都命中，而且很难联想到是路径上游撞的。

于是「仅限」一条规则同时覆盖两种诉求：

    规则「包含 相机」
      * 相机导入/2026/a.mp4   -> 父目录名命中 -> 通过
      * 其他/相机花絮.mp4     -> 文件名命中   -> 通过
      * 其他/a.mp4            -> 哪一段都没命中 -> 不处理

## 顺序

**排除优先于仅限**：同时命中时一律排除。这不是可配置的偏好 —— 让「排除」
输给「仅限」意味着用户明确说不要的东西还可能被切，和安全相关的判断
（归档目录排除、自己的产物跳过）都按同一方向偏保守。

## 空 = 不过滤

一条规则都没有时 `compile_filters` 返回 None，调用方据此走「完全不判断」的
原路径。这保证了两件事：升级后老监控目录的扫描耗时一字不变；以及
`collect_files` 里那套剪枝代码不会在无规则时被启用。
"""

from __future__ import annotations

import re
from pathlib import Path

# 与 app/config.py 的 FILTER_MODES、app/models.py 的 FilterMode 一致
FILTER_MODES = ("contains", "regex")


def _prepare(rules) -> tuple:
    """把落盘的规则预编译成「判断时不用再解析」的形式。

    contains 提前转小写（忽略大小写的语义，规则里写「相机」也要能命中
    「相机导入」和「SONY-相机」）；regex 提前 compile，避免在扫描热路径上
    对每个文件反复编译同一条正则。
    """
    out = []
    for item in rules or ():
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "")
        if not value:
            continue
        if item.get("mode") == "regex":
            try:
                out.append(("regex", re.compile(value), value))
            except re.error:
                # normalize 时已经拦过一道；这里是「配置被手工改坏」的兜底，
                # 丢掉这一条，别让整次扫描因为一条坏正则而中断
                continue
        else:
            out.append(("contains", value.lower(), value))
    return tuple(out)


def compile_filters(raw) -> dict | None:
    """编译一个监控目录的过滤规则；一条规则都没有时返回 None。

    返回 None 而不是「全空的编译结果」很重要：调用方据此判断「完全不用过滤」，
    既省掉逐文件的判断开销，也让无规则时的扫描路径和升级前一模一样。
    """
    if not isinstance(raw, dict):
        return None
    ext_include = tuple(e for e in (raw.get("extInclude") or []) if e)
    ext_exclude = tuple(e for e in (raw.get("extExclude") or []) if e)
    include = _prepare(raw.get("nameInclude"))
    exclude = _prepare(raw.get("nameExclude"))
    if not (ext_include or ext_exclude or include or exclude):
        return None
    return {
        "ext_include": ext_include,
        "ext_exclude": ext_exclude,
        "include": include,
        "exclude": exclude,
    }


def _hit(rule, parts, lowered) -> bool:
    """一条规则是否命中这些路径段中的任意一段。"""
    mode, payload, _raw = rule
    for text, lower in zip(parts, lowered):
        if mode == "regex":
            if payload.search(text):
                return True
        elif payload in lower:
            return True
    return False


def _parts(path, rel_parts) -> tuple:
    if rel_parts:
        return tuple(rel_parts)
    # 调用方没给相对段（拿不到扫描根时的保守兜底）：只用文件名一段，
    # 宁可少命中也不要因为路径上游的名字误伤
    return (Path(path).name,)


def explain(compiled: dict, path, rel_parts=None, suffix: str = None) -> str | None:
    """判断一个文件是否被过滤掉。通过返回 None，否则返回给人看的原因。

    原因会直接出现在扫描结果的「跳过明细」里，所以要写清是哪一类规则挡的
    （类型 / 名字 / 仅限 / 排除），用户才分得清「规则写错了」和「本来就没文件」。
    """
    parts = _parts(path, rel_parts)
    lowered = tuple(p.lower() for p in parts)
    ext = (suffix if suffix is not None else Path(path).suffix).lower()

    # ① 排除，先类型后名字
    if ext and ext in compiled["ext_exclude"]:
        return "命中排除的文件类型 %s" % ext
    for rule in compiled["exclude"]:
        if _hit(rule, parts, lowered):
            return "命中排除规则「%s」" % rule[2]

    # ② 仅限：非空时要求命中
    inc_ext = compiled["ext_include"]
    if inc_ext and ext not in inc_ext:
        return "不在仅限的文件类型内（%s）" % "、".join(inc_ext)
    inc = compiled["include"]
    if inc and not any(_hit(rule, parts, lowered) for rule in inc):
        return "不符合本目录的「仅限」规则"

    return None


def dir_pruned(compiled: dict, rel_parts) -> bool:
    """这个子目录是否整棵都不用进（只可能因为命中排除规则）。

    纯性能优化，不改变结果：被剪掉的目录里每个文件，其相对路径都包含这个
    目录名，逐个判断也会得出「命中排除规则」同样的结论 —— 只是要白白
    readdir 一遍整棵子树（存储池上很可能是几十万个条目）。

    「仅限」规则**不剪枝**：子目录名没命中，不代表它下面没有命中的文件
    （规则可能是冲着文件名写的）。
    """
    if not rel_parts:
        return False
    parts = tuple(rel_parts)
    lowered = tuple(p.lower() for p in parts)
    return any(_hit(rule, parts, lowered) for rule in compiled["exclude"])


def build_matchers(raw):
    """给 core.splitter.collect_files 用的两个回调；无规则时返回 (None, None)。

    file_filter(path, rel_parts) -> 原因或 None
    dir_filter(rel_parts)        -> 是否整棵剪掉
    """
    compiled = compile_filters(raw)
    if compiled is None:
        return None, None

    def file_filter(path, rel_parts):
        return explain(compiled, path, rel_parts)

    def dir_filter(rel_parts):
        return dir_pruned(compiled, rel_parts)

    return file_filter, dir_filter

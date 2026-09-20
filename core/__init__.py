#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core —— 无损分割引擎

对外只暴露两件事：
    splitter.split_one(...)   处理单个视频
    undo.scan_groups/apply_undo(...)   撤销分割

引擎本身不关心 Web、队列、调度，保持可独立测试。
"""

from . import splitter, undo  # noqa: F401

__all__ = ["splitter", "undo"]

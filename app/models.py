#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/models.py —— 对外 JSON 的请求/响应模型

字段命名：Python 里用 snake_case，对外一律 camelCase。
靠 alias_generator 自动转换，不手写别名，也不在业务代码里手动拼字符串。
（docs/api.md 是唯一约定，改这里必须同步改文档。）
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# ---------------------------------------------------------------- 系统

class ToolInfo(CamelModel):
    path: Optional[str] = None
    version: Optional[str] = None
    ok: bool = False


class HealthOut(CamelModel):
    ok: bool
    version: str
    python: str
    ffmpeg: ToolInfo
    ffprobe: ToolInfo
    time: str
    uptime_sec: float


class JobCounts(CamelModel):
    queued: int = 0
    running: int = 0
    success: int = 0
    failed: int = 0
    canceled: int = 0
    skipped: int = 0


class StatsOut(CamelModel):
    jobs: JobCounts
    watchpoints: int = 0
    today_bytes: int = 0
    total_bytes: int = 0
    total_parts: int = 0


class DirItem(CamelModel):
    name: str
    path: str


class DirShortcut(CamelModel):
    """目录选择器里的「常用目录」条目。

    为什么需要它：fnOS 的存储池根（/vol1）权限位是 000、也没有扩展 ACL，
    内核拒绝对它 readdir —— 「从根往下逐级点」这条路第一级就走不通。
    但拿到完整路径就能正常访问（enumeration 和 access 是两回事），
    所以把这些「用户自己用过、肯定真实存在」的目录直接摆出来当入口。
    """
    name: str
    path: str
    # watchpoint=已添加的监控目录 | job=最近处理过 | setting=系统输出目录
    kind: Literal["watchpoint", "job", "setting"] = "job"
    note: str = ""


class BrowseOut(CamelModel):
    path: str
    parent: Optional[str] = None
    #: 可访问的根目录 = 容器里实际挂载进来的数据目录（自动探测，没有白名单）。
    roots: list[str] = Field(default_factory=list)
    dirs: list[DirItem] = Field(default_factory=list)
    shortcuts: list[DirShortcut] = Field(default_factory=list)
    # 根目录不可枚举时（fnOS 的 /vol1）自动探测到的、**可直接进入**的子目录，
    # 如 /vol1/1000 —— 前端把它们摆成「点一下直达」的入口。
    suggested_roots: list[str] = Field(default_factory=list)
    video_count: int = 0
    error: Optional[str] = None


# ---------------------------------------------------------------- 设置

# 原片处理方式，取值与 app/config.py 的 MARK_SOURCES 一致，改一处要同步另一处。
#   rename 加 #origin 后缀留在原处 | move 移到归档子目录
#   none   不处理原片            | delete 切分成功后删除原片
MarkSource = Literal["rename", "move", "none", "delete"]


class SplitSettings(CamelModel):
    # 没有 mode：只做 ffmpeg 无损流拷贝，不再让用户选。曾经的 auto/copy/bytes
    # 已删除，残留的 mode 键会在读取配置时被忽略（_deep_merge 只认默认里有的键）。
    by_size: bool = True
    size: str = "3.9G"
    seconds: float = 300
    all: bool = False
    ext: list[str] = Field(default_factory=list)
    recursive: bool = True
    outdir_mode: Literal["same", "custom"] = "same"
    outdir: str = ""
    mark_source: MarkSource = "rename"
    source_dir: str = "resize-video-origin-file"
    keep_metadata: bool = True
    overwrite: bool = False
    debug: bool = False


class WatchSettings(CamelModel):
    realtime: bool = True
    poll_interval: int = 30
    settle_seconds: int = 60
    min_size: str = "0"
    ignore_suffixes: list[str] = Field(default_factory=list)
    # 没有 allowed_roots：可访问范围只由容器挂载决定，不再是用户可配置项


class ServerSettings(CamelModel):
    host: str = "0.0.0.0"
    port: int = 8099
    job_log_lines: int = 2000


class Settings(CamelModel):
    split: SplitSettings = Field(default_factory=SplitSettings)
    watch: WatchSettings = Field(default_factory=WatchSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)


# PATCH /api/settings 的请求体。界面上一改就落盘的那些字段走它，一次只带一个字段；
# 与 PUT 的整体替换相对 —— PUT 少带一个字段就会把它打回默认值，不适合「改一个开关」。
#
# 字段刻意少于 Settings：
#   * all   是 by_size 的派生值，由 _normalize_settings 算出来，不给外部写入口
#   * debug 界面上没有，也不对外开放
class SplitSettingsPatch(CamelModel):
    by_size: Optional[bool] = None
    size: Optional[str] = None
    seconds: Optional[float] = None
    ext: Optional[list[str]] = None
    recursive: Optional[bool] = None
    outdir_mode: Optional[Literal["same", "custom"]] = None
    outdir: Optional[str] = None
    mark_source: Optional[MarkSource] = None
    source_dir: Optional[str] = None
    keep_metadata: Optional[bool] = None
    overwrite: Optional[bool] = None


class WatchSettingsPatch(CamelModel):
    realtime: Optional[bool] = None
    poll_interval: Optional[int] = None
    settle_seconds: Optional[int] = None
    min_size: Optional[str] = None
    ignore_suffixes: Optional[list[str]] = None


class ServerSettingsPatch(CamelModel):
    host: Optional[str] = None
    port: Optional[int] = None
    job_log_lines: Optional[int] = None


class SettingsPatch(CamelModel):
    """只改传进来的字段；三块都可选，每块内部也各自可选。"""

    split: Optional[SplitSettingsPatch] = None
    watch: Optional[WatchSettingsPatch] = None
    server: Optional[ServerSettingsPatch] = None


# ---------------------------------------------------------------- 监控目录

# 扫描方式：realtime 实时监听 | interval 每隔 N 小时 | daily 每天 HH:MM | manual 仅手动
# 取值与 app/config.py 的 SCAN_MODES 必须一致，改这里要同步改那边。
ScanMode = Literal["realtime", "interval", "daily", "manual"]

# 过滤规则的写法：
#   contains 包含某串（忽略大小写）—— 常用的那种，不用学正则
#   regex    正则表达式 —— 复杂规则才需要
# 取值与 app/services/filters.py 的 FILTER_MODES 一致，改一处要同步另一处。
FilterMode = Literal["contains", "regex"]


class FilterRule(CamelModel):
    """一条「名字规则」。

    它比对的是**文件自身名字 + 监控目录之下各级文件夹名**这每一段，
    不含监控目录以上的路径 —— 与「归档目录排除」同一口径，
    否则规则里写个 `1000` 会把 `/vol1/1000/...` 下的一切都命中。
    """
    mode: FilterMode = "contains"
    value: str = ""


class WatchFilters(CamelModel):
    """某个监控目录自己的「只看这些 / 不看这些」规则。

    为什么每个目录一套而不是全局一套：同一个人可能既想监控「相机导入」
    （只认 mp4），又想监控「录制」目录（排队剔除试拍的花絮）。全局一套
    等于逼用户为不同目录建不同的库。

    空 = 不过滤（所以老监控目录零迁移成本，行为与升级前完全一致）。
    排除优先于仅限：同时命中时一律排除。
    """
    # 文件类型：取值范围必须是「引擎能无损切分的格式」（engine.SUPPORTED_EXTS），
    # 界面上的候选则进一步收窄到系统设置里已启用的那几种
    ext_include: list[str] = Field(default_factory=list)
    ext_exclude: list[str] = Field(default_factory=list)
    name_include: list[FilterRule] = Field(default_factory=list)
    name_exclude: list[FilterRule] = Field(default_factory=list)


class FilterPreviewIn(CamelModel):
    """试算「这条路径会不会被这套规则挡下」——编辑页的命中预览（单条样例）。

    只做字符串判断，**不要求这个文件真的存在**：用户往往是拿一个脑子里的
    文件名去试规则，而不是先去目录里翻出一个真实文件。所以这里既不校验
    存在性，也不要求路径落在可浏览范围内。
    """
    path: str
    # 当前表单里的监控目录路径。给了它才能把「整条粘进来的绝对路径」
    # 相对化；不给就按相对路径处理
    base_path: str = ""
    # 允许为空 = 「一条规则都没配」，此时结论必然是「会被处理」
    filters: Optional[WatchFilters] = None


class FilterListFile(CamelModel):
    """命中预览里的一行：某个监控目录下已存在的文件会怎么被这套规则对待。

    path 是**相对监控目录**的路径（本级文件名，或含子目录的相对路径）。
    预览只在目录下找「引擎能处理的视频」——非视频文件本来就不会被切，
    列出来只会刷屏，与扫描实际看到的候选一致。
    """
    path: str
    # False = 会被处理（命中）；True = 被这套规则挡下，skipped_reason 说明原因
    skipped: bool = False
    skipped_reason: str = ""


class FilterListPreviewIn(CamelModel):
    """对监控目录下**已存在文件**做命中预览。

    与 FilterPreviewIn 的区别：那边试算一条手敲的样例路径（不要求存在）；
    这边列出目录里真实已有的候选视频，逐个告诉用户会被处理还是被规则挡下。
    范围只限监控目录本身：递归由调用方（前端）按监控目录的递归开关决定。
    """
    path: str = ""            # 要列文件的目录；空 = 不预览，直接返回空表
    recursive: bool = True    # 是否连同子目录一起列
    # 允许为空 = 「一条规则都没配」，此时结论必然是「会被处理」
    filters: Optional[WatchFilters] = None


class FilterListPreviewOut(CamelModel):
    """命中预览的列表结果。

    ok=False 表示**规则本身有问题**（正则编译不过）或目录读不动，
    此时 files 为空、由 message 说明原因 —— 一律 200 返回，理由同上方的
    FilterPreviewOut（预览是「帮我看看」而不是「保存配置」）。
    """
    ok: bool = True
    message: str = ""
    has_rules: bool = False
    # 目录里找到的候选视频总数（含被挡下的）
    total: int = 0
    # 其中会被处理的个数
    hit: int = 0
    # 逐文件结论，按「命中在前、被挡在后」排序
    files: list[FilterListFile] = Field(default_factory=list)


class FilterPreviewOut(CamelModel):
    """预览结论。

    ok=False 表示**规则本身有问题**（正则编译不过），此时没有判定结论；
    这类问题同样用 200 返回，由 message 说明 —— 预览是「帮我看看」而不是
    「保存配置」，用 400 会逼前端去区分「网络坏了」和「正则写错了」。
    """
    ok: bool = True
    message: str = ""
    # 实际参与比对的每一段名字，**照实回显**：用户困惑「我这条规则在跟什么
    # 比」时，看着这一列就明白了（尤其是正则跨不了路径段这个坑）
    parts: list[str] = Field(default_factory=list)
    suffix: str = ""
    # 有没有配规则。False = 不过滤，什么都不会被挡
    has_rules: bool = False
    skipped: bool = False
    # skipped=True 时的原因，与扫描结果「跳过明细」里的文案完全同源
    reason: str = ""


class WatchPoint(CamelModel):
    id: str
    path: str
    recursive: bool = True
    scan_mode: ScanMode = "realtime"
    scan_interval_hours: int = 6
    scan_time: str = "03:00"
    # 原片处理方式。空串 = 跟随系统设置 —— 这是有意的「未设置」状态，
    # 不是缺省值，解析优先级见 config.resolve_mark_policy
    mark_source: str = ""
    source_dir: str = ""
    note: str = ""
    filters: WatchFilters = Field(default_factory=WatchFilters)
    created_at: str = ""
    last_scan_at: Optional[str] = None
    # 下次自动扫描时间；实时监听与仅手动没有「下次」，返回 None
    next_scan_at: Optional[str] = None
    video_count: int = 0


class WatchPointCreate(CamelModel):
    path: str
    recursive: bool = True
    scan_mode: ScanMode = "realtime"
    scan_interval_hours: int = 6
    scan_time: str = "03:00"
    mark_source: str = ""
    source_dir: str = ""
    note: str = ""
    filters: WatchFilters = Field(default_factory=WatchFilters)


class WatchPointUpdate(CamelModel):
    recursive: Optional[bool] = None
    scan_mode: Optional[ScanMode] = None
    scan_interval_hours: Optional[int] = None
    scan_time: Optional[str] = None
    # 传空串表示「改回跟随系统设置」，所以这两个字段不能用 None 表达「清空」
    mark_source: Optional[str] = None
    source_dir: Optional[str] = None
    note: Optional[str] = None
    # 整块替换：传 {} 就是「清空全部规则，回到不过滤」。
    # 不用 None 表达清空，理由与 markSource 那两项相同 —— 两者语义不同。
    filters: Optional[WatchFilters] = None


class IgnoredFile(CamelModel):
    """扫描时「看到了但按规则不处理」的文件。

    存在的意义是把「真的没有视频」和「有视频、但被保护性跳过」区分开——
    以前这两种情况的返回都是 found=0，用户只能靠猜。

    kind / resettable 是再往下一层的分化：光说「跳过了」还不够，
    用户真正要判断的是「这个还救不救得回来」。
    """
    name: str
    reason: str
    kind: str = ""            # "slice" 切片 / "origin" 已切分过的原片
    resettable: bool = False  # 切片已不在的原片 -> 恢复原名即可重新分割


class ResettableDir(CamelModel):
    """含「切片已不在的原片」的目录。前端据此逐目录发起恢复。"""
    path: str
    recursive: bool = True


class ScanResult(CamelModel):
    found: int = 0
    queued: int = 0
    skipped: int = 0
    waiting: int = 0
    ignored: list[IgnoredFile] = Field(default_factory=list)
    ignored_total: int = 0
    resettable_total: int = 0
    resettable_dirs: list[ResettableDir] = Field(default_factory=list)
    message: str = ""

    @classmethod
    def from_engine(cls, result: dict) -> ScanResult:
        """把扫描引擎的 dict 结果转成对外模型。

        两个扫描入口（全部目录 / 单个目录）出的键完全一致，转换只写这一处，
        以后加字段就不会漏掉其中一个接口。
        """
        return cls(
            found=result.get("found", 0),
            queued=result.get("queued", 0),
            skipped=result.get("skipped", 0),
            waiting=result.get("waiting", 0),
            ignored=result.get("ignored") or [],
            ignored_total=result.get("ignoredTotal", 0),
            resettable_total=result.get("resettableTotal", 0),
            resettable_dirs=result.get("resettableDirs") or [],
            message=result.get("message", ""))


class ScanIn(CamelModel):
    """手动扫描时的临时覆盖参数。

    字段留空（或整个请求体不传）= 跟随该监控目录 / 系统设置。它刻意**不落盘**：
    表达的是「就这一次按这个方式处理」，改的是一次行为，不是配置。
    """
    mark_source: Optional[MarkSource] = None
    source_dir: Optional[str] = None


# ---------------------------------------------------------------- 撤销

class UndoPreviewIn(CamelModel):
    path: str
    recursive: bool = True


class UndoSlice(CamelModel):
    path: str
    name: str
    size: int


class UndoGroup(CamelModel):
    base: str
    suffix: str
    origin: str
    slices: list[UndoSlice] = Field(default_factory=list)
    origin_size: int = 0
    slice_sum: int = 0
    mode: str = "?"
    ok: bool = False
    reason: str = ""
    origin_duration: float = 0
    slice_durations: list[float] = Field(default_factory=list)
    duration_sum: float = 0


class UndoLoneOrigin(CamelModel):
    """已被切分、但切片已经不在了的原片（`原名#origin.扩展名`）。

    成因多半是切片被手工删除或移走了。这类文件以前在界面上完全隐身：
    扫描器把它当「已处理」跳过，撤销页又只从切片出发去找原片，于是它
    既不在 groups 也不在 orphans 里，只能 SSH 手工改名才能脱困。
    它本身不是错误，只是「已经没有可撤销的东西了」。
    """
    # 字段名与 UndoGroup.origin 对齐：两者都是「原片的路径」，
    # 共用同一个恢复函数，就没必要各叫各的
    origin: str
    name: str
    base: str = ""
    suffix: str = ""
    size: int = 0
    mtime: str = ""


class UndoPreviewOut(CamelModel):
    path: str
    groups: list[UndoGroup] = Field(default_factory=list)
    origin_only: list[UndoLoneOrigin] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)
    ok_count: int = 0
    bad_count: int = 0


class UndoApplyIn(CamelModel):
    path: str
    recursive: bool = True
    delete_slices: bool = True
    restore_origin: bool = True
    # 同时把「切片已不在」的原片也恢复原名。默认关，因为恢复原名等于让它
    # 重新变回待处理文件，实时监听会立刻再切一遍 —— 必须由用户明确要求。
    restore_origin_only: bool = False
    trash: bool = True


class UndoDetail(CamelModel):
    base: str
    action: str
    message: str


class UndoApplyOut(CamelModel):
    deleted: int = 0
    trashed: int = 0
    restored: int = 0
    restored_orphans: int = 0
    skipped: int = 0
    freed_bytes: int = 0
    problems: list[str] = Field(default_factory=list)
    details: list[UndoDetail] = Field(default_factory=list)
    orphans: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- 清理

class ClearJobsIn(CamelModel):
    statuses: list[str] = Field(default_factory=lambda: ["success", "failed", "canceled"])


# ---------------------------------------------------------------- 处理失败的文件

class FailureOut(CamelModel):
    """一个「切不动」的文件。

    注意它不代表磁盘上少了什么：切分失败时本工具**绝不改动原片**，
    这里只是把「哪个文件、为什么没成」记下来给人看。
    size / mtime 是判断依据：文件变了就自动重新尝试，不需要人工清记录。
    """
    path: str
    name: str
    size: int = 0
    mtime: float = 0
    reason: str = ""
    job_id: Optional[str] = None
    at: str = ""


class FailureListOut(CamelModel):
    total: int = 0
    items: list[FailureOut] = Field(default_factory=list)


class FailurePathIn(CamelModel):
    path: str


class FailureClearIn(CamelModel):
    # 不传 paths（或传 null）表示「全部清掉」
    paths: Optional[list[str]] = None


# ---------------------------------------------------------------- 配置导入导出

class ConfigBundle(CamelModel):
    """
    导出/导入的配置包。

    三块内容都用**存储原样**（camelCase 的裸 dict/list），不再套一层模型：
    它们本来就是 config 读写的文件内容，再定义一遍模型只会多一处要同步的地方，
    而且 normalize（save_settings / normalize_watchpoint）本来就会纠正脏值。
    """
    version: int = 1
    exported_at: Optional[str] = None
    settings: dict = Field(default_factory=dict)
    watchpoints: list[dict] = Field(default_factory=list)
    archive_dirs: list[str] = Field(default_factory=list)


class ImportResult(CamelModel):
    settings_applied: bool = False
    watchpoints_added: int = 0
    watchpoints_updated: int = 0
    watchpoints_skipped: int = 0
    archive_dirs_added: int = 0
    message: str = ""

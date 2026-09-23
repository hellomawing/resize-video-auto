// 与 docs/api.md 严格对应的 TS 类型定义。
// 字段名一律 camelCase，与后端 Pydantic alias 输出保持一致；不要在此处发明字段。

export interface FfTool {
  path: string
  version: string
  ok: boolean
}

export interface Health {
  ok: boolean
  version: string
  python: string
  ffmpeg: FfTool
  ffprobe: FfTool
  time: string
  uptimeSec: number
}

export interface JobStats {
  queued: number
  running: number
  success: number
  failed: number
  canceled: number
}

export interface Stats {
  jobs: JobStats
  watchpoints: number
  todayBytes: number
  totalParts: number
}

export interface BrowseEntry {
  name: string
  path: string
}

// 目录选择器里的「常用目录」。为什么需要：像 fnOS 这类系统把存储池根目录
// （/vol1）设成不可枚举，从根往下逐级点第一级就断了；而这些目录是用户
// 自己用过的，点一下直达，绕开那一层。
export interface DirShortcut {
  name: string
  path: string
  kind: 'watchpoint' | 'job' | 'setting'
  note: string
}

/** /api/env：容器内的运行环境（只读展示用，例如「设置 → 服务参数」里的数据目录） */
export interface EnvInfo {
  dataDir: string
  dataDirWritable: boolean
  ffmpeg: string | null
  ffprobe: string | null
  pythonVersion: string
  platform: string
  uid: number | null
  gid: number | null
  /**
   * 环境变量 VS_HOST / VS_PORT 的取值。
   * 有值 = 监听地址/端口由部署（容器）锁死，设置页里改了不生效，
   * 界面要把对应输入框置为只读并说明原因。
   */
  hostEnv: string | null
  portEnv: string | null
}

export interface BrowseResult {
  path: string
  parent: string | null
  // 可访问的根目录 = 容器里实际挂载进来的数据目录（没有白名单这个概念）
  roots: string[]
  dirs: BrowseEntry[]
  shortcuts: DirShortcut[]
  // 根目录不可枚举（fnOS 的 /vol1）时自动探测到的可直接进入的层，如 /vol1/1000
  suggestedRoots: string[]
  videoCount: number
  error: string | null
}

// ---- 设置 ----
/**
 * 切割方式的历史取值。**已经不是设置项了**：本工具只做 ffmpeg 无损流拷贝。
 * 保留这个联合类型是因为任务行里还存着老任务的 mode（auto / bytes），
 * 列表与详情要能照原样显示出来。
 */
export type SplitMode = 'auto' | 'copy' | 'bytes'
export type OutdirMode = 'same' | 'custom'
export type MarkSource = 'rename' | 'move' | 'none' | 'delete'

/**
 * 原片处理方式 + 「跟随上级」的空串。
 *
 * 空串是一个**有意义的取值**，不是缺省值：在监控目录那一层表示「跟随系统设置」。
 * 系统设置那一层没有上级可跟随，所以那里的控件要把「跟随」项藏掉。
 */
export type MarkValue = MarkSource | ''

export interface SplitSettings {
  bySize: boolean
  size: string
  seconds: number
  all: boolean
  ext: string[]
  recursive: boolean
  outdirMode: OutdirMode
  outdir: string
  /**
   * 原片处理方式。系统设置这一层没有上级可跟随，界面上不提供空串选项；
   * 万一被手改成空串，后端 normalize 会按默认值纠正，所以这里照实标成 MarkValue。
   */
  markSource: MarkValue
  sourceDir: string
  keepMetadata: boolean
  overwrite: boolean
}

export interface WatchSettings {
  realtime: boolean
  pollInterval: number
  settleSeconds: number
  minSize: string
  ignoreSuffixes: string[]
}

export interface ServerSettings {
  host: string
  port: number
  jobLogLines: number
}

export interface Settings {
  split: SplitSettings
  watch: WatchSettings
  server: ServerSettings
}

/**
 * PATCH /api/settings 的请求体：只带要改的字段，其余保持不动。
 * 与 PUT 的整体替换相对（PUT 少带一个字段就会把它打回默认值），
 * 用于界面上「拨个开关就立刻生效」的那些项。
 */
export type SettingsPatch = {
  split?: Partial<SplitSettings>
  watch?: Partial<WatchSettings>
  server?: Partial<ServerSettings>
}

// ---- 监控目录 ----
// 扫描方式：realtime 实时监听 | interval 每隔 N 小时 | daily 每天 HH:MM | manual 仅手动
export type ScanMode = 'realtime' | 'interval' | 'daily' | 'manual'

// ---- 监控目录的过滤规则 ----
/**
 * 一条规则的两种写法：
 *   contains 包含某串（忽略大小写）—— 常用的那种，不必为了「含 XX 字符」去学正则
 *   regex    正则表达式 —— 只有复杂规则才需要，按你写的原样生效（要忽略大小写自己加 (?i)）
 */
export type FilterMode = 'contains' | 'regex'

export interface FilterRule {
  mode: FilterMode
  value: string
}

/**
 * 某个监控目录自己的「只看这些 / 不看这些」。
 *
 * 比对对象是**文件 / 文件夹的完整名字（含扩展名）**，以及该文件到监控目录
 * 之间各级文件夹的名字 —— 不含监控目录以上的路径。所以规则「包含 相机」
 * 既能命中「相机导入/2026/a.mp4」（父文件夹命中），也能命中「SONY-相机.mp4」
 * （文件名命中），但不会因为路径上游是 /vol1/1000 而被误命中。
 *
 * 空数组 = 该项不限制；排除优先于仅限（同时命中时一律排除）。
 */
export interface WatchFilters {
  /** 只处理这些类型，空 = 不限。取值必须是引擎能无损切分的格式 */
  extInclude: string[]
  /** 不要这些类型 */
  extExclude: string[]
  /** 只处理命中这些规则的文件/文件夹，空 = 不限 */
  nameInclude: FilterRule[]
  /** 命中这些规则的文件/文件夹一律不用 */
  nameExclude: FilterRule[]
}

/**
 * 「命中预览」的试算结果：拿一条样例路径去问这套规则会不会把它挡下。
 *
 * 判定与真实扫描**同源**（后端 services.filters.explain），所以预览说
 * 「会被处理」，扫描时就真的会处理。纯计算，不要求文件真实存在。
 */
export interface WatchFilterPreview {
  /** false = 规则本身有问题（正则编译不过），此时没有判定结论 */
  ok: boolean
  message: string
  /** 实际参与比对的每一段名字，照实回显 —— 规则到底在跟什么比，看这里 */
  parts: string[]
  suffix: string
  /** false = 一条规则都没配，什么都不会被挡 */
  hasRules: boolean
  skipped: boolean
  /** skipped 为真时的原因，与扫描结果「跳过明细」里的文案同源 */
  reason: string
}

export interface WatchPoint {
  id: string
  path: string
  recursive: boolean
  scanMode: ScanMode
  scanIntervalHours: number
  scanTime: string
  /**
   * 原片处理方式。空串表示「跟随系统设置」——这是有意的未设置状态，
   * 不是缺省值；解析优先级见后端 config.resolve_mark_policy
   */
  markSource: MarkValue
  sourceDir: string
  note: string
  filters: WatchFilters
  createdAt: string
  lastScanAt: string | null
  nextScanAt: string | null
  videoCount: number
}

export interface WatchPointCreate {
  path: string
  recursive: boolean
  scanMode: ScanMode
  scanIntervalHours: number
  scanTime: string
  markSource: MarkValue
  sourceDir: string
  note: string
  filters: WatchFilters
}

/**
 * 手动扫描时的临时覆盖参数：字段留空 = 跟随该监控目录 / 系统设置。
 * 只作用于本次入队的任务，不会写进任何配置。
 */
export interface ScanOptions {
  markSource?: MarkSource
  sourceDir?: string
}

/** 扫描时「看到了但按规则没处理」的文件 */
export interface IgnoredFile {
  name: string
  reason: string
  /** 'slice' 切片 / 'origin' 已切分过的原片 */
  kind: string
  /** 切片已不在的原片 —— 恢复原名就能重新分割 */
  resettable: boolean
}

/** 含「切片已不在的原片」的目录，前端据此逐目录恢复 */
export interface ResettableDir {
  path: string
  recursive: boolean
}

export interface ScanResult {
  found: number
  queued: number
  skipped: number
  /** 还在拷贝中、需要等待稳定的数量 */
  waiting: number
  /** 被跳过的明细，最多几十条 */
  ignored: IgnoredFile[]
  /** 被跳过的总数。明细可能被截断，要显示总数时以这个为准 */
  ignoredTotal: number
  /** 其中「切片已不在、可以重新分割」的原片数量 */
  resettableTotal: number
  /** 这些原片所在的目录 */
  resettableDirs: ResettableDir[]
  /** 后端生成的人话总结，直接展示即可，不要在前端另拼一套文案 */
  message: string
}

// ---- 任务 ----
export type JobStatus = 'queued' | 'running' | 'success' | 'failed' | 'canceled' | 'skipped'
export type JobPhase = 'waiting' | 'probe' | 'splitting' | 'verifying' | 'marking' | 'done'
export type JobTrigger = 'watch' | 'manual' | 'retry'

export interface ProducedFile {
  name: string
  size: number
}

export interface Job {
  id: string
  src: string
  srcName: string
  srcSize: number
  status: JobStatus
  phase: JobPhase
  progress: number
  partsTotal: number
  partsDone: number
  mode: SplitMode
  usedMode: string
  outdir: string
  trigger: JobTrigger
  watchpointId: string | null
  /** 入队那一刻定下的原片处理方式（快照）。老任务可能为 null */
  markSource: string | null
  sourceDir: string | null
  message: string
  error: string | null
  produced: ProducedFile[]
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  durationSec: number | null
}

export interface JobList {
  total: number
  items: Job[]
}

export interface JobLog {
  jobId: string
  lines: string[]
  truncated: boolean
}

export interface ClearBody {
  statuses: JobStatus[]
}

// ---- 撤销分割 ----
export interface UndoSlice {
  path: string
  name: string
  size: number
}

export interface UndoGroup {
  base: string
  suffix: string
  origin: string
  slices: UndoSlice[]
  originSize: number
  sliceSum: number
  mode: string
  ok: boolean
  reason: string
  originDuration: number
  sliceDurations: number[]
  durationSum: number
}

/**
 * 已被切分、但切片已经不在的原片（`原名#origin.扩展名`）。
 *
 * 它没有可撤销的内容，但也必须显示出来：否则这个文件在界面上彻底隐身
 * （扫描跳过它、撤销页的 groups/orphans 里也没有它），只能手工改名。
 */
export interface UndoLoneOrigin {
  origin: string
  name: string
  base: string
  suffix: string
  size: number
  mtime: string
}

export interface UndoPreview {
  path: string
  groups: UndoGroup[]
  /** 切片已不在的孤零零原片，只能「恢复原名」 */
  originOnly: UndoLoneOrigin[]
  orphans: string[]
  okCount: number
  badCount: number
}

export interface UndoApplyBody {
  path: string
  recursive: boolean
  deleteSlices: boolean
  restoreOrigin: boolean
  /** 是否同时把「切片已不在」的原片也恢复原名 */
  restoreOriginOnly?: boolean
  trash: boolean
}

export interface UndoDetail {
  base: string
  action: string
  message: string
}

export interface UndoResult {
  deleted: number
  trashed: number
  restored: number
  /** 本次恢复原名的「无切片原片」数量 */
  restoredOrphans: number
  skipped: number
  freedBytes: number
  problems: string[]
  details: UndoDetail[]
  orphans: string[]
}

// ---- 处理失败的文件 ----

/**
 * 一个「切不动」的文件。**原片仍在磁盘上原封未动** —— 切分失败时本工具
 * 不会改名、移动或删除源文件，这里只是登记「哪个文件、为什么没成」。
 *
 * size / mtime 是「同一性凭据」：文件被替换或改动过（两者有一个变了），
 * 后端会自动忘掉这条记录、重新尝试一次，不需要人工清理。
 */
export interface FailureRecord {
  path: string
  name: string
  size: number
  mtime: number
  reason: string
  /** 失败那次的任务 id，可跳去任务队列看完整日志 */
  jobId: string | null
  at: string
}

export interface FailureList {
  total: number
  items: FailureRecord[]
}

// ---- 配置导入导出 ----
export interface ConfigBundle {
  version: number
  exportedAt: string | null
  settings: Record<string, unknown>
  watchpoints: Record<string, unknown>[]
  archiveDirs: string[]
}

export interface ImportResult {
  settingsApplied: boolean
  watchpointsAdded: number
  watchpointsUpdated: number
  watchpointsSkipped: number
  archiveDirsAdded: number
  message: string
}

// ---- WebSocket 消息（服务端单向推送） ----
export type WsMessage =
  | { type: 'hello'; serverTime: string; version: string }
  | { type: 'job.created'; job: Job }
  | { type: 'job.updated'; job: Job }
  | { type: 'job.log'; jobId: string; line: string }
  | { type: 'job.progress'; jobId: string; progress: number; partsDone: number; partsTotal: number; phase: JobPhase }
  | { type: 'scan.finished'; watchpointId: string | null; found: number; queued: number }
  | { type: 'watchpoint.scan'; watchpointId: string; path: string }
  | { type: 'settings.updated'; source?: string }
  | { type: 'ping'; serverTime: string }

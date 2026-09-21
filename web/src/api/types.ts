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
  schedules: number
  todayBytes: number
  totalParts: number
}

export interface BrowseEntry {
  name: string
  path: string
}

export interface BrowseResult {
  path: string
  parent: string
  roots: string[]
  dirs: BrowseEntry[]
  videoCount: number
  error: string | null
}

// ---- 设置 ----
export type SplitMode = 'auto' | 'copy' | 'bytes'
export type OutdirMode = 'same' | 'custom'
export type MarkSource = 'rename' | 'move' | 'none' | 'delete'

export interface SplitSettings {
  mode: SplitMode
  bySize: boolean
  size: string
  seconds: number
  all: boolean
  ext: string[]
  recursive: boolean
  outdirMode: OutdirMode
  outdir: string
  markSource: MarkSource
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
  allowedRoots: string[]
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

// ---- 监控目录 ----
// 扫描方式：realtime 实时监听 | interval 每隔 N 小时 | daily 每天 HH:MM | manual 仅手动
export type ScanMode = 'realtime' | 'interval' | 'daily' | 'manual'

export interface WatchPoint {
  id: string
  path: string
  recursive: boolean
  scanMode: ScanMode
  scanIntervalHours: number
  scanTime: string
  note: string
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
  note: string
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

// ---- 定时任务 ----
export interface Schedule {
  id: string
  name: string
  cron: string
  enabled: boolean
  watchpointIds: string[]
  lastRunAt: string | null
  nextRunAt: string | null
  cronText: string
}

export interface ScheduleCreate {
  name: string
  cron: string
  enabled: boolean
  watchpointIds: string[]
}

// ---- 任务 ----
export type JobStatus = 'queued' | 'running' | 'success' | 'failed' | 'canceled' | 'skipped'
export type JobPhase = 'waiting' | 'probe' | 'splitting' | 'verifying' | 'marking' | 'done'
export type JobTrigger = 'watch' | 'manual' | 'schedule' | 'retry'

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

// ---- WebSocket 消息（服务端单向推送） ----
export type WsMessage =
  | { type: 'hello'; serverTime: string; version: string }
  | { type: 'job.created'; job: Job }
  | { type: 'job.updated'; job: Job }
  | { type: 'job.log'; jobId: string; line: string }
  | { type: 'job.progress'; jobId: string; progress: number; partsDone: number; partsTotal: number; phase: JobPhase }
  | { type: 'scan.finished'; watchpointId: string | null; found: number; queued: number }
  | { type: 'watchpoint.scan'; watchpointId: string; path: string }
  | { type: 'settings.updated' }
  | { type: 'schedule.fired'; scheduleId: string; name: string }
  | { type: 'ping'; serverTime: string }

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
export interface WatchPoint {
  id: string
  path: string
  recursive: boolean
  enabled: boolean
  note: string
  createdAt: string
  lastScanAt: string | null
  videoCount: number
}

export interface WatchPointCreate {
  path: string
  recursive: boolean
  note: string
}

export interface ScanResult {
  found: number
  queued: number
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

export interface UndoPreview {
  path: string
  groups: UndoGroup[]
  orphans: string[]
  okCount: number
  badCount: number
}

export interface UndoApplyBody {
  path: string
  recursive: boolean
  deleteSlices: boolean
  restoreOrigin: boolean
  trash: boolean
}

export interface UndoDetail {
  base: string
  action: string
  message: string
}

export interface UndoResult {
  deleted: number
  restored: number
  skipped: number
  freedBytes: number
  problems: string[]
  details: UndoDetail[]
}

// ---- WebSocket 消息（服务端单向推送） ----
export type WsMessage =
  | { type: 'hello'; serverTime: string; version: string }
  | { type: 'job.created'; job: Job }
  | { type: 'job.updated'; job: Job }
  | { type: 'job.log'; jobId: string; line: string }
  | { type: 'job.progress'; jobId: string; progress: number; partsDone: number; partsTotal: number; phase: JobPhase }
  | { type: 'scan.finished'; watchpointId: string | null; found: number; queued: number }
  | { type: 'settings.updated' }
  | { type: 'schedule.fired'; scheduleId: string; name: string }

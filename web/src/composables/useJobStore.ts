import { reactive } from 'vue'
import type { Job, JobPhase } from '../api/types'
import { useWebSocket } from './useWebSocket'

// 全局任务状态 map：无论当前在哪个页面，WebSocket 推送的 job.* 都会汇入这里，
// 保证 /jobs 之外（如概览页）也能实时反映任务进度与日志。

const byId = reactive(new Map<string, Job>())
const logs = reactive(new Map<string, string[]>())

/** 合并/插入任务；保留未知字段的既有值（如本地已累计的日志不在此处管理）。 */
function upsert(job: Job): void {
  const prev = byId.get(job.id)
  byId.set(job.id, prev ? { ...prev, ...job } : { ...job })
}

/** 追加单条日志（WS 实时推送），不存在则新建数组。 */
function appendLog(jobId: string, line: string): void {
  const arr = logs.get(jobId)
  if (arr) arr.push(line)
  else logs.set(jobId, [line])
}

/** 用 API 返回的完整日志覆盖（首次打开详情时）。 */
function setLog(jobId: string, lines: string[]): void {
  logs.set(jobId, [...lines])
}

/** 进度推送：更新数值字段；排队中收到进度则视为已转入运行。 */
function applyProgress(
  jobId: string,
  progress: number,
  partsDone: number,
  partsTotal: number,
  phase: JobPhase,
): void {
  const job = byId.get(jobId)
  if (!job) return
  job.progress = progress
  job.partsDone = partsDone
  job.partsTotal = partsTotal
  job.phase = phase
  if (job.status === 'queued') job.status = 'running'
}

let wired = false

export function useJobStore() {
  // 仅接线一次：监听 WS 事件更新全局状态
  if (!wired) {
    wired = true
    const ws = useWebSocket()
    ws.on((msg) => {
      switch (msg.type) {
        case 'job.created':
          upsert(msg.job)
          break
        case 'job.updated':
          upsert(msg.job)
          break
        case 'job.log':
          appendLog(msg.jobId, msg.line)
          break
        case 'job.progress':
          applyProgress(msg.jobId, msg.progress, msg.partsDone, msg.partsTotal, msg.phase)
          break
        default:
          break
      }
    })
  }

  return { byId, logs, upsert, appendLog, setLog }
}

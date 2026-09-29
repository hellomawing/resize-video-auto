import { request } from './client'
import type {
  ScanOptions,
  ScanResult,
  WatchFilterListPreview,
  WatchFilterPreview,
  WatchFilters,
  WatchPoint,
  WatchPointCreate,
} from './types'

export const listWatchpoints = (): Promise<WatchPoint[]> =>
  request<WatchPoint[]>('/watchpoints')

export const createWatchpoint = (body: WatchPointCreate): Promise<WatchPoint> =>
  request<WatchPoint>('/watchpoints', { method: 'POST', body })

export const updateWatchpoint = (
  id: string,
  body: Partial<
    Pick<
      WatchPoint,
      | 'recursive'
      | 'scanMode'
      | 'scanIntervalHours'
      | 'scanTime'
      | 'markSource'
      | 'sourceDir'
      | 'note'
      | 'filters'
    >
  >,
): Promise<WatchPoint> => request<WatchPoint>(`/watchpoints/${id}`, { method: 'PUT', body })

export const deleteWatchpoint = (id: string): Promise<void> =>
  request<void>(`/watchpoints/${id}`, { method: 'DELETE' })

/**
 * 立即扫描一次。不受扫描方式限制 —— 这是「手动分割」的入口。
 * options 指定「就这一次」原片怎么处理；不传则跟随该目录、再退回系统设置。
 */
export const scanWatchpoint = (id: string, options?: ScanOptions): Promise<ScanResult> =>
  request<ScanResult>(`/watchpoints/${id}/scan`, { method: 'POST', body: options })

/**
 * 试算一条样例路径会不会被这套规则挡下。
 *
 * 保存前就能用 —— 它不走所选的监控目录，规则直接随请求带上，
 * 所以「还没添加这个目录」时也能试。纯计算，不影响任何配置。
 * basePath 传当前选的监控目录，这样整条绝对路径粘进来也能认。
 */
export const previewWatchFilters = (body: {
  path: string
  basePath?: string
  filters: WatchFilters
}): Promise<WatchFilterPreview> =>
  request<WatchFilterPreview>('/watchpoints/filter-preview', { method: 'POST', body })

/**
 * 对监控目录下已存在文件做命中预览——编辑页「命中预览」的列表版。
 *
 * 列出目录里已有的候选视频，逐个标出会被处理还是被规则挡下。
 * path 是要列文件的目录（当前选的监控目录），递归与否由前端按监控目录传。
 */
export const previewWatchFilterList = (body: {
  path: string
  recursive: boolean
  filters: WatchFilters
}): Promise<WatchFilterListPreview> =>
  request<WatchFilterListPreview>('/watchpoints/filter-list-preview', { method: 'POST', body })

import { request } from './client'
import type { WatchPoint, WatchPointCreate, ScanOptions, ScanResult } from './types'

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

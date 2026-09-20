import { request } from './client'
import type { Health, Stats, BrowseResult, ScanResult } from './types'

export const getHealth = (): Promise<Health> => request<Health>('/health')

export const getStats = (): Promise<Stats> => request<Stats>('/stats')

/** 目录浏览：不传 path 时返回可访问根目录列表 */
export const browse = (path?: string): Promise<BrowseResult> => {
  const q = path ? `?path=${encodeURIComponent(path)}` : ''
  return request<BrowseResult>(`/browse${q}`)
}

/** 扫描全部已启用的监控目录并入队 */
export const scanAll = (): Promise<ScanResult> =>
  request<ScanResult>('/scan', { method: 'POST' })

import { request } from './client'
import type { Health, Stats, BrowseResult, ScanOptions, ScanResult } from './types'

export const getHealth = (): Promise<Health> => request<Health>('/health')

export const getStats = (): Promise<Stats> => request<Stats>('/stats')

/** 目录浏览：不传 path 时返回可访问根目录列表 */
export const browse = (path?: string): Promise<BrowseResult> => {
  const q = path ? `?path=${encodeURIComponent(path)}` : ''
  return request<BrowseResult>(`/browse${q}`)
}

/**
 * 扫描全部监控目录并入队。
 * options 指定「就这一次」原片怎么处理；不传则各目录按自己的设置来。
 *
 * 界面目前**没有**入口：手动扫描统一走监控目录页每行的 `scanWatchpoint`，
 * 一次对准一个目录。这个客户端函数留着，是为了接口还在（脚本在用），
 * 将来要恢复全局扫描时不必再对着后端找一遍。
 */
export const scanAll = (options?: ScanOptions): Promise<ScanResult> =>
  request<ScanResult>('/scan', { method: 'POST', body: options })

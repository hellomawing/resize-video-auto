import { request } from './client'
import type { ConfigBundle, ImportResult, Settings } from './types'

export const getSettings = (): Promise<Settings> => request<Settings>('/settings')

export const updateSettings = (body: Settings): Promise<Settings> =>
  request<Settings>('/settings', { method: 'PUT', body })

export const exportConfig = (): Promise<ConfigBundle> =>
  request<ConfigBundle>('/settings/export')

// 导入会替换设置、按路径合并监控目录，调用方必须先让用户确认过
export const importConfig = (bundle: unknown): Promise<ImportResult> =>
  request<ImportResult>('/settings/import', { method: 'POST', body: bundle })

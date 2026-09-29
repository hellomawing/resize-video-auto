import { request } from './client'
import type { ConfigBundle, ImportResult, Settings, SettingsPatch } from './types'

/**
 * 本页的客户端标识。随每次保存报给后端，后端把它原样带进 settings.updated 广播。
 *
 * 用途：让发起端认出「这条广播是我自己触发的」。它手里已经有本次保存的返回值，
 * 若再去 GET 一次全量设置，会把同一个页面上别的 tab 里还没提交的输入冲掉。
 * 每个标签页各自一个（模块级随机串），所以多开页面时互不影响。
 */
export const CLIENT_ID = `web-${Math.random().toString(36).slice(2, 10)}`

const withSource = (path: string): string =>
  `${path}?source=${encodeURIComponent(CLIENT_ID)}`

export const getSettings = (): Promise<Settings> => request<Settings>('/settings')

/** 整体替换：只用于导入这类整份覆盖的场景 */
export const updateSettings = (body: Settings): Promise<Settings> =>
  request<Settings>(withSource('/settings'), { method: 'PUT', body })

/** 字段级更新：只改传进来的字段，界面上「改完即生效」的那些项走这条 */
export const patchSettings = (body: SettingsPatch): Promise<Settings> =>
  request<Settings>(withSource('/settings'), { method: 'PATCH', body })

export const exportConfig = (): Promise<ConfigBundle> =>
  request<ConfigBundle>('/settings/export')

// 导入会替换设置、按路径合并监控目录，调用方必须先让用户确认过
export const importConfig = (bundle: unknown): Promise<ImportResult> =>
  request<ImportResult>('/settings/import', { method: 'POST', body: bundle })

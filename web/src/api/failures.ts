import { request } from './client'
import type { FailureList } from './types'

/** 切不动的文件列表（最近失败的排前面） */
export const listFailures = (): Promise<FailureList> => request<FailureList>('/failures')

/**
 * 再试一次。后端会先忘掉失败记录 —— 否则扫描器看到「文件没变、上次失败过」
 * 会继续拦着它，点了也没用 —— 然后重新入队。
 * 原片处理方式按该文件所属监控目录的设置走，找不到就用系统默认。
 */
export const retryFailure = (path: string): Promise<void> =>
  request<void>('/failures/retry', { method: 'POST', body: { path } })

/** 从列表移除。只删记录，不动磁盘上的文件；不传 paths 表示全部清掉。 */
export const clearFailures = (paths?: string[]): Promise<void> =>
  request<void>('/failures/clear', { method: 'POST', body: paths ? { paths } : {} })

import { request } from './client'
import type { Settings } from './types'

export const getSettings = (): Promise<Settings> => request<Settings>('/settings')

export const updateSettings = (body: Settings): Promise<Settings> =>
  request<Settings>('/settings', { method: 'PUT', body })

import { request } from './client'
import type { Schedule, ScheduleCreate } from './types'

export const listSchedules = (): Promise<Schedule[]> => request<Schedule[]>('/schedules')

export const createSchedule = (body: ScheduleCreate): Promise<Schedule> =>
  request<Schedule>('/schedules', { method: 'POST', body })

export const updateSchedule = (
  id: string,
  body: Partial<ScheduleCreate>,
): Promise<Schedule> => request<Schedule>(`/schedules/${id}`, { method: 'PUT', body })

export const deleteSchedule = (id: string): Promise<void> =>
  request<void>(`/schedules/${id}`, { method: 'DELETE' })

export const runSchedule = (id: string): Promise<void> =>
  request<void>(`/schedules/${id}/run`, { method: 'POST' })

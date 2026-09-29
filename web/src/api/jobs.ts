import { request } from './client'
import type { Job, JobList, JobLog, JobStatus, ClearBody } from './types'

export interface ListJobsParams {
  status?: JobStatus | ''
  q?: string
  limit?: number
  offset?: number
}

export const listJobs = (params: ListJobsParams): Promise<JobList> => {
  const qs = new URLSearchParams()
  if (params.status) qs.set('status', params.status)
  if (params.q) qs.set('q', params.q)
  if (params.limit !== undefined) qs.set('limit', String(params.limit))
  if (params.offset !== undefined) qs.set('offset', String(params.offset))
  const query = qs.toString()
  return request<JobList>(`/jobs${query ? `?${query}` : ''}`)
}

export const getJob = (id: string): Promise<Job> => request<Job>(`/jobs/${id}`)

export const getJobLog = (id: string): Promise<JobLog> =>
  request<JobLog>(`/jobs/${id}/log`)

export const retryJob = (id: string): Promise<void> =>
  request<void>(`/jobs/${id}/retry`, { method: 'POST' })

export const cancelJob = (id: string): Promise<void> =>
  request<void>(`/jobs/${id}/cancel`, { method: 'POST' })

export const deleteJob = (id: string): Promise<void> =>
  request<void>(`/jobs/${id}`, { method: 'DELETE' })

export const clearJobs = (statuses: JobStatus[]): Promise<void> =>
  request<void>('/jobs/clear', { method: 'POST', body: { statuses } as ClearBody })

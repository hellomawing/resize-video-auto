import { request } from './client'
import type { UndoPreview, UndoApplyBody, UndoResult } from './types'

export const undoPreview = (body: {
  path: string
  recursive: boolean
}): Promise<UndoPreview> => request<UndoPreview>('/undo/preview', { method: 'POST', body })

export const undoApply = (body: UndoApplyBody): Promise<UndoResult> =>
  request<UndoResult>('/undo/apply', { method: 'POST', body })

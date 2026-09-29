import { reactive } from 'vue'

// 轻量提示条：模块级单例，任意组件调用同一份状态。
export type ToastType = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  type: ToastType
  message: string
}

const state = reactive<{ items: ToastItem[] }>({ items: [] })
let seq = 0

function remove(id: number): void {
  const idx = state.items.findIndex((t) => t.id === id)
  if (idx >= 0) state.items.splice(idx, 1)
}

export function useToast() {
  function push(type: ToastType, message: string, duration = 3500): void {
    const id = ++seq
    state.items.push({ id, type, message })
    // 自动消失；用户点击也会触发 remove
    window.setTimeout(() => remove(id), duration)
  }

  return {
    items: state.items,
    remove,
    success: (message: string) => push('success', message),
    error: (message: string) => push('error', message),
    info: (message: string) => push('info', message),
  }
}

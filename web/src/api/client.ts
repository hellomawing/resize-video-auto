// 原生 fetch 封装：统一 baseURL、超时、错误文案提取。
// 不引入 axios，所有接口调用都走这里。

export interface RequestOptions {
  method?: string
  body?: unknown
  /** 超时时间（毫秒），默认 30 秒 */
  timeoutMs?: number
  /** 外部可传入的中止信号，用于组件卸载时取消请求 */
  signal?: AbortSignal
}

/** 统一错误：携带 HTTP 状态码，供上层决定是否重试或提示 */
export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const BASE = '/api'

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, timeoutMs = 30000, signal } = options

  // 超时与调用方主动取消共用一个控制器
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', () => controller.abort(), { once: true })
  }

  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })

    if (!res.ok) {
      // 优先取后端约定的 { detail } 人话文案，解析失败才退回 HTTP 状态文本
      let detail = res.statusText
      try {
        const data = (await res.json()) as unknown
        if (data && typeof data === 'object' && 'detail' in data) {
          const d = (data as { detail: unknown }).detail
          if (typeof d === 'string') detail = d
        }
      } catch {
        // 响应体非 JSON，保持 statusText
      }
      throw new ApiError(detail, res.status)
    }

    const contentType = res.headers.get('content-type') ?? ''
    if (!contentType.includes('application/json')) {
      // 204 或无响应体的写操作
      return undefined as T
    }
    return (await res.json()) as T
  } finally {
    clearTimeout(timer)
  }
}

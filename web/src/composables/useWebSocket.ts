import { ref } from 'vue'
import type { WsMessage } from '../api/types'

// 单例 WebSocket 连接：全局只建一条，断线指数退避重连（3s 起，封顶 30s）。
type Listener = (msg: WsMessage) => void

let socket: WebSocket | null = null
const listeners = new Set<Listener>()
const connected = ref(false)

let reconnectDelay = 3000
let reconnectTimer: number | null = null

/** 依据页面协议选择 ws / wss，避免硬编码 host 与端口。 */
function wsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss://' : 'ws://'
  return `${proto}${location.host}/api/ws`
}

function clearReconnect(): void {
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

function scheduleReconnect(): void {
  if (reconnectTimer !== null) return
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null
    // 退避翻倍，封顶 30 秒
    reconnectDelay = Math.min(reconnectDelay * 2, 30000)
    connect()
  }, reconnectDelay)
}

function connect(): void {
  try {
    socket = new WebSocket(wsUrl())
  } catch {
    // 极端情况下 new WebSocket 抛错，安排一次重连
    scheduleReconnect()
    return
  }

  socket.onopen = () => {
    connected.value = true
    reconnectDelay = 3000 // 连接成功，重置退避步长
  }

  socket.onmessage = (ev: MessageEvent) => {
    let msg: WsMessage
    try {
      msg = JSON.parse(ev.data as string) as WsMessage
    } catch {
      return // 非 JSON 帧忽略
    }
    listeners.forEach((fn) => fn(msg))
  }

  socket.onclose = () => {
    connected.value = false
    scheduleReconnect()
  }

  socket.onerror = () => {
    // 出错后由 onclose 统一走重连逻辑
    socket?.close()
  }
}

export function useWebSocket() {
  // 首次调用才真正建连，后续调用复用单例
  if (!socket) connect()

  /** 订阅消息，返回取消订阅函数 */
  function on(fn: Listener): () => void {
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  }

  return { connected, on }
}

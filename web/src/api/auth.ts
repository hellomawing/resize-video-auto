import { request } from './client'
import type { AuthStatus } from './types'

/** 当前访问密码状态：决定前端要不要弹登录框 */
export const getAuthStatus = (): Promise<AuthStatus> =>
  request<AuthStatus>('/auth/status')

/** 登录：成功后服务端下发会话 cookie。未配置密码时也照常通过 */
export const login = (password: string): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>('/auth/login', { method: 'POST', body: { password } })

/** 登出：清掉会话 cookie */
export const logout = (): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>('/auth/logout', { method: 'POST' })

/**
 * 设置 / 修改 / 关闭访问密码。
 * current 只有在已配置密码时才需要；newPassword 传空串 = 关闭。
 */
export const changePassword = (current: string, newPassword: string): Promise<AuthStatus> =>
  request<AuthStatus>('/auth/password', {
    method: 'POST',
    body: { current, newPassword },
  })
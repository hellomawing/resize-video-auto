import { ref } from 'vue'
import { getAuthStatus, login as apiLogin, logout as apiLogout } from '../api/auth'

/**
 * 访问密码的全局状态（单例）。
 *
 * state：
 *   - checking  启动时正在查询后端是否要求密码
 *   - open      无密码，直接可用
 *   - authed    需要密码，且本机已通过登录
 *   - locked    需要密码，且本机尚未登录（要弹登录框）
 *
 * 只有「需要密码」时才真正发生 LOGIN。未配置密码时 open 一路放行，
 * 不产生任何登录负担。
 */
export type AuthState = 'checking' | 'open' | 'authed' | 'locked'

const state = ref<AuthState>('checking')

export function useAuth() {
  /** 启动时或登录/登出后刷新状态 */
  async function refresh(): Promise<void> {
    try {
      // 后端返回 enabled（要不要密码）+ authed（这个请求带的会话还有效吗）。
      // 只按 enabled 判会误伤：刷新 / 新开标签页时明明登录过，却又被弹回登录页，
      // 等于逼用户每次刷新都重输密码。authed 才区分得出「这台设备登录了没」。
      const res = await getAuthStatus()
      if (!res.enabled) state.value = 'open'
      else state.value = res.authed ? 'authed' : 'locked'
    } catch {
      // status 是免鉴权端点，正常不会失败；失败按「未配置密码」处理，
      // 免得后端异常时整个前端起不来
      state.value = 'open'
    }
  }

  /** 登录：成功则切到 authed。返回错误信息（成功返回 null）。 */
  async function doLogin(password: string): Promise<string | null> {
    try {
      await apiLogin(password)
      state.value = 'authed'
      return null
    } catch (e) {
      return e instanceof Error ? e.message : '登录失败'
    }
  }

  /** 登出 */
  async function doLogout(): Promise<void> {
    try {
      await apiLogout()
    } catch {
      // 网络异常也要退出到登录态，本地状态优先
    }
    state.value = 'locked'
  }

  /** 设置/关闭密码后同步到本地状态 */
  function sync(enabled: boolean): void {
    state.value = enabled ? 'authed' : 'open'
  }

  function markAuthed(): void {
    state.value = 'authed'
  }

  /** 强制回到登录页（改密后旧会话已失效） */
  function forceRelogin(): void {
    state.value = 'locked'
  }

  return {
    state,
    refresh,
    login: doLogin,
    logout: doLogout,
    sync,
    markAuthed,
    forceRelogin,
  }
}
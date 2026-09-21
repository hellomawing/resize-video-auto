import { reactive } from 'vue'
import { useToast } from './useToast'
import { undoApply } from '../api/undo'
import type { ResettableDir, ScanResult } from '../api/types'

/**
 * 扫描结果的兜底接管：扫到「切片已不在的原片」时，弹一句问要不要重切。
 *
 * 这类原片（`xxx#origin.MP4`）本来是「已完成」的保护标记 —— 不跳过的话，
 * 工具会把自己刚切完的原片再切一遍。但切片一旦被删掉或搬走，这个标记就
 * 名不副实了：分割结果已经没了，原片其实是可以重新分割的。后端会把它
 * 单独标出来（resettableTotal），这里负责给用户一个明确的动作，
 * 而不是让他对着一句「已跳过」发呆。
 *
 * 做成 composable 是因为多个扫描入口（总览页、监控目录页）都要同一套行为，
 * 各写一遍迟早会走样。
 */
export function useScanRescue() {
  const toast = useToast()

  const state = reactive({
    open: false,
    busy: false,
    total: 0,
    names: [] as string[],
    dirs: [] as ResettableDir[],
  })

  // 恢复后要再扫一遍，用的还是发起这次扫描的那个入口
  let rescan: (() => Promise<ScanResult>) | null = null

  /**
   * 扫描结果先过这里。
   * 返回 true 表示已经接管（弹窗了），调用方就别再弹普通的成功提示，
   * 免得「扫描完成」和「要不要重切」两句话同时冒出来。
   */
  function offer(r: ScanResult, rescanFn: () => Promise<ScanResult>): boolean {
    const n = r.resettableTotal || 0
    if (!n) return false
    state.total = n
    state.names = (r.ignored || []).filter((i) => i.resettable).map((i) => i.name)
    state.dirs = r.resettableDirs || []
    rescan = rescanFn
    state.open = true
    return true
  }

  async function confirm(): Promise<void> {
    if (state.busy) return
    state.busy = true
    try {
      let restored = 0
      // 逐目录恢复：扫描可能跨多个监控目录，接口是按目录处理的。
      // 只恢复原名，不删任何东西（deleteSlices/restoreOrigin 都关掉）。
      for (const d of state.dirs) {
        const r = await undoApply({
          path: d.path,
          recursive: d.recursive,
          deleteSlices: false,
          restoreOrigin: false,
          restoreOriginOnly: true,
          trash: true,
        })
        restored += r.restoredOrphans || 0
      }
      state.open = false
      if (!restored) {
        toast.info('没有恢复任何文件，可能已被其它操作处理过了')
        return
      }
      toast.success(`已恢复 ${restored} 个原片，正在重新分割…`)
      if (rescan) {
        const again = await rescan()
        toast.success(again.message || '已重新扫描')
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '恢复失败')
    } finally {
      state.busy = false
    }
  }

  function cancel(): void {
    state.open = false
  }

  return { state, offer, confirm, cancel }
}

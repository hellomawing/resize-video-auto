import { computed, ref } from 'vue'
import type { MarkValue, ScanOptions } from '../api/types'

/** 一次「本次扫描怎么处理原片」的取值快照 */
export interface ScanOverride {
  /** 没选覆盖时为 undefined —— 那种情况交给后端按「目录 → 系统」的层级去解析 */
  options?: ScanOptions
  /** 当次选择的人话描述。必须在发起扫描时抓下来，复位后就取不到了 */
  text: string
}

/**
 * 手动扫描的「就这一次」原片处理方式。
 *
 * 空串 = 跟随该目录设置、再退回系统设置，这是默认也是常态。
 *
 * 用完必须复位：「删除源文件」不可逆，一个被遗忘的界面状态会在用户下一次
 * 随手点「扫描」时把原片删掉。宁可每次都让他重新选，也不留这个隐患。
 *
 * 概览页的「立即扫描」和监控目录页的行内「扫描」共用这一份，是为了让
 * 两处的语义与危险提示不会各写一套、某一边漏掉警告。
 */
export function useScanOverride() {
  const mark = ref<MarkValue>('')
  const dir = ref('')

  /** 本次是否会删源文件。入口处据此决定要不要先拦一道确认 */
  const willDelete = computed(() => mark.value === 'delete')

  /** 抓一份当前取值的快照 */
  function take(): ScanOverride {
    if (!mark.value) return { text: '跟随各级设置' }
    const options: ScanOptions = { markSource: mark.value }
    // 目录名只在 move 下有意义；空串表示跟随系统设置里的目录名
    if (dir.value) options.sourceDir = dir.value
    const text = describe()
    return { options, text }
  }

  /** 复位成「跟随各级设置」 */
  function reset(): void {
    mark.value = ''
    dir.value = ''
  }

  /** 给后端摘要收尾：用了覆盖才追加说明，没用就别多嘴 */
  function annotate(message: string, snap: ScanOverride): string {
    return snap.options ? `${message}（本次按「${snap.text}」处理源片）` : message
  }

  function describe(): string {
    switch (mark.value) {
      case 'rename':
        return '加 #origin 后缀留在原处'
      case 'move':
        return `移到 ${dir.value || '归档子文件夹'}`
      case 'none':
        return '不处理原片'
      case 'delete':
        return '删除源文件'
      default:
        return '跟随各级设置'
    }
  }

  return { mark, dir, willDelete, take, reset, annotate }
}

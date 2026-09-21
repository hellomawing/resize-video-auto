// 原片处理方式的文案工具。
//
// 下拉框里那几条选项文案由 MarkSourcePicker 统一提供（三个入口共用同一个组件，
// 天生不会分叉）。这里只放「把一个取值说成一句人话」的部分，给两处共用：
//   1. 手动扫描结束后的提示语（useScanOverride）
//   2. 「跟随系统设置」后面那句「当前系统设置是哪种方式」
// 这两处说的是同一件事，措辞必须只有一个来源，否则改了选项文案却忘了改说明。

import type { MarkValue } from '../api/types'

/**
 * 把原片处理方式说成一句人话。
 *
 * 空串（跟随上级）本身没有确定行为，返回空串，由调用方决定怎么兜底措辞 ——
 * 扫描提示语说「跟随各级设置」，而「跟随系统设置」的说明处则应该干脆不显示。
 */
export function describeMark(mark: MarkValue, dir?: string): string {
  switch (mark) {
    case 'rename':
      return '加 #origin 后缀留在原处'
    case 'move':
      // 目录名留空表示跟随系统设置里的目录名，所以只报「归档」不报具体名字
      return dir ? `移到 ${dir}` : '移到归档子文件夹'
    case 'none':
      return '不处理原片'
    case 'delete':
      return '删除源文件'
    default:
      return ''
  }
}

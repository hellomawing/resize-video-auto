<script setup lang="ts">
// 原片处理方式选择器。三处共用同一份控件：
//   系统设置（默认值）、监控目录（本目录覆盖）、手动扫描（仅这一次）
// 共用是为了让「怎么设」和「临时改一次」看到的是同一套措辞与警告，
// 不会出现某个入口少写一句危险提示的情况。
//
// 空串是一个**有意义的取值**，表示「跟随上级」——系统设置那一层没有上级，
// 所以那里的空串选项文案由调用方用 followLabel 覆盖掉或干脆不显示。
import type { MarkValue } from '../api/types'

// 与后端 config.DEFAULT_SOURCE_DIR 保持一致，只用于输入框的占位提示
const DEFAULT_SOURCE_DIR = 'resize-video-origin-file'

withDefaults(
  defineProps<{
    /** 当前取值。空串 = 跟随上级设置 */
    modelValue: MarkValue
    /** 归档子目录名（仅 move 时有意义） */
    sourceDir?: string
    /** 「跟随」那一项的文案 */
    followLabel?: string
    /** 隐藏「跟随」项：系统设置那一层没有上级可跟随，必须给个确定值 */
    hideFollow?: boolean
    disabled?: boolean
    /** 表格行内用的紧凑尺寸 */
    compact?: boolean
  }>(),
  {
    sourceDir: '',
    followLabel: '跟随系统设置',
    hideFollow: false,
    disabled: false,
    compact: false,
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: MarkValue): void
  (e: 'update:sourceDir', value: string): void
}>()

function onMark(event: Event): void {
  emit('update:modelValue', (event.target as HTMLSelectElement).value as MarkValue)
}

function onDir(event: Event): void {
  emit('update:sourceDir', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <div class="mark-picker">
    <div class="mark-row">
      <select
        class="select"
        :class="{ 'select--sm': compact }"
        :value="modelValue"
        :disabled="disabled"
        @change="onMark"
      >
        <option v-if="!hideFollow" value="">{{ followLabel }}</option>
        <option value="rename">加 #origin 后缀，留在原处</option>
        <option value="move">移到归档子文件夹</option>
        <option value="none">不处理原片</option>
        <option value="delete">删除源文件（危险）</option>
      </select>

      <input
        v-if="modelValue === 'move'"
        class="input"
        :class="{ 'input--sm': compact }"
        :value="sourceDir"
        :disabled="disabled"
        :placeholder="DEFAULT_SOURCE_DIR"
        title="归档子文件夹名，不存在会自动创建"
        @change="onDir"
      />
    </div>

    <!-- 两个会导致「找不回原片」的选项必须当场说清楚，不能只靠颜色或名字 -->
    <div v-if="modelValue === 'delete'" class="mark-hint mark-hint--danger">
      切分成功后原片会被永久删除，无法恢复
    </div>
    <div v-else-if="modelValue === 'none'" class="mark-hint">
      原片保持原名，下次扫描可能被当成新视频再切一遍
    </div>
    <div v-else-if="modelValue === 'move'" class="mark-hint">
      原片移到同级子文件夹，文件名不变
    </div>
  </div>
</template>

<style scoped>
.mark-picker {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.mark-row {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}
.select--sm,
.input--sm {
  height: 30px;
  padding: 0 var(--space-2);
  font-size: var(--font-size-sm);
}
.select--sm {
  width: auto;
  min-width: 180px;
}
.input--sm {
  width: auto;
  min-width: 150px;
}
.mark-hint {
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  line-height: 1.5;
}
.mark-hint--danger {
  color: var(--color-danger);
}
</style>

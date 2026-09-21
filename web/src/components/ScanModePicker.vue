<script setup lang="ts">
// 扫描方式选择器。表格行内和新增/编辑弹窗里共用同一份控件，
// 保证「怎么设」和「怎么改」看到的是同一套选项，不会出现两处不一致。
//
// 为什么是一个下拉而不是「启用 + 自动扫描」两个开关：两个开关会组合出
// 「启用了但不自动扫」这种需要停下来想一下的状态；一个下拉直接说清
// 「这个目录按什么节奏扫」，包括「不自动扫，只手动」。
import type { ScanMode } from '../api/types'

// 档位限定成 24 的约数，理由见后端 config.SCAN_INTERVALS 的注释：
// 只有能整除 24，「每 N 小时」才能从 0 点起均匀铺满一天，不留空档。
const INTERVALS = [1, 2, 3, 4, 6, 8, 12, 24]

defineProps<{
  scanMode: ScanMode
  scanIntervalHours: number
  scanTime: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:scanMode', value: ScanMode): void
  (e: 'update:scanIntervalHours', value: number): void
  (e: 'update:scanTime', value: string): void
}>()

function onMode(event: Event): void {
  emit('update:scanMode', (event.target as HTMLSelectElement).value as ScanMode)
}

function onInterval(event: Event): void {
  emit('update:scanIntervalHours', Number((event.target as HTMLSelectElement).value))
}

function onTime(event: Event): void {
  emit('update:scanTime', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <div class="scan-picker">
    <select class="select select--sm" :value="scanMode" :disabled="disabled" @change="onMode">
      <option value="realtime">实时监听</option>
      <option value="interval">每隔几小时</option>
      <option value="daily">每天定时</option>
      <option value="manual">仅手动</option>
    </select>

    <template v-if="scanMode === 'interval'">
      <span class="scan-picker-sep">每</span>
      <select class="select select--sm" :value="scanIntervalHours" :disabled="disabled" @change="onInterval">
        <option v-for="h in INTERVALS" :key="h" :value="h">{{ h }} 小时</option>
      </select>
    </template>

    <input
      v-else-if="scanMode === 'daily'"
      class="input input--time"
      type="time"
      :value="scanTime"
      :disabled="disabled"
      @change="onTime"
    />
  </div>
</template>

<style scoped>
.scan-picker {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}
.scan-picker-sep {
  color: var(--color-text-soft);
  font-size: var(--font-size-sm);
  flex-shrink: 0;
}
.select--sm {
  width: auto;
  min-width: 104px;
  height: 30px;
  padding: 0 var(--space-2);
  font-size: var(--font-size-sm);
}
.input--time {
  width: auto;
  height: 30px;
  padding: 0 var(--space-2);
  font-size: var(--font-size-sm);
}
</style>

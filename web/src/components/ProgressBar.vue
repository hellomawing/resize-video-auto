<script setup lang="ts">
import { computed } from 'vue'

// 进度条：value 取值 0~1。运行中显示百分比与分段完成数。
const props = withDefaults(
  defineProps<{
    value: number
    partsDone?: number
    partsTotal?: number
    color?: string
  }>(),
  { partsDone: undefined, partsTotal: undefined, color: 'var(--color-primary)' },
)

const percent = computed(() => {
  const v = Number.isFinite(props.value) ? props.value : 0
  return Math.max(0, Math.min(1, v)) * 100
})
</script>

<template>
  <div class="progress">
    <div class="progress-track">
      <div class="progress-fill" :style="{ width: `${percent}%`, background: color }" />
    </div>
    <div class="progress-meta">
      <span>{{ percent.toFixed(0) }}%</span>
      <span v-if="partsTotal !== undefined" class="faint">第 {{ partsDone ?? 0 }}/{{ partsTotal }} 段</span>
    </div>
  </div>
</template>

<style scoped>
.progress {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.progress-track {
  flex: 1;
  height: 8px;
  background: var(--color-muted-soft);
  border-radius: 999px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.3s ease;
}
.progress-meta {
  display: flex;
  gap: var(--space-2);
  font-size: var(--font-size-xs);
  color: var(--color-text-soft);
  white-space: nowrap;
  min-width: 84px;
  justify-content: flex-end;
}
</style>

<script setup lang="ts">
import { computed } from 'vue'
import type { JobStatus } from '../api/types'

// 任务状态徽章，颜色严格按契约第 8 节。
const props = defineProps<{ status: JobStatus }>()

const META: Record<JobStatus, { label: string; color: string; bg: string }> = {
  success: { label: '成功', color: 'var(--color-success)', bg: 'var(--color-success-soft)' },
  failed: { label: '失败', color: 'var(--color-danger)', bg: 'var(--color-danger-soft)' },
  running: { label: '运行中', color: 'var(--color-primary)', bg: 'var(--color-primary-soft)' },
  queued: { label: '排队中', color: 'var(--color-muted)', bg: 'var(--color-muted-soft)' },
  canceled: { label: '已取消', color: 'var(--color-muted)', bg: 'var(--color-muted-soft)' },
  skipped: { label: '已跳过', color: 'var(--color-muted)', bg: 'var(--color-muted-soft)' },
}

// 用计算属性解析，状态变化时徽章同步更新
const meta = computed(() => META[props.status])
</script>

<template>
  <span class="badge" :style="{ color: meta.color, background: meta.bg }">{{ meta.label }}</span>
</template>

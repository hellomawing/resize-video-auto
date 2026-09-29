<script setup lang="ts">
// 概览页统计卡片：标题 + 主数值 + 可选副文案，支持状态色强调。
withDefaults(
  defineProps<{
    title: string
    value: string | number
    hint?: string
    accent?: 'primary' | 'success' | 'danger' | 'muted'
    loading?: boolean
  }>(),
  { hint: '', accent: 'muted', loading: false },
)
</script>

<template>
  <div class="stat-card" :class="`stat-card--${accent}`">
    <div class="stat-title">{{ title }}</div>
    <div class="stat-value" :title="loading ? undefined : String(value)">
      <span v-if="loading" class="spinner" />
      <span v-else class="stat-value-text">{{ value }}</span>
    </div>
    <div v-if="hint" class="stat-hint" :title="hint">{{ hint }}</div>
  </div>
</template>

<style scoped>
.stat-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-border-strong);
  border-radius: var(--radius);
  padding: var(--space-4);
  box-shadow: var(--shadow-sm);
  /* 卡片内容里有 ffmpeg 路径这类不可断行的长串，
     min-width:0 才允许它被压缩，否则会把整行卡片撑出容器 */
  min-width: 0;
}
.stat-card--primary {
  border-left-color: var(--color-primary);
}
.stat-card--success {
  border-left-color: var(--color-success);
}
.stat-card--danger {
  border-left-color: var(--color-danger);
}
.stat-card--muted {
  border-left-color: var(--color-muted);
}
.stat-title {
  font-size: var(--font-size-sm);
  color: var(--color-text-soft);
}
.stat-value {
  font-size: var(--font-size-2xl);
  font-weight: 600;
  margin-top: var(--space-2);
  min-height: 32px;
  display: flex;
  align-items: center;
  min-width: 0;
}
/* 数值超长时截断成一行，完整内容放 title 里；避免长版本串把卡片拉高 */
.stat-value-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stat-hint {
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  margin-top: var(--space-1);
  /* 路径是连续无空格的串，允许任意位置换行，不然会横向溢出；
     再限两行，避免一条长路径把卡片拉得比同排的高出一大截。
     完整内容仍可通过 title 悬浮查看。 */
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>

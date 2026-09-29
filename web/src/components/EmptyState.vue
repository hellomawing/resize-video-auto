<script setup lang="ts">
// 空状态：列表/表格无数据时统一展示，避免留白屏。
withDefaults(
  defineProps<{
    text: string
    hint?: string
  }>(),
  { hint: '' },
)
</script>

<template>
  <div class="empty">
    <div class="empty-icon">🗂</div>
    <div class="empty-text">{{ text }}</div>
    <div v-if="hint" class="empty-hint">{{ hint }}</div>
    <slot />
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-6) var(--space-4);
  color: var(--color-text-faint);
  text-align: center;
}
.empty-icon {
  font-size: 32px;
  opacity: 0.6;
}
.empty-text {
  font-size: var(--font-size);
  color: var(--color-text-soft);
}
.empty-hint {
  font-size: var(--font-size-xs);
}
</style>

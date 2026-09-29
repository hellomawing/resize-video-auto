<script setup lang="ts">
import Modal from './Modal.vue'

// 「切片已不在的原片」提示框。
// 只负责呈现与确认，恢复动作本身由 useScanRescue 的 confirm() 做 ——
// 这个组件不该知道怎么调接口。
withDefaults(
  defineProps<{
    modelValue: boolean
    /** 可重新分割的原片总数（明细可能只列出前几条） */
    total: number
    names: string[]
    busy?: boolean
  }>(),
  { busy: false },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'confirm'): void
}>()
</script>

<template>
  <Modal
    :model-value="modelValue"
    title="发现有可以重新分割的原片"
    @update:model-value="emit('update:modelValue', false)"
  >
    <p class="lead">
      目录里有 <b>{{ total }}</b> 个原片，它们的<b>分割结果已经不在</b>了
      （切片被删除或移走），所以扫描时被当成「已处理」跳过：
    </p>
    <ul class="names">
      <li v-for="n in names" :key="n">{{ n }}</li>
    </ul>
    <p v-if="total > names.length" class="more">
      另有 {{ total - names.length }} 个未列出
    </p>
    <p class="note">
      恢复原名后，它们会重新变回待处理文件，并<b>立即重新分割一次</b>。
      如果暂时不想重切，可以先去「监控目录」页把扫描方式改成
      <b>仅手动</b>，再自己挑时间点扫描。
    </p>

    <template #footer>
      <button class="btn" :disabled="busy" @click="emit('update:modelValue', false)">
        稍后再说
      </button>
      <button class="btn btn--primary" :disabled="busy" @click="emit('confirm')">
        {{ busy ? '处理中…' : '恢复并重新分割' }}
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.lead {
  margin: 0 0 var(--space-3);
  line-height: 1.7;
}

.names {
  margin: 0 0 var(--space-3);
  padding: var(--space-2) var(--space-3);
  max-height: 180px;
  overflow: auto;
  list-style: none;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
}

.names li {
  padding: 2px 0;
  word-break: break-all;
}

.more {
  margin: calc(-1 * var(--space-2)) 0 var(--space-3);
  color: var(--color-text-faint);
  font-size: var(--font-size-xs);
}

.note {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  color: var(--color-text-soft);
  background: var(--color-warning-soft);
  border-radius: var(--radius);
  font-size: var(--font-size-sm);
  line-height: 1.7;
}
</style>

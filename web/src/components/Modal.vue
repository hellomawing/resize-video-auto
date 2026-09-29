<script setup lang="ts">
import { watch } from 'vue'

// 通用对话框：基于 modelValue 控制开关，支持点遮罩 / 关闭按钮关闭，
// 点击内容区不会关闭（事件已 stopPropagation）。
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title?: string
    wide?: boolean
  }>(),
  { title: '', wide: false },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

function close(): void {
  emit('update:modelValue', false)
}

// 打开时锁定背景滚动，关闭时恢复
watch(
  () => props.modelValue,
  (open) => {
    document.body.style.overflow = open ? 'hidden' : ''
  },
)
</script>

<template>
  <teleport to="body">
    <div v-if="modelValue" class="modal-backdrop" @click.self="close">
      <div class="modal" :class="{ 'modal--wide': wide }" @click.stop>
        <div class="modal-head">
          <div class="modal-title">{{ title }}</div>
          <button class="modal-close" aria-label="关闭" @click="close">×</button>
        </div>
        <div class="modal-body">
          <slot />
        </div>
        <div v-if="$slots.footer" class="modal-foot">
          <slot name="footer" />
        </div>
      </div>
    </div>
  </teleport>
</template>

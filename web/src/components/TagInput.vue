<script setup lang="ts">
import { ref } from 'vue'

// 标签输入：回车添加、点 × 删除。用于 ext / ignoreSuffixes 等列表字段。
const props = withDefaults(
  defineProps<{
    modelValue: string[]
    placeholder?: string
    /** 输入时是否自动去除首尾空格（扩展名等需保留原样时可关闭） */
    trim?: boolean
  }>(),
  { placeholder: '输入后回车添加', trim: true },
)

const emit = defineEmits<{ (e: 'update:modelValue', value: string[]): void }>()

const draft = ref('')

function add(): void {
  let val = props.trim ? draft.value.trim() : draft.value
  if (!val) return
  // 去重：已存在则忽略，避免重复提交后端
  if (!props.modelValue.includes(val)) {
    emit('update:modelValue', [...props.modelValue, val])
  }
  draft.value = ''
}

function removeAt(index: number): void {
  const next = props.modelValue.slice()
  next.splice(index, 1)
  emit('update:modelValue', next)
}
</script>

<template>
  <div class="tags">
    <span v-for="(tag, i) in modelValue" :key="tag" class="tag">
      {{ tag }}
      <button type="button" aria-label="删除" @click="removeAt(i)">×</button>
    </span>
    <input
      v-model="draft"
      class="tag-input"
      :placeholder="modelValue.length ? '' : placeholder"
      @keydown.enter.prevent="add"
      @keydown.,.prevent="add"
    />
  </div>
</template>

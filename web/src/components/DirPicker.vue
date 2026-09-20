<script setup lang="ts">
import { ref, watch } from 'vue'
import Modal from './Modal.vue'
import { browse } from '../api/system'
import { useToast } from '../composables/useToast'
import type { BrowseResult } from '../api/types'

// 目录选择器（弹窗）：基于 /api/browse 逐级浏览，可切换白名单根目录。
// modelValue 为已选路径；open 控制弹窗显隐。父组件用 v-model 绑定路径、v-model:open 控制开关。
const props = defineProps<{
  modelValue: string
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'update:open', value: boolean): void
}>()

const toast = useToast()

const current = ref('')
const roots = ref<string[]>([])
const dirs = ref<BrowseResult['dirs']>([])
const parent = ref('')
const error = ref<string | null>(null)
const loading = ref(false)

async function load(path: string): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await browse(path || undefined)
    roots.value = res.roots
    dirs.value = res.dirs
    parent.value = res.parent
    current.value = res.path
    if (res.error) error.value = res.error
  } catch (e) {
    error.value = e instanceof Error ? e.message : '无法浏览该目录'
    toast.error(error.value)
  } finally {
    loading.value = false
  }
}

// 打开弹窗时初始化：以已选路径或首个根目录作为起点
watch(
  () => props.open,
  (open) => {
    if (open) {
      const start = props.modelValue || ''
      void load(start)
    }
  },
)

function chooseDir(path: string): void {
  void load(path)
}

function confirm(): void {
  emit('update:modelValue', current.value)
  emit('update:open', false)
}

function cancel(): void {
  emit('update:open', false)
}
</script>

<template>
  <Modal :model-value="open" title="选择目录" wide @update:model-value="cancel">
    <div class="picker">
      <div class="picker-bar">
        <button class="btn btn--sm" :disabled="!parent || loading" @click="chooseDir(parent)">
          ↑ 上级
        </button>
        <select class="select" :value="current" @change="chooseDir(($event.target as HTMLSelectElement).value)">
          <option v-for="r in roots" :key="r" :value="r">{{ r }}</option>
          <option v-if="current && !roots.includes(current)" :value="current">{{ current }}</option>
        </select>
      </div>

      <div v-if="error" class="picker-error">{{ error }}</div>

      <div class="picker-list">
        <div v-if="loading" class="picker-loading"><span class="spinner" /> 加载中…</div>
        <div v-else-if="dirs.length === 0" class="picker-empty">该目录下没有可进入的子目录</div>
        <button
          v-for="d in dirs"
          :key="d.path"
          class="picker-item"
          @click="chooseDir(d.path)"
        >
          <span class="picker-folder">📁</span>
          <span class="text-ellipsis">{{ d.name }}</span>
        </button>
      </div>
    </div>

    <template #footer>
      <div class="row-between grow">
        <span class="faint">当前路径：{{ current || '（根）' }}</span>
        <div class="row">
          <button class="btn" @click="cancel">取消</button>
          <button class="btn btn--primary" @click="confirm">选择此目录</button>
        </div>
      </div>
    </template>
  </Modal>
</template>

<style scoped>
.picker-bar {
  display: flex;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}
.picker-bar .select {
  flex: 1;
}
.picker-error {
  background: var(--color-danger-soft);
  color: var(--color-danger);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  margin-bottom: var(--space-3);
}
.picker-list {
  max-height: 320px;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
}
.picker-loading,
.picker-empty {
  padding: var(--space-5);
  text-align: center;
  color: var(--color-text-faint);
  font-size: var(--font-size-sm);
}
.picker-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-3);
  border: none;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
  cursor: pointer;
  font-size: var(--font-size-sm);
  text-align: left;
}
.picker-item:last-child {
  border-bottom: none;
}
.picker-item:hover {
  background: var(--color-primary-soft);
}
.picker-folder {
  flex-shrink: 0;
}
</style>

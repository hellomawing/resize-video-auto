<script setup lang="ts">
import { ref, watch } from 'vue'
import Modal from './Modal.vue'
import { browse } from '../api/system'
import type { BrowseResult, DirShortcut } from '../api/types'

// 目录选择器（弹窗）：基于 /api/browse 逐级浏览，可切换白名单根目录。
// modelValue 为已选路径；open 控制弹窗显隐。父组件用 v-model 绑定路径、v-model:open 控制开关。
//
// 为什么除了「逐级浏览」还要有「常用目录」和「手动输入路径」两个入口：
// fnOS 这类系统把存储池根目录（/vol1）的权限位设成 000、连一条扩展 ACL 都没有，
// 内核拒绝对它 readdir —— 于是**从根往下点的第一级就是死的**，用户会看到
// 「没有权限」并且再也走不动（但直接访问 /vol1/1000/… 完全正常）。
// 所以这里给两条绕过它的路：点「常用目录」直达，或者把完整路径敲进来。
const props = defineProps<{
  modelValue: string
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'update:open', value: boolean): void
}>()

const current = ref('')
const roots = ref<string[]>([])
const missingRoots = ref<string[]>([])
const dirs = ref<BrowseResult['dirs']>([])
const shortcuts = ref<DirShortcut[]>([])
const parent = ref('')
const error = ref<string | null>(null)
const loading = ref(false)
// 手动输入的完整路径
const typed = ref('')

const KIND_LABELS: Record<DirShortcut['kind'], string> = {
  watchpoint: '监控',
  job: '最近',
  setting: '输出',
}

async function load(path: string): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await browse(path || undefined)
    roots.value = res.roots
    missingRoots.value = res.missingRoots || []
    dirs.value = res.dirs
    shortcuts.value = res.shortcuts || []
    parent.value = res.parent ?? ''
    current.value = res.path
    typed.value = res.path
    if (res.error) error.value = res.error
  } catch (e) {
    // 这里不再另弹 toast：错误就显示在弹窗里，用户本来就在看着它
    error.value = e instanceof Error ? e.message : '无法浏览该目录'
  } finally {
    loading.value = false
  }
}

// 打开弹窗时初始化：以已选路径作为起点，没有就用根目录列表
watch(
  () => props.open,
  (open) => {
    if (open) {
      void load(props.modelValue || '')
    }
  },
)

function chooseDir(path: string): void {
  void load(path)
}

function goto(): void {
  const target = typed.value.trim()
  if (!target) {
    error.value = '请先输入完整路径（以 / 开头，例如 /vol1/1000/video-split-in）'
    return
  }
  void load(target)
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

      <div class="picker-bar">
        <input
          v-model="typed"
          class="input"
          placeholder="直接输入完整路径，如 /vol1/1000/video-split-in"
          @keyup.enter="goto"
        />
        <button class="btn" :disabled="loading" @click="goto">进入</button>
      </div>

      <div v-if="error" class="picker-error">{{ error }}</div>

      <div v-if="missingRoots.length" class="picker-hint">
        配置的可访问根目录里有 {{ missingRoots.length }} 个不存在：{{ missingRoots.join('、') }}
        —— 容器里可能没把它们挂载进来，所以没放进上面的下拉框。
      </div>

      <div v-if="shortcuts.length" class="picker-sect">
        <div class="picker-sect-title">常用目录（点一下直达）</div>
        <div class="picker-chips">
          <button
            v-for="s in shortcuts"
            :key="s.path"
            class="chip"
            :title="s.note ? `${s.path} — ${s.note}` : s.path"
            @click="chooseDir(s.path)"
          >
            <span class="chip-kind">{{ KIND_LABELS[s.kind] }}</span>
            <span class="text-ellipsis">{{ s.name }}</span>
          </button>
        </div>
      </div>

      <div class="picker-sect">
        <div class="picker-sect-title">子目录</div>
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
.picker-hint {
  font-size: var(--font-size-xs);
  color: var(--color-text-soft);
  line-height: 1.7;
  margin-bottom: var(--space-3);
}
.picker-error {
  background: var(--color-danger-soft);
  color: var(--color-danger);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  line-height: 1.7;
  margin-bottom: var(--space-3);
}
.picker-sect {
  margin-bottom: var(--space-3);
}
.picker-sect-title {
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  margin-bottom: var(--space-2);
}
.picker-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  max-width: 100%;
  padding: 4px 10px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface);
  cursor: pointer;
  font-size: var(--font-size-xs);
  text-align: left;
}
.chip:hover {
  background: var(--color-primary-soft);
}
.chip-kind {
  flex-shrink: 0;
  padding: 0 5px;
  border-radius: 999px;
  font-size: 11px;
  background: var(--color-primary-soft);
  color: var(--color-text-soft);
}
.picker-list {
  max-height: 260px;
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

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { browse } from '../api/system'
import type { BrowseEntry, DirShortcut } from '../api/types'

// 监控目录表单里的「路径选择」区块。**行内呈现，不再自带弹窗** ——
// 之前是「新增监控目录」弹窗里再开一个「选择目录」弹窗，两层叠着，
// 选完还得回头确认刚才选的是哪一层。现在自上而下三步走完：
//
//   1. 选存储位置 —— 容器里挂载进来的数据目录（docker-compose 的 volumes）
//   2. 选使用方式 —— 直接用这个位置，还是进它的子文件夹里挑一个
//   3. 选了「子文件夹」才展开逐级浏览
//
// 为什么第 1 步是「先定存储位置」：挂进来的通常是一整块存储空间
// （真机是 /vol1/1000/video-split-in → /test-video），从哪块空间里选是这个
// 表单的第一个决策；先定它，后面下钻才有明确边界，「上级」也才知道到哪为止。
//
// 「常用目录」和「可直接进入的目录」两条捷径保留：fnOS 这类系统把存储池根目录
// （/vol1）的权限位设成 000、连一条扩展 ACL 都没有，内核拒绝对它 readdir ——
// 从根往下点的第一级就是死的（但直接访问 /vol1/1000/… 完全正常）。
// 详见 docs/api.md 里 /api/browse 的说明。
const props = defineProps<{
  modelValue: string
  /** 所属弹窗是否打开。打开时才去拉存储位置，省得页面一进来就发请求 */
  active: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

type UseMode = 'root' | 'sub'

const roots = ref<string[]>([])
const root = ref('')
const mode = ref<UseMode>('root')
const browsePath = ref('')
const dirs = ref<BrowseEntry[]>([])
const shortcuts = ref<DirShortcut[]>([])
const suggestedRoots = ref<string[]>([])
const parent = ref('')
const error = ref<string | null>(null)
const loading = ref(false)

const KIND_LABELS: Record<DirShortcut['kind'], string> = {
  watchpoint: '监控',
  job: '最近',
  setting: '输出',
}

function pick(path: string): void {
  emit('update:modelValue', path)
}

/** 包含 path 的那个存储位置；多个根相互嵌套时取最长的（最精确的那个） */
function rootOf(path: string): string {
  let best = ''
  for (const r of roots.value) {
    if (path === r || path.startsWith(r.endsWith('/') ? r : r + '/')) {
      if (r.length > best.length) best = r
    }
  }
  return best
}

/**
 * 「上级」止步于存储位置本身 —— 再往上就出了可访问范围，后端也只会给 null。
 * 挂进来的常常是深层子目录（/vol1/1000/video-split-in → /test-video），
 * 照直往上走会摆出一个点了必然 403 的按钮。
 */
const canUp = computed(() => !!parent.value && browsePath.value !== root.value)

async function loadDir(path: string): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await browse(path)
    dirs.value = res.dirs
    shortcuts.value = res.shortcuts || []
    suggestedRoots.value = res.suggestedRoots || []
    parent.value = res.parent ?? ''
    browsePath.value = res.path || path
    if (res.error) error.value = res.error
    pick(browsePath.value)
  } catch (e) {
    // 不另弹 toast：错误就显示在表单里，用户本来就在看着它
    error.value = e instanceof Error ? e.message : '无法浏览该目录'
  } finally {
    loading.value = false
  }
}

/** 弹窗打开时初始化：拉存储位置，默认「直接使用」第一项 */
async function init(): Promise<void> {
  loading.value = true
  error.value = null
  mode.value = 'root'
  dirs.value = []
  parent.value = ''
  try {
    const res = await browse()
    roots.value = res.roots
    shortcuts.value = res.shortcuts || []
    suggestedRoots.value = res.suggestedRoots || []
    root.value = res.roots[0] || ''
    browsePath.value = root.value
    error.value = res.error
    pick(root.value)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '无法读取已挂载的目录'
  } finally {
    loading.value = false
  }
}

function onRoot(event: Event): void {
  root.value = (event.target as HTMLSelectElement).value
  browsePath.value = root.value
  if (mode.value === 'sub') {
    void loadDir(root.value)
  } else {
    pick(root.value)
  }
}

function setMode(next: UseMode): void {
  if (mode.value === next) return
  mode.value = next
  if (next === 'root') {
    pick(root.value)
    return
  }
  void loadDir(browsePath.value || root.value)
}

/** 点某个目录（子目录 / 捷径）：进去并选中它 */
function open(path: string): void {
  const hit = rootOf(path)
  if (hit) root.value = hit
  void loadDir(path)
}

function goUp(): void {
  if (canUp.value) void loadDir(parent.value)
}

// 弹窗每次打开都重新初始化（根目录可能因为改了 compose 而变过）；
// 关闭时不动，免得表单里已选好的路径被清掉。
watch(
  () => props.active,
  (on) => {
    if (on) void init()
  },
  { immediate: true },
)
</script>

<template>
  <div class="picker">
    <!-- ① 存储位置 -->
    <div class="picker-block">
      <div class="picker-step"><span class="picker-no">1</span>选择存储位置</div>
      <div v-if="loading && !roots.length" class="picker-loading">
        <span class="spinner" /> 读取已挂载的目录…
      </div>
      <select v-else class="select" :value="root" :disabled="!roots.length" @change="onRoot">
        <option v-for="r in roots" :key="r" :value="r">{{ r }}</option>
        <option v-if="!roots.length" value="">（没有检测到已挂载的目录）</option>
      </select>
      <div class="picker-hint">容器已挂载的目录，即 docker-compose 里 volumes 挂进来的路径</div>
    </div>

    <!-- ② 使用方式 -->
    <div class="picker-block">
      <div class="picker-step"><span class="picker-no">2</span>选择使用方式</div>
      <label class="picker-radio" :class="{ 'is-off': !root }">
        <input
          type="radio"
          name="vs-use-mode"
          :checked="mode === 'root'"
          :disabled="!root"
          @change="setMode('root')"
        />
        <span>
          直接使用这个文件夹
          <span class="picker-radio-path text-ellipsis">{{ root || '—' }}</span>
        </span>
      </label>
      <label class="picker-radio" :class="{ 'is-off': !root }">
        <input
          type="radio"
          name="vs-use-mode"
          :checked="mode === 'sub'"
          :disabled="!root"
          @change="setMode('sub')"
        />
        <span>选择它的子文件夹</span>
      </label>
    </div>

    <!-- ③ 子文件夹（选「选择它的子文件夹」才出现） -->
    <div v-if="mode === 'sub'" class="picker-block">
      <div class="picker-step"><span class="picker-no">3</span>选择子文件夹</div>

      <div class="picker-bar">
        <button class="btn btn--sm" type="button" :disabled="!canUp || loading" @click="goUp">
          ↑ 上级
        </button>
        <span class="picker-here text-ellipsis" :title="browsePath">{{ browsePath }}</span>
      </div>

      <div v-if="error" class="picker-error">{{ error }}</div>

      <div v-if="suggestedRoots.length" class="picker-sect">
        <div class="picker-sect-title">可直接进入的目录（点一下直达）</div>
        <div class="picker-chips">
          <button
            v-for="p in suggestedRoots"
            :key="p"
            class="chip"
            type="button"
            :title="p"
            @click="open(p)"
          >
            <span class="chip-kind">直达</span>
            <span class="text-ellipsis">{{ p }}</span>
          </button>
        </div>
      </div>

      <div v-if="shortcuts.length" class="picker-sect">
        <div class="picker-sect-title">常用目录（点一下直达）</div>
        <div class="picker-chips">
          <button
            v-for="s in shortcuts"
            :key="s.path"
            class="chip"
            type="button"
            :title="s.note ? `${s.path} — ${s.note}` : s.path"
            @click="open(s.path)"
          >
            <span class="chip-kind">{{ KIND_LABELS[s.kind] }}</span>
            <span class="text-ellipsis">{{ s.name }}</span>
          </button>
        </div>
      </div>

      <div class="picker-list">
        <div v-if="loading" class="picker-loading"><span class="spinner" /> 加载中…</div>
        <div v-else-if="dirs.length === 0" class="picker-empty">该目录下没有可进入的子目录</div>
        <button
          v-for="d in dirs"
          :key="d.path"
          class="picker-item"
          type="button"
          :title="d.path"
          @click="open(d.path)"
        >
          <span class="picker-folder">📁</span>
          <span class="picker-name text-ellipsis">{{ d.name }}</span>
        </button>
      </div>
    </div>

    <!-- 「直接使用」这一支遇到错误时（比如一个目录都没挂载）也要说出来 -->
    <div v-else-if="error" class="picker-error">{{ error }}</div>
  </div>
</template>

<style scoped>
.picker {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  /* 长路径不许把这层撑宽：下面每一层都靠它兜底 */
  min-width: 0;
}
.picker-block {
  min-width: 0;
}
.picker-step {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  margin-bottom: var(--space-2);
}
.picker-no {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--color-primary-soft);
  color: var(--color-primary-dark);
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.picker-hint {
  margin-top: var(--space-1);
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
}
.picker-radio {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  font-size: var(--font-size-sm);
  cursor: pointer;
  min-width: 0;
}
.picker-radio input {
  flex-shrink: 0;
}
.picker-radio > span {
  min-width: 0;
}
.picker-radio.is-off {
  color: var(--color-text-faint);
  cursor: not-allowed;
}
/* 「直接使用」后面跟着的那截路径：长了就省略，别把整行顶出去 */
.picker-radio-path {
  display: inline-block;
  max-width: 100%;
  vertical-align: bottom;
  color: var(--color-text-faint);
  font-size: var(--font-size-xs);
}
.picker-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  min-width: 0;
}
.picker-bar .btn {
  flex-shrink: 0;
}
.picker-here {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-xs);
  color: var(--color-text-soft);
}
.picker-error {
  background: var(--color-danger-soft);
  color: var(--color-danger);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  line-height: 1.7;
  overflow-wrap: anywhere;
  margin-bottom: var(--space-2);
}
.picker-loading,
.picker-empty {
  padding: var(--space-4);
  text-align: center;
  color: var(--color-text-faint);
  font-size: var(--font-size-sm);
}
.picker-sect {
  margin-bottom: var(--space-3);
  min-width: 0;
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
  min-width: 0;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  max-width: 100%;
  min-width: 0;
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
.chip .text-ellipsis {
  min-width: 0;
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
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  min-width: 0;
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
/* 目录名过长时省略，不许撑破弹窗（真机上的目录名能长到几十个字符） */
.picker-name {
  flex: 1;
  min-width: 0;
}
</style>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

// 日志查看器：等宽字体、自动滚底、可暂停、支持关键字过滤。
const props = withDefaults(
  defineProps<{
    lines: string[]
    loading?: boolean
  }>(),
  { loading: false },
)

const paused = ref(false)
const filter = ref('')
const scroller = ref<HTMLElement | null>(null)

// 过滤：子串匹配（大小写不敏感），空过滤则全量
const filtered = computed(() => {
  const kw = filter.value.trim().toLowerCase()
  if (!kw) return props.lines
  return props.lines.filter((l) => l.toLowerCase().includes(kw))
})

function scrollToBottom(): void {
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

// 日志新增时，未暂停才自动滚底
watch(
  () => props.lines.length,
  async () => {
    if (paused.value) return
    await nextTick()
    scrollToBottom()
  },
)

function jumpBottom(): void {
  paused.value = false
  scrollToBottom()
}
</script>

<template>
  <div class="log-viewer">
    <div class="log-toolbar">
      <input v-model="filter" class="input log-search" placeholder="过滤关键字…" />
      <button class="btn btn--sm" :class="{ 'btn--primary': !paused }" @click="paused = !paused">
        {{ paused ? '已暂停（点此继续滚底）' : '自动滚底中' }}
      </button>
      <button class="btn btn--sm" :disabled="paused" @click="jumpBottom">到底部</button>
      <span class="log-count faint">{{ filtered.length }} / {{ lines.length }} 行</span>
    </div>

    <div ref="scroller" class="log-body">
      <div v-if="loading" class="log-placeholder"><span class="spinner" /> 加载日志中…</div>
      <div v-else-if="filtered.length === 0" class="log-placeholder">暂无日志</div>
      <pre v-for="(line, i) in filtered" :key="i" class="log-line">{{ line }}</pre>
    </div>
  </div>
</template>

<style scoped>
.log-viewer {
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  overflow: hidden;
  background: #1e1e1e;
}
.log-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2);
  background: #2a2a2a;
  border-bottom: 1px solid #000;
  flex-wrap: wrap;
}
.log-search {
  max-width: 220px;
  height: 28px;
  background: #1e1e1e;
  border-color: #3a3a3a;
  color: #e6e6e6;
}
.log-search:focus {
  box-shadow: none;
  border-color: var(--color-primary);
}
.log-count {
  margin-left: auto;
  color: #9aa1ab;
  font-size: var(--font-size-xs);
}
.log-body {
  max-height: 360px;
  overflow-y: auto;
  padding: var(--space-2) var(--space-3);
}
.log-placeholder {
  color: #8a8a8a;
  font-size: var(--font-size-sm);
  padding: var(--space-4);
  text-align: center;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
}
.log-line {
  margin: 0;
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  line-height: 1.6;
  color: #d4d4d4;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>

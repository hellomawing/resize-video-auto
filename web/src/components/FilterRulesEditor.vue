<script setup lang="ts">
import { computed } from 'vue'
import type { FilterMode, FilterRule, WatchFilters } from '../api/types'

// 监控目录的「只看这些 / 不看这些」编辑器。
//
// 两组规则并排摆在一屏里，而不是各开一个弹窗：规则本身就少，摆在一起才能
// 一眼看出「仅限」和「排除」有没有打架（排除优先，同一个文件的结论只可能
// 是「排除」）。
//
// 文件类型**不是自由输入**，而是从系统设置里已启用的格式点选：手输一个
// 系统根本没启用的类型，规则看着配好了、实际什么都不会发生 —— 那是最难
// 排查的一类问题。
const props = defineProps<{
  modelValue: WatchFilters
  /** 系统设置里已启用的格式（「设置 → 处理的扩展名」） */
  availableExts: string[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: WatchFilters): void
}>()

const filters = computed(() => props.modelValue)

type NameKey = 'nameInclude' | 'nameExclude'
type ExtKey = 'extInclude' | 'extExclude'

function patch(next: Partial<WatchFilters>): void {
  emit('update:modelValue', { ...props.modelValue, ...next })
}

/**
 * 点选一个文件类型。选中的同时把另一组的同名项去掉 —— 同一个类型两边都选
 * 没有意义（排除永远赢），留着只会让人对着界面想「为什么这条没用」。
 * 后端 normalize 也做同样的清理，两边口径一致。
 */
function toggleExt(key: ExtKey, ext: string): void {
  const on = props.modelValue[key].includes(ext)
  const next = props.modelValue[key].filter((e) => e !== ext)
  const out: Partial<WatchFilters> = { [key]: on ? next : [...next, ext] } as Partial<WatchFilters>
  if (!on) {
    const other: ExtKey = key === 'extInclude' ? 'extExclude' : 'extInclude'
    out[other] = props.modelValue[other].filter((e) => e !== ext)
  }
  patch(out)
}

function addRule(key: NameKey): void {
  patch({ [key]: [...props.modelValue[key], { mode: 'contains', value: '' }] } as Partial<WatchFilters>)
}

function setRule(key: NameKey, index: number, rule: Partial<FilterRule>): void {
  const list = props.modelValue[key].map((r, i) => (i === index ? { ...r, ...rule } : r))
  patch({ [key]: list } as Partial<WatchFilters>)
}

function removeRule(key: NameKey, index: number): void {
  patch({ [key]: props.modelValue[key].filter((_, i) => i !== index) } as Partial<WatchFilters>)
}

function onValue(key: NameKey, index: number, event: Event): void {
  setRule(key, index, { value: (event.target as HTMLInputElement).value })
}

function onMode(key: NameKey, index: number, event: Event): void {
  setRule(key, index, { mode: (event.target as HTMLSelectElement).value as FilterMode })
}

/** 正则写错了就当场标出来（保存时也会再拦一道，见父页面） */
function regexError(rule: FilterRule): string {
  if (rule.mode !== 'regex' || !rule.value) return ''
  try {
    new RegExp(rule.value)
    return ''
  } catch (e) {
    return '正则写错了：' + (e instanceof Error ? e.message : String(e))
  }
}

/** 第一条写错的正则；父页面据此阻止保存 */
const invalidRule = computed(() => {
  for (const key of ['nameInclude', 'nameExclude'] as NameKey[]) {
    for (const rule of props.modelValue[key]) {
      if (regexError(rule)) return rule.value
    }
  }
  return ''
})

defineExpose({ invalidRule })

const PLACEHOLDER: Record<FilterMode, string> = {
  contains: '如：相机',
  regex: '如：^DJI_\\d{4}\\.mp4$',
}
</script>

<template>
  <div class="rules">
    <!-- ① 文件类型 -->
    <div class="rules-block">
      <div class="rules-title">文件类型</div>
      <div class="rules-row">
        <span class="rules-tag rules-tag--in">仅限</span>
        <div class="chips">
          <button
            v-for="e in availableExts"
            :key="'i' + e"
            class="chip"
            :class="{ 'is-on': filters.extInclude.includes(e) }"
            type="button"
            @click="toggleExt('extInclude', e)"
          >
            {{ e }}
          </button>
          <span v-if="!availableExts.length" class="rules-empty">
            系统设置里没有启用任何格式，请先到「设置 → 处理的扩展名」里勾选
          </span>
        </div>
      </div>
      <div class="rules-row">
        <span class="rules-tag rules-tag--out">排除</span>
        <div class="chips">
          <button
            v-for="e in availableExts"
            :key="'o' + e"
            class="chip"
            :class="{ 'is-on': filters.extExclude.includes(e), 'is-on--out': filters.extExclude.includes(e) }"
            type="button"
            @click="toggleExt('extExclude', e)"
          >
            {{ e }}
          </button>
        </div>
      </div>
      <div class="rules-hint">
        候选来自「设置 → 处理的扩展名」，只会处理系统已启用的格式。
        两组都不选 = 不限制；同一个类型两边都选时以「排除」为准。
      </div>
    </div>

    <!-- ② 名字规则 -->
    <div class="rules-block">
      <div class="rules-title">文件名与文件夹名</div>
      <div class="rules-row">
        <span class="rules-tag rules-tag--in">仅限</span>
        <div class="rules-list">
          <div v-for="(r, i) in filters.nameInclude" :key="'i' + i" class="rule-group">
            <div class="rule">
              <select class="select" :value="r.mode" @change="onMode('nameInclude', i, $event)">
                <option value="contains">包含</option>
                <option value="regex">正则</option>
              </select>
              <input
                class="input"
                :class="{ 'is-bad': !!regexError(r) }"
                :value="r.value"
                :placeholder="PLACEHOLDER[r.mode]"
                @input="onValue('nameInclude', i, $event)"
              />
              <button class="btn btn--sm" type="button" @click="removeRule('nameInclude', i)">删除</button>
            </div>
            <div v-if="regexError(r)" class="rule-bad">{{ regexError(r) }}</div>
          </div>
          <button class="btn btn--sm" type="button" @click="addRule('nameInclude')">+ 添加规则</button>
        </div>
      </div>

      <div class="rules-row">
        <span class="rules-tag rules-tag--out">排除</span>
        <div class="rules-list">
          <div v-for="(r, i) in filters.nameExclude" :key="'o' + i" class="rule-group">
            <div class="rule">
              <select class="select" :value="r.mode" @change="onMode('nameExclude', i, $event)">
                <option value="contains">包含</option>
                <option value="regex">正则</option>
              </select>
              <input
                class="input"
                :class="{ 'is-bad': !!regexError(r) }"
                :value="r.value"
                :placeholder="PLACEHOLDER[r.mode]"
                @input="onValue('nameExclude', i, $event)"
              />
              <button class="btn btn--sm" type="button" @click="removeRule('nameExclude', i)">删除</button>
            </div>
            <div v-if="regexError(r)" class="rule-bad">{{ regexError(r) }}</div>
          </div>
          <button class="btn btn--sm" type="button" @click="addRule('nameExclude')">+ 添加规则</button>
        </div>
      </div>

      <div class="rules-hint">
        比对的是文件 / 文件夹的<strong>完整名字（含扩展名）</strong>，
        以及该文件到监控目录之间各级文件夹的名字。
        「包含」不区分大小写；「正则」按你写的原样生效（要忽略大小写可写 <code>(?i)</code>）。
        例：<code>包含 相机</code> 能命中 <code>相机导入/2026/a.mp4</code> 和
        <code>SONY-相机.mp4</code>；<code>正则 ^DJI_\d{4}\.mp4$</code> 只命中
        <code>DJI_0002.mp4</code>（<strong>别忘了把扩展名算进去</strong>）。
      </div>
    </div>
  </div>
</template>

<style scoped>
.rules {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}
.rules-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}
.rules-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
}
.rules-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  min-width: 0;
}
.rules-tag {
  flex-shrink: 0;
  /* 与 chip 首行文字基线对齐 */
  margin-top: 5px;
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-xs);
  white-space: nowrap;
}
.rules-tag--in {
  background: var(--color-primary-soft);
  color: var(--color-primary-dark);
}
.rules-tag--out {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}
.rules-hint {
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  line-height: 1.9;
}
.rules-hint code {
  font-family: var(--font-mono);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0 4px;
}
.rules-empty {
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
}
.chips {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.chip {
  padding: 3px 10px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface);
  color: var(--color-text-soft);
  font-size: var(--font-size-xs);
  cursor: pointer;
}
.chip:hover {
  border-color: var(--color-border-strong);
}
.chip.is-on {
  background: var(--color-primary);
  border-color: var(--color-primary);
  color: #fff;
}
.chip.is-on--out {
  background: var(--color-danger);
  border-color: var(--color-danger);
}
.rules-list {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  align-items: flex-start;
}
.rule-group {
  width: 100%;
  min-width: 0;
}
.rule {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.rule .select {
  flex-shrink: 0;
  width: 86px;
}
/* 规则值可能很长（一条正则能写几十字符），只许省略、不许把行顶宽 */
.rule .input {
  flex: 1;
  min-width: 0;
  font-family: var(--font-mono);
}
.rule .btn {
  flex-shrink: 0;
}
.input.is-bad {
  border-color: var(--color-danger);
}
.rule-bad {
  margin-top: 2px;
  font-size: var(--font-size-xs);
  color: var(--color-danger);
  overflow-wrap: anywhere;
}
</style>

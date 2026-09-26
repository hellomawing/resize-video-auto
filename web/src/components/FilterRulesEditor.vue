<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { previewWatchFilterList } from '../api/watchpoints'
import type {
  FilterMode,
  FilterRule,
  WatchFilterListPreview,
  WatchFilters,
} from '../api/types'

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
  /** 当前选的监控目录。同时用于命中预览里「列出目录下已有文件」 */
  basePath?: string
  /** 监控目录的递归开关 —— 命中预览按它决定列本级还是连子目录一起列 */
  recursive?: boolean
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

// ---------------------------------------------------------------- 命中预览
//
// 规则是纯文本匹配，光看写法很难确定它到底会挡住什么 —— 尤其是两条规则
// 叠在一起、或者拿正则去写「含 A 且不含 B」的时候（跨不了路径段，见下面的
// 说明）。与其让用户配完保存、再去扫描结果的「跳过明细」里反查，不如直接在
// 边上列出**当前监控目录下已经存在的候选视频**，逐个标出会被处理还是一套
// 规则挡下：一眼就能看到规则把哪些留了下来、把哪些踢了出去。
//
// 判定一律问后端（services.filters.explain，和真实扫描同一份逻辑），前端
// **不自己算**：预览说会被处理、实际扫描却跳过，那比没有预览更糟。
//
// 没选监控目录（新增时 path 还没定）就没有可列的文件，规则区照常编辑、
// 命中预览先显示「选好目录后这里会列出已有文件」的占位。
const previewing = ref(false)
const previewResult = ref<WatchFilterListPreview | null>(null)
const previewError = ref('')
let previewTimer: number | undefined
// 只认最新一次请求的结果。网络有快有慢，先发的可能后回 —— 不挡的话，
// 界面上就会停着一个「上一次」的列表，那比空着更误导人
let previewSeq = 0

async function runPreview(): Promise<void> {
  const seq = ++previewSeq
  previewing.value = true
  try {
    const result = await previewWatchFilterList({
      path: props.basePath?.trim() || '',
      recursive: props.recursive ?? true,
      filters: props.modelValue,
    })
    if (seq !== previewSeq) return
    previewResult.value = result
    previewError.value = ''
  } catch (e) {
    if (seq !== previewSeq) return
    previewResult.value = null
    previewError.value = e instanceof Error ? e.message : '预览失败'
  } finally {
    if (seq === previewSeq) previewing.value = false
  }
}

/**
 * 选了目录或规则一变就重算，防抖 400ms。
 *
 * 罗列目录文件是走磁盘的，不值得每改一个字符就重跑一遍；但**不跳过**正则
 * 写错的情况 —— 前端用的是浏览器正则、后端是 Python 正则，两者语法并不完全
 * 一样（比如 `(?P<name>…)` 后端认、浏览器不认）。所以「正则到底行不行」
 * 这件事交给后端下结论，本地那行红字只当提前提示。
 */
function schedulePreview(): void {
  window.clearTimeout(previewTimer)
  const dir = props.basePath?.trim() || ''
  if (!dir) {
    previewSeq += 1          // 作废在途请求，别让它回来把空态填上
    previewResult.value = null
    previewError.value = ''
    previewing.value = false
    return
  }
  previewResult.value = null
  previewing.value = true
  previewTimer = window.setTimeout(runPreview, 400)
}

watch([() => props.basePath, () => props.recursive, () => props.modelValue],
  schedulePreview, { deep: true })
onUnmounted(() => window.clearTimeout(previewTimer))

const hitFiles = computed(() =>
  previewResult.value?.files.filter((f) => !f.skipped) ?? [])

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
      <div class="rules-row rules-row--field">
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
          <div class="rules-add">
            <button class="btn btn--sm" type="button" @click="addRule('nameInclude')">+ 添加规则</button>
          </div>
        </div>
      </div>

      <div class="rules-row rules-row--field">
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
          <div class="rules-add">
            <button class="btn btn--sm" type="button" @click="addRule('nameExclude')">+ 添加规则</button>
          </div>
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

    <!-- ③ 命中预览 -->
    <div class="rules-block">
      <div class="rules-title">命中预览</div>
      <template v-if="basePath">
        <div v-if="previewing" class="pv">
          <div class="pv-line faint">正在列出 <span class="pv-dir">{{ basePath }}</span> 下的候选视频…</div>
        </div>
        <template v-else-if="previewError || (previewResult && !previewResult.ok)">
          <div class="pv">
            <div class="pv-line pv-line--bad">{{ previewError || previewResult?.message }}</div>
          </div>
        </template>
        <template v-else-if="previewResult">
          <div class="pv-cols">
            <!-- 左：原始视频文件列表 -->
            <div class="pv-col">
              <div class="pv-col-title">
                原始视频文件
                <span class="pv-count" :class="{ 'pv-count--muted': !previewResult.total }">
                  {{ previewResult.total }}
                </span>
              </div>
              <ul v-if="previewResult.files.length" class="pv-list">
                <li
                  v-for="f in previewResult.files"
                  :key="'a' + f.path"
                  class="pv-item"
                  :class="f.skipped ? 'pv-item--out' : 'pv-item--in'"
                >
                  {{ f.path }}
                </li>
              </ul>
              <div v-else class="pv-empty-line">目录下没有候选视频</div>
            </div>

            <!-- 右：命中的视频文件列表 -->
            <div class="pv-col">
              <div class="pv-col-title">
                命中视频文件
                <span class="pv-count pv-count--hit">{{ hitFiles.length }}</span>
              </div>
              <ul v-if="hitFiles.length" class="pv-list">
                <li
                  v-for="f in hitFiles"
                  :key="'h' + f.path"
                  class="pv-item pv-item--in"
                >
                  {{ f.path }}
                </li>
              </ul>
              <div v-else class="pv-empty-line">没有命中的文件</div>
            </div>
          </div>
        </template>
        <div class="rules-hint">
          对照的是 <strong>{{ basePath }}</strong> 下{{ recursive ? '（含子目录）' : '' }}现有候选视频，
          与真实扫描用的是同一套判断；只读，不会改动任何文件。
        </div>
      </template>
      <div v-else class="pv pv-empty">
        选好监控目录后，这里会列出该目录下已有的候选视频，并标出会被当前规则处理哪些、挡下哪些。
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
  /* 行首「仅限 / 排除」标签的高度基准 = **本行第一个控件的真实高度**。
     两边并不一样（文件类型是 chip 22px，名字规则是输入控件 36px），
     所以不能拿一个固定值去凑 —— 那样必然有一处对不齐。 */
  --lead-h: 22px;
}
/* 名字规则行的首行是 select / input（36px），标签跟着它走 */
.rules-row--field {
  --lead-h: 36px;
}
.rules-tag {
  flex-shrink: 0;
  /* 高度与首行控件一致 + 内部垂直居中，天然比 margin-top 硬凑更稳 */
  display: inline-flex;
  align-items: center;
  height: var(--lead-h);
  padding: 0 var(--space-2);
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
.rules-add {
  /* 一条规则都没有时，本行唯一的控件是「+ 添加规则」按钮（28px）。
     垫成与规则行等高的槽，行首标签才不会比它高出一截。 */
  height: var(--lead-h);
  display: flex;
  align-items: center;
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
/* 命中预览：列出当前监控目录下已有文件的结论 */
.pv {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
}
.pv-empty {
  color: var(--color-text-faint);
  font-size: var(--font-size-xs);
}
.pv-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  min-width: 0;
  font-size: var(--font-size-sm);
}
.pv-dir {
  font-family: var(--font-mono);
  overflow-wrap: anywhere;
}
.pv-line--bad {
  font-size: var(--font-size-xs);
  color: var(--color-danger);
  overflow-wrap: anywhere;
}
/* 左右分栏：左=原始视频列表，右=命中视频列表 */
.pv-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  min-width: 0;
}
@media (max-width: 720px) {
  .pv-cols {
    grid-template-columns: 1fr;
  }
}
.pv-col {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
}
.pv-col-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--color-text-soft);
}
.pv-count {
  font-weight: 600;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--color-surface);
}
.pv-count--hit {
  background: var(--color-primary-soft);
  color: var(--color-primary-dark);
}
.pv-count--muted {
  color: var(--color-text-faint);
}
.pv-empty-line {
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  padding: var(--space-2);
}
.pv-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 280px;
  overflow-y: auto;
}
.pv-item {
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  padding: 3px 6px;
  border-radius: var(--radius-sm);
  overflow-wrap: anywhere;
}
.pv-item--in {
  background: var(--color-primary-soft);
  color: var(--color-primary-dark);
}
.pv-item--out {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}
</style>

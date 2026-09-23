<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import Toggle from '../components/Toggle.vue'
import Modal from '../components/Modal.vue'
import TagInput from '../components/TagInput.vue'
import MarkSourcePicker from '../components/MarkSourcePicker.vue'
import {
  CLIENT_ID,
  getSettings,
  patchSettings,
  exportConfig,
  importConfig,
} from '../api/settings'
import { getEnv } from '../api/system'
import { useToast } from '../composables/useToast'
import { useWebSocket } from '../composables/useWebSocket'
import type {
  ConfigBundle,
  EnvInfo,
  MarkValue,
  OutdirMode,
  Settings,
  SettingsPatch,
  WsMessage,
} from '../api/types'

const toast = useToast()
const ws = useWebSocket()

const loading = ref(true)
const saving = ref(false)
// 初始为空对象（类型断言），加载完成前由 loading 遮罩拦截访问，避免模板里到处判空
const form = ref<Settings>({} as Settings)
// markSource 正在等确认的那个新值；用户点「我再想想」时它就是废弃的
const pendingMarkSource = ref<MarkValue | null>(null)
// markSource 从别的值切到 delete 时必须二次确认（不可逆）
const confirmDeleteSource = ref(false)
// 开启「覆盖同名切片」同样是破坏性操作（被覆盖的内容找不回来）：
// 关掉不需要问，开启前必须问一次
const confirmOverwrite = ref(false)
// 改监听地址/端口会让服务按新值监听，提交前问一次
const confirmServerChange = ref(false)
// 监听参数进入页面时的值，用来判断这次提交有没有动过它；
// 用户点过确认后置 ack，免得后面的检查再弹一遍
const serverAck = ref(false)
// 容器运行环境（数据目录、uid/gid）：只读展示，取不到就不显示这一块，
// 它是「怎么部署的」的参考信息，不该影响设置页本身可用
const env = ref<EnvInfo | null>(null)

// ---------------------------------------------------------------- 分栏

type TabKey = 'split' | 'watch' | 'server' | 'backup'

const TABS: { key: TabKey; label: string }[] = [
  { key: 'split', label: '切分参数' },
  { key: 'watch', label: '监控参数' },
  { key: 'server', label: '服务参数' },
  { key: 'backup', label: '配置备份与恢复' },
]
const TAB_LABEL: Record<TabKey, string> = {
  split: '切分参数',
  watch: '监控参数',
  server: '服务参数',
  backup: '配置备份与恢复',
}

const activeTab = ref<TabKey>('split')

/**
 * 需要「输完再存」的字段。它们都是自由文本或数字输入框 —— 输入过程中会经过
 * 空串、"3."、被清空的数字这些中间态，每敲一下就往服务端送的话，后端的规范化
 * 逻辑会把中间态纠正掉、再回填进输入框，等于一边打字一边被改。所以这些字段
 * 攒在该分栏底部，由按钮提交。
 *
 * 其余字段（开关、下拉、标签）都是离散值，没有中间态，改完即存。
 */
const MANUAL_FIELDS: Record<TabKey, string[]> = {
  split: ['size', 'seconds', 'outdir', 'sourceDir'],
  watch: ['pollInterval', 'settleSeconds', 'minSize'],
  // host / port 只在非容器环境可编辑（容器里被环境变量锁死、输入框是禁用态），
  // 放进来不会有副作用；本地开发时改它们才走得通
  server: ['host', 'port', 'jobLogLines'],
  backup: [],
}

const FIELD_LABEL: Record<string, string> = {
  size: '单段大小',
  seconds: '单段时长',
  outdir: '输出目录路径',
  sourceDir: '归档目录名',
  pollInterval: '轮询间隔',
  settleSeconds: '文件稳定检测',
  minSize: '最小文件大小',
  host: '监听地址',
  port: '监听端口',
  jobLogLines: '任务日志保留行数',
}

// ---------------------------------------------------------------- 未保存状态

// 已落盘的手动字段快照（序列化后的字符串，逐分栏一份）。
// 当前值和它不一致，就说明这一栏有没提交的输入。
const baseline = ref<Record<TabKey, string>>({ split: '', watch: '', server: '', backup: '' })

function manualBag(tab: TabKey): Record<string, unknown> {
  return ((form.value as unknown as Record<string, Record<string, unknown>>)[tab] ?? {})
}

function manualSnapshot(tab: TabKey): string {
  const bag = manualBag(tab)
  const picked: Record<string, unknown> = {}
  for (const key of MANUAL_FIELDS[tab]) picked[key] = bag[key]
  return JSON.stringify(picked)
}

/** 保存成功后以服务端返回的值为新基准；调用时机必须在 form 已被回填之后 */
function resetBaseline(): void {
  const next = {} as Record<TabKey, string>
  for (const t of TABS) next[t.key] = manualSnapshot(t.key)
  baseline.value = next
}

const dirtyTabs = computed<TabKey[]>(() =>
  loading.value
    ? []
    : TABS.map((t) => t.key).filter((k) => manualSnapshot(k) !== baseline.value[k]),
)
const hasDirty = computed(() => dirtyTabs.value.length > 0)

const tabDirty = (tab: TabKey): boolean => dirtyTabs.value.includes(tab)

function dirtyCount(tab: TabKey): number {
  const bag = manualBag(tab)
  const before = JSON.parse(baseline.value[tab] || '{}') as Record<string, unknown>
  return MANUAL_FIELDS[tab].filter(
    (k) => JSON.stringify(bag[k]) !== JSON.stringify(before[k]),
  ).length
}

function tabStateText(tab: TabKey): string {
  if (savingTab.value === tab) return '保存中…'
  if (tabDirty(tab)) return `${dirtyCount(tab)} 项输入还没保存`
  return '本页输入已保存'
}

// ---------------------------------------------------------------- 保存

type SaveState = 'idle' | 'saving' | 'ok' | 'error'

const saveState = ref<SaveState>('idle')
// 正在保存的是哪一栏：分栏底部的按钮要据此显示转圈
const savingTab = ref<TabKey | null>(null)
const savedAt = ref('')

const saveStateText = computed(() => {
  if (saveState.value === 'saving') return '保存中…'
  if (saveState.value === 'error') return '保存失败'
  if (saveState.value === 'ok') return `已保存 ${savedAt.value}`
  return ''
})

const clock = (): string => new Date().toLocaleTimeString('zh-CN', { hour12: false })

/**
 * 把服务端返回的值写回 form —— 但**只回填本次提交过的字段**。
 *
 * 不能整体覆盖：服务端返回的是全量设置，而用户很可能正在别的分栏里打字，
 * 整体覆盖会把那些还没提交的输入一起冲掉。
 */
function applySaved(saved: Settings, partial: SettingsPatch): void {
  const target = form.value as unknown as Record<string, Record<string, unknown>>
  const incoming = saved as unknown as Record<string, Record<string, unknown>>
  for (const [group, fields] of Object.entries(partial)) {
    const bag = target[group]
    const fresh = incoming[group]
    if (!bag || !fresh) continue
    for (const key of Object.keys(fields as Record<string, unknown>)) bag[key] = fresh[key]
  }
  // bySize 会派生 all（服务端算的）。前端不显示也不提交它，但要跟着同步，
  // 免得 form 里留一个与服务端不一致的派生值
  if (partial.split?.bySize !== undefined) form.value.split.all = saved.split.all
}

async function autosave(partial: SettingsPatch, label: string, tab?: TabKey): Promise<boolean> {
  saveState.value = 'saving'
  savingTab.value = tab ?? activeTab.value
  try {
    const saved = await patchSettings(partial)
    applySaved(saved, partial)
    saveState.value = 'ok'
    savedAt.value = clock()
    return true
  } catch (e) {
    saveState.value = 'error'
    toast.error(`${label}没保存成功：${e instanceof Error ? e.message : '未知错误'}`)
    return false
  } finally {
    savingTab.value = null
  }
}

/** 离散字段（开关 / 下拉 / 标签）：改完就把这一个字段落盘，不等点按钮 */
function autosaveField(
  group: 'split' | 'watch' | 'server',
  key: string,
  value: unknown,
  label: string,
): void {
  // 先改本地值，开关和下拉要立刻有反馈，不等请求回来
  ;(form.value[group] as unknown as Record<string, unknown>)[key] = value
  void autosave({ [group]: { [key]: value } } as SettingsPatch, label)
}

/** 监听地址/端口这一次有没有被动过（进页面 / 上次保存时的值 vs 当前值） */
function serverTouched(): boolean {
  if (!baseline.value.server) return false
  const before = JSON.parse(baseline.value.server) as Record<string, unknown>
  const now = manualBag('server')
  return before.host !== now.host || before.port !== now.port
}

/** 提交某一栏里所有「输完再存」的字段 */
async function commitTab(tab: TabKey, skipGuards = false): Promise<void> {
  if (!MANUAL_FIELDS[tab].length || !tabDirty(tab)) return
  // 改监听参数会让服务按新值监听，本机环境又没人能拦住，先问一次
  if (tab === 'server' && !skipGuards && serverTouched() && !serverAck.value) {
    confirmServerChange.value = true
    return
  }

  const bag = manualBag(tab)
  const beforeVals: Record<string, unknown> = {}
  const partial: Record<string, unknown> = {}
  for (const key of MANUAL_FIELDS[tab]) {
    beforeVals[key] = bag[key]
    partial[key] = bag[key]
  }

  const ok = await autosave({ [tab]: partial } as SettingsPatch, TAB_LABEL[tab], tab)
  if (!ok) return

  // 后端会静默纠正非法值（认不出的大小、超范围的数字）。纠正了就明说一句，
  // 否则用户会以为自己填的东西不声不响地没了
  const fixed = MANUAL_FIELDS[tab].filter(
    (k) => JSON.stringify(bag[k]) !== JSON.stringify(beforeVals[k]),
  )
  if (fixed.length) {
    const names = fixed.map((k) => FIELD_LABEL[k] ?? k).join('、')
    toast.info(`这些输入不符合格式，已按规则纠正：${names}`)
  }
  resetBaseline()
}

function switchTab(next: TabKey): void {
  if (next === activeTab.value) return
  const from = activeTab.value
  activeTab.value = next
  // 离开一栏就等于「这一页的输入到此为止」，语义与输入框失焦一致：
  // 顺手把没提交的输入补上，用户不必记着「我还有个没保存的」
  void commitTab(from)
}

function onServerChangeOk(): void {
  serverAck.value = true
  confirmServerChange.value = false
  void commitTab('server')
}

/**
 * 覆盖开关不能直接 v-model：开启是不可逆的（同名文件直接被盖掉），
 * 必须先弹确认；关闭是往安全方向走，直接落盘。
 */
function onOverwriteChange(value: boolean): void {
  if (value) {
    confirmOverwrite.value = true
    return
  }
  autosaveField('split', 'overwrite', false, '覆盖同名切片')
}

function onOverwriteOk(): void {
  confirmOverwrite.value = false
  autosaveField('split', 'overwrite', true, '覆盖同名切片')
}

/**
 * 源文件处理方式。只有「这一次真的从别的值切到删除」才该守门 ——
 * 老写法只看当前值是不是 delete，于是设过一次之后就每次保存都弹一次，
 * 哪怕改的是完全不相干的东西。
 */
function onMarkSourceChange(value: MarkValue): void {
  if (value === 'delete' && form.value.split.markSource !== 'delete') {
    pendingMarkSource.value = value
    confirmDeleteSource.value = true
    return
  }
  autosaveField('split', 'markSource', value, '源文件处理方式')
}

function onDeleteSourceOk(): void {
  confirmDeleteSource.value = false
  const value = pendingMarkSource.value
  pendingMarkSource.value = null
  if (value) autosaveField('split', 'markSource', value, '源文件处理方式')
}

// ---------------------------------------------------------------- 加载

const remoteChanged = ref(false)

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await getSettings()
    form.value = data
    resetBaseline()
    serverAck.value = false
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载设置失败')
  } finally {
    loading.value = false
  }
}

// 另一处改了设置。有没提交的输入时不能静默覆盖 —— 挂个提示让用户自己决定
function onWsMessage(msg: WsMessage): void {
  if (msg.type !== 'settings.updated') return
  // 自己刚保存触发的那条：本地已经是服务端返回的权威值了。这时若再全量拉一次，
  // 会把本页其它分栏里还没提交的输入冲掉
  if (msg.source === CLIENT_ID) return
  if (hasDirty.value) {
    remoteChanged.value = true
    return
  }
  void load()
}

async function reloadFromRemote(): Promise<void> {
  remoteChanged.value = false
  await load()
}

let unsub: (() => void) | null = null

function onBeforeUnload(e: BeforeUnloadEvent): void {
  if (!hasDirty.value) return
  e.preventDefault()
  e.returnValue = ''
}

onMounted(() => {
  void load()
  void getEnv()
    .then((e) => { env.value = e })
    .catch(() => { env.value = null })
  unsub = ws.on(onWsMessage)
  window.addEventListener('beforeunload', onBeforeUnload)
})

onUnmounted(() => {
  unsub?.()
  window.removeEventListener('beforeunload', onBeforeUnload)
})

// 离开页面前不能直接自动提交：「改监听参数」那类确认门需要人回答，
// 页面已经走了就来不及问。所以交给用户选一次。
let leaveNext: (() => void) | null = null
const confirmLeave = ref(false)

onBeforeRouteLeave((_to, _from, next) => {
  if (!hasDirty.value) {
    next()
    return
  }
  // 挡住这次导航，等用户在确认框里选完再决定放不放行
  leaveNext = () => next()
  confirmLeave.value = true
})

async function leaveNow(save: boolean): Promise<void> {
  confirmLeave.value = false
  if (save) {
    // 用户已经明确选了「保存」，路上不再为监听参数打断他
    for (const tab of [...dirtyTabs.value]) await commitTab(tab, true)
  }
  const next = leaveNext
  leaveNext = null
  next?.()
}

// ---------------------------------------------------------------- 备份恢复

// 配置导入导出：导出直接下载 JSON；导入先读文件、弹确认框，确认后才提交
const exporting = ref(false)
const importing = ref(false)
const confirmImport = ref(false)
const pendingBundle = ref<ConfigBundle | null>(null)
// 原生 file input 的外观没法跟这套按钮统一，所以藏起来、由「导入配置」按钮触发；
// 文件名要在确认框里回显 —— 藏了 input 之后就只剩这一个地方能告诉用户选的是哪个文件
const fileRef = ref<HTMLInputElement | null>(null)
const pendingName = ref('')

// 取消导入（或导入完成后关闭）就把待导入内容和文件名一起丢掉，
// 免得下次打开确认框还挂着上一次选的文件
watch(confirmImport, (open) => {
  if (!open) {
    pendingBundle.value = null
    pendingName.value = ''
  }
})

// bySize 是个布尔字段：true = 按大小切，false = 按时长切。这里用下拉框而不是开关——
// 开关关上时的含义（「按时长」）正好是标签的反义，很容易看反。
const basis = computed<'size' | 'seconds'>(() =>
  form.value.split?.bySize ? 'size' : 'seconds',
)

const OUTDIR_OPTIONS: { value: OutdirMode; label: string; hint: string }[] = [
  {
    value: 'same',
    label: '与源文件同目录',
    hint: '切片就放在原片所在的文件夹里；原片本身按上面「源文件处理方式」处置。',
  },
  {
    value: 'custom',
    label: '统一放到指定目录',
    hint: '下面填绝对路径（如 /vol1/split-out），目录不存在会自动创建。留空等同于「与源文件同目录」。',
  },
]
const outdirHint = computed(
  () => OUTDIR_OPTIONS.find((o) => o.value === form.value.split?.outdirMode)?.hint ?? '',
)

/**
 * 监听地址/端口在容器里被环境变量锁死了：启动逻辑是「环境变量优先于设置」，
 * 而 Dockerfile 已经写死了 VS_HOST / VS_PORT，所以这两个框改了没有任何作用。
 * 取到值就置为只读并说明该去哪儿改 —— 别让人对着一个没反应的输入框反复试。
 */
const hostLocked = computed(() => !!env.value?.hostEnv)
const portLocked = computed(() => !!env.value?.portEnv)

function onSelectChange(
  e: Event,
  group: 'split' | 'watch' | 'server',
  key: string,
  label: string,
): void {
  autosaveField(group, key, (e.target as HTMLSelectElement).value, label)
}

/** 「每段按什么切」的下拉存的是 size / seconds，而字段本身是布尔 bySize */
function onBasisChange(e: Event): void {
  const bySize = (e.target as HTMLSelectElement).value === 'size'
  autosaveField('split', 'bySize', bySize, '每段按什么切')
}

function fileStamp(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`
}

async function doExport(): Promise<void> {
  exporting.value = true
  try {
    const data = await exportConfig()
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
    )
    const a = document.createElement('a')
    a.href = url
    a.download = `video-splitter-config-${fileStamp()}.json`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('配置已导出（含设置、监控目录、归档目录记录）')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '导出失败')
  } finally {
    exporting.value = false
  }
}

/** 「导入配置」按钮：转去点那个藏起来的 file input，真正的处理在 onPickFile 里 */
function pickFile(): void {
  fileRef.value?.click()
}

function onPickFile(e: Event): void {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const fileName = file.name
  const reader = new FileReader()
  reader.onload = () => {
    // 先清空：否则再选同一个文件不会再触发 change
    input.value = ''
    try {
      const parsed = JSON.parse(String(reader.result)) as ConfigBundle
      if (!parsed || typeof parsed !== 'object' || !parsed.settings) {
        toast.error('这不像本工具导出的配置文件（缺少 settings 字段）')
        return
      }
      pendingBundle.value = parsed
      pendingName.value = fileName
      confirmImport.value = true
    } catch {
      toast.error('文件不是有效的 JSON，无法导入')
    }
  }
  reader.readAsText(file)
}

async function doImport(): Promise<void> {
  if (!pendingBundle.value) return
  importing.value = true
  try {
    const result = await importConfig(pendingBundle.value)
    toast.success(result.message || '导入完成')
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '导入失败')
  } finally {
    importing.value = false
    // 关掉确认框；待导入内容与文件名由上面的 watch 一并清掉
    confirmImport.value = false
  }
}
</script>

<template>
  <!-- page--form：内容列限宽 + 标签移到左侧两栏，说明文字不再铺满整个屏幕宽 -->
  <div class="page page--form">
    <div class="page-header">
      <div>
        <h1 class="page-title">设置</h1>
        <div class="page-subtitle">切分参数、监控参数与服务的全局配置</div>
      </div>
      <!-- 开关、下拉这类改完即存的字段，存完在这里报一声，不用弹提示条打扰 -->
      <div v-if="saveStateText" class="save-state" :class="`save-state--${saveState}`">
        <span v-if="saveState === 'saving'" class="spinner" />
        {{ saveStateText }}
      </div>
    </div>

    <div v-if="remoteChanged" class="remote-bar">
      <span>另一个页面刚改过设置。你这里有还没提交的输入，直接重新加载会把它们丢掉。</span>
      <button class="btn btn--sm" @click="reloadFromRemote">放弃我的输入并重新加载</button>
    </div>

    <div class="tabs" role="tablist">
      <button
        v-for="t in TABS"
        :key="t.key"
        class="tab"
        :class="{ 'tab--active': activeTab === t.key }"
        role="tab"
        :aria-selected="activeTab === t.key"
        @click="switchTab(t.key)"
      >
        {{ t.label }}
        <span v-if="tabDirty(t.key)" class="tab-dot" title="有输入还没保存" />
      </button>
    </div>

    <div v-if="loading" class="card"><span class="spinner" /> 加载中…</div>

    <template v-else>
      <!-- ============================ 切分参数 ============================ -->
      <section v-show="activeTab === 'split'" class="tab-panel" role="tabpanel">
        <div class="card">
          <h2 class="card-title">切分参数</h2>
          <div class="field-hint card-intro">
            决定「切成什么样、切哪些、切出来的放哪」。
            <span class="hl">开关和下拉框改完立即生效</span>；输入框填好后点本页底部的保存。
          </div>
          <div class="field-hint">
            切割方式固定为 <strong>ffmpeg 无损流拷贝</strong>：只换容器、不重新编码，
            <span class="hl">画质音质零损失</span>，每段都能独立播放。
            切不动的文件不会被硬切，而是原样保留原片、记进「任务队列 → 处理失败的文件」里等你处理。
          </div>

          <div class="field">
            <label class="field-label">每段按什么切</label>
            <select :value="basis" class="select" @change="onBasisChange">
              <option value="size">按大小 —— 每段不超过设定体积</option>
              <option value="seconds">按时长 —— 每段大约设定秒数</option>
            </select>
            <div class="field-hint">
              它同时决定下面填哪一格：<strong>按大小</strong>填「单段大小」、<strong>按时长</strong>填「单段时长」。
              也决定扫描门槛 —— 按大小时只切超过「单段大小」的视频，按时长时所有视频都入队
              （仍受「监控参数 → 最小文件大小」限制）。
            </div>
          </div>

          <div v-if="basis === 'size'" class="field">
            <label class="field-label">
              <span>单段大小<span class="need-save">需保存</span></span>
            </label>
            <input v-model="form.split.size" class="input" placeholder="如 3.9G" />
            <div class="field-hint">
              身兼两职：① <strong>比它小的视频直接跳过</strong>、不切；② 切分时以它为<strong>每段的上限</strong>。
              支持 B / K / M / G，如 500M、3.9G，不区分大小写。
            </div>
          </div>
          <div v-else class="field">
            <label class="field-label">
              <span>单段时长（秒）<span class="need-save">需保存</span></span>
            </label>
            <input v-model.number="form.split.seconds" type="number" min="1" class="input" />
            <div class="field-hint">
              每段的目标秒数。切点必须落在关键帧上，所以每段实际时长会围绕这个值浮动，通常略长一点。
            </div>
          </div>

          <div class="field">
            <label class="field-label">处理的扩展名</label>
            <TagInput
              :model-value="form.split.ext"
              placeholder="输入扩展名后回车，如 .mp4"
              @update:model-value="(v: string[]) => autosaveField('split', 'ext', v, '处理的扩展名')"
            />
            <div class="field-hint">
              只处理这些后缀的文件，不区分大小写；漏写前面的点会自动补上。
              本工具自己切出来的片段、已加标记的原片、归档目录里的文件<strong>始终跳过</strong>，与此项无关。
            </div>
            <div class="field-hint">
              可选范围限「能无损切分的格式」：
              <span class="mono">.mp4 .m4v .mov .mkv .webm .ts .m2ts .mts</span>。
              其它后缀（avi / wmv / flv / rmvb 等）只能重新编码（有损）或按字节硬劈（第 2 段起播不了），
              本工具两者都不做 —— 所以它们既不会被扫描，<span class="hl">填进来也会在保存时被剔除</span>。
            </div>
          </div>

          <div class="field">
            <label class="field-label">递归扫描子目录</label>
            <Toggle
              :model-value="form.split.recursive"
              @update:model-value="(v: boolean) => autosaveField('split', 'recursive', v, '递归扫描子目录')"
            />
            <div class="field-hint">
              开启：连各级子文件夹一起找；关闭：只看监控目录这一层，子目录整个忽略。
            </div>
          </div>

          <div class="field">
            <label class="field-label">输出目录</label>
            <select
              :value="form.split.outdirMode"
              class="select"
              @change="onSelectChange($event, 'split', 'outdirMode', '输出目录')"
            >
              <option v-for="o in OUTDIR_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
            <input
              v-if="form.split.outdirMode === 'custom'"
              v-model="form.split.outdir"
              class="input"
              placeholder="自定义输出目录绝对路径"
            />
            <div class="field-hint">{{ outdirHint }}</div>
          </div>

          <div class="field">
            <label class="field-label">源文件处理方式（默认值）</label>
            <MarkSourcePicker
              :model-value="form.split.markSource"
              v-model:source-dir="form.split.sourceDir"
              hide-follow
              @update:model-value="onMarkSourceChange"
            />
            <div class="field-hint">
              这是<strong>全局默认值</strong>，但可以被覆盖：每个监控目录能在「监控目录」页单独设一个，
              手动扫描时还能只改这一次。
              生效顺序是 <span class="hl">这一次手动指定 &gt; 该目录的设置 &gt; 这里</span>，
              并以<strong>任务入队那一刻</strong>为准 —— 已排队的任务不受之后改动影响。
            </div>
          </div>

          <div class="field">
            <label class="field-label">保留元数据</label>
            <Toggle
              :model-value="form.split.keepMetadata"
              @update:model-value="(v: boolean) => autosaveField('split', 'keepMetadata', v, '保留元数据')"
            />
            <div class="field-hint">
              把源文件的容器信息一并写进切片：<strong>拍摄时间、相机 / 机型、定位</strong>这类标签都靠它留住。
              关闭后切片只剩基本属性。少数标签 ffmpeg 会强制改写，开着也留不住。
            </div>
          </div>

          <div class="field">
            <label class="field-label">覆盖已存在的同名切片</label>
            <Toggle :model-value="form.split.overwrite" @update:model-value="onOverwriteChange" />
            <div class="field-hint" :class="{ 'field-hint--warn': form.split.overwrite }">
              关闭（推荐）：一旦发现同名切片就<strong>中止这次切分</strong>，已有文件一个都不动，留你确认。
              开启：直接覆盖同名文件，<span class="hl hl--danger">被覆盖的内容找不回来</span>。
              同名切片多半是重复扫描造成的，但如果那个名字下正好是你自己放的文件，就一起没了。
            </div>
          </div>

          <div class="savebar">
            <span class="savebar-state" :class="{ 'savebar-state--dirty': tabDirty('split') }">
              {{ tabStateText('split') }}
            </span>
            <button
              class="btn btn--primary"
              :disabled="!tabDirty('split') || savingTab === 'split'"
              @click="commitTab('split')"
            >
              <span v-if="savingTab === 'split'" class="spinner" /> 保存设置
            </button>
          </div>
        </div>
      </section>

      <!-- ============================ 监控参数 ============================ -->
      <section v-show="activeTab === 'watch'" class="tab-panel" role="tabpanel">
        <div class="card">
          <h2 class="card-title">监控参数</h2>
          <div class="field-hint card-intro">
            决定「多久发现一次新文件」与「什么样的文件才值得切」。
            筛选门槛（稳定检测、最小大小、忽略后缀）<strong>对手动点「扫描」同样生效</strong>。
          </div>

          <div class="field">
            <label class="field-label">实时监听（inotify）</label>
            <Toggle
              :model-value="form.watch.realtime"
              @update:model-value="(v: boolean) => autosaveField('watch', 'realtime', v, '实时监听')"
            />
            <div class="field-hint">
              开着时新文件<strong>几秒内</strong>就被发现。网络共享目录（SMB / NFS）的系统通知不可靠，
              会自动退化为轮询。
              <span class="hl">关掉不等于停止扫描</span> —— 仍按下面的间隔轮询，只是不再即时。
            </div>
          </div>
          <div class="field">
            <label class="field-label">
              <span>轮询间隔（秒）<span class="need-save">需保存</span></span>
            </label>
            <input v-model.number="form.watch.pollInterval" type="number" min="5" max="86400" class="input" />
            <div class="field-hint">
              每隔这么久把「实时监听」模式的监控目录整个扫一遍。这一项<strong>始终生效</strong>，
              是 inotify 的兜底（网络共享上它就是唯一的发现手段）。
              范围 5 ~ 86400；定时扫描 / 仅手动的目录不受它影响。
            </div>
          </div>
          <div class="field">
            <label class="field-label">
              <span>文件稳定检测（秒）<span class="need-save">需保存</span></span>
            </label>
            <input v-model.number="form.watch.settleSeconds" type="number" min="0" max="86400" class="input" />
            <div class="field-hint">
              大小与修改时间连续这么多秒不变才入队，避免切到还没拷完的半成品。
              填 <span class="hl">0 = 不做检测</span>：正在写入的文件会被直接切走，不太建议。
            </div>
          </div>
          <div class="field">
            <label class="field-label">
              <span>最小文件大小（忽略更小的）<span class="need-save">需保存</span></span>
            </label>
            <input v-model="form.watch.minSize" class="input" placeholder="如 0 或 100M" />
            <div class="field-hint">
              比它小的文件不切。<strong>填 0 = 不限制</strong>。支持 B / K / M / G，如 100M。
            </div>
          </div>
          <div class="field">
            <label class="field-label">忽略的后缀</label>
            <TagInput
              :model-value="form.watch.ignoreSuffixes"
              placeholder="输入后缀后回车，如 .tmp"
              @update:model-value="(v: string[]) => autosaveField('watch', 'ignoreSuffixes', v, '忽略的后缀')"
            />
            <div class="field-hint">
              这些后缀的文件<strong>永远不处理</strong>，对所有监控目录一并生效（不必逐个去配过滤规则）。
              默认挡的是下载中的临时文件：
              <span class="mono">.tmp .part .crdownload .!qb .download</span>。
            </div>
          </div>

          <div class="savebar">
            <span class="savebar-state" :class="{ 'savebar-state--dirty': tabDirty('watch') }">
              {{ tabStateText('watch') }}
            </span>
            <button
              class="btn btn--primary"
              :disabled="!tabDirty('watch') || savingTab === 'watch'"
              @click="commitTab('watch')"
            >
              <span v-if="savingTab === 'watch'" class="spinner" /> 保存设置
            </button>
          </div>
        </div>
      </section>

      <!-- ============================ 服务参数 ============================ -->
      <section v-show="activeTab === 'server'" class="tab-panel" role="tabpanel">
        <div class="card">
          <h2 class="card-title">服务参数</h2>
          <div class="field-hint card-intro">
            服务本身的监听与日志。<strong>这一栏改错会让控制台打不开</strong>，不确定就保持默认。
          </div>
          <div class="field">
            <label class="field-label">监听地址</label>
            <!-- 锁定态直接显示环境变量里的真实取值：设置里那份可能是历史值，跟实际对不上 -->
            <input v-if="hostLocked" class="input input--locked" :value="env?.hostEnv ?? ''" disabled />
            <input v-else v-model="form.server.host" class="input" />
            <div v-if="hostLocked" class="field-hint field-hint--warn">
              实际生效值来自部署给的 <span class="mono">VS_HOST</span>，<strong>在这里改了不会生效</strong>。
              要改得动 Docker 的 environment 后重建容器。
            </div>
            <div v-else class="field-hint">
              容器内监听哪块网卡，默认 <span class="mono">0.0.0.0</span>（全都听）。<strong>一般不用改</strong>。
            </div>
          </div>
          <div class="field">
            <label class="field-label">监听端口</label>
            <input
              v-if="portLocked"
              class="input input--locked"
              :value="env?.portEnv ?? ''"
              disabled
            />
            <input
              v-else
              v-model.number="form.server.port"
              type="number"
              min="1"
              max="65535"
              class="input"
            />
            <div v-if="portLocked" class="field-hint field-hint--warn">
              实际生效值来自部署给的 <span class="mono">VS_PORT</span>，<strong>在这里改了不会生效</strong>。
              想换访问端口就改 Docker 端口映射的<strong>宿主端口</strong>那一侧（如
              <span class="mono">18099:{{ env?.portEnv }}</span>），容器内的不用动。
            </div>
            <div v-else class="field-hint">
              容器内监听的端口。外部访问走的是 Docker 端口映射（如 <span class="mono">8099:8099</span>），
              <span class="hl">两边必须一致</span> —— 只改这里而不同步改映射，控制台就打不开了。
            </div>
          </div>
          <div class="field">
            <label class="field-label">
              <span>任务日志保留行数<span class="need-save">需保存</span></span>
            </label>
            <input v-model.number="form.server.jobLogLines" type="number" min="100" max="100000" class="input" />
            <div class="field-hint">
              每个任务的日志最多留这么多行，超出丢掉最早的。范围 100 ~ 100000。
            </div>
          </div>

          <!-- 只读：告诉用户「状态存在哪儿」，备份与迁移时才找得到 -->
          <div v-if="env" class="field">
            <label class="field-label">数据目录（只读）</label>
            <input class="input" :value="env.dataDir" readonly />
            <div class="field-hint">
              设置、监控目录列表、任务历史、失败清单、归档目录记录都在这里。
              它由 Docker 自己管理（卷 <span class="mono">video-splitter-data</span>），
              <strong>不用配置、升级容器也不会丢</strong>；飞牛应用则固定在安装所在存储空间的
              <span class="mono">@appdata/video-splitter</span>。
              备份用下面的「导出配置」；要连任务历史一起搬，才需要打包整个卷。
              当前属主为 <span class="mono">{{ env.uid }}:{{ env.gid }}</span> ——
              切片在文件管理里改不动时，用它对照部署时填的 PUID / PGID。
            </div>
          </div>

          <div class="savebar">
            <span class="savebar-state" :class="{ 'savebar-state--dirty': tabDirty('server') }">
              {{ tabStateText('server') }}
            </span>
            <button
              class="btn btn--primary"
              :disabled="!tabDirty('server') || savingTab === 'server'"
              @click="commitTab('server')"
            >
              <span v-if="savingTab === 'server'" class="spinner" /> 保存设置
            </button>
          </div>
        </div>
      </section>

      <!-- ======================== 配置备份与恢复 ======================== -->
      <section v-show="activeTab === 'backup'" class="tab-panel" role="tabpanel">
        <div class="card">
          <h2 class="card-title">配置备份与恢复</h2>
          <div class="field-hint card-intro">
            导出一份 JSON：含<strong>设置</strong>、<strong>监控目录</strong>、
            <strong>归档目录记录</strong>。换机器或重装后在另一台导入，不用重新配一遍。
            任务历史不在里面 —— 那是运行数据，要一起搬得打包整个数据卷。
          </div>
          <div class="backup-actions">
            <button class="btn" :disabled="exporting" @click="doExport">
              <span v-if="exporting" class="spinner" />
              <svg v-else class="backup-ico" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                <path
                  d="M8 2.5v8M4.8 7.3 8 10.5l3.2-3.2M3 13.2h10"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.6"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              导出配置
            </button>

            <button class="btn" :disabled="importing" @click="pickFile">
              <svg class="backup-ico" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                <path
                  d="M8 10.5v-8M4.8 5.7 8 2.5l3.2 3.2M3 13.2h10"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.6"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              导入配置
            </button>

            <!-- 原生 file input 的外观跟这套按钮凑不到一起，藏起来由上面的按钮点开 -->
            <input
              ref="fileRef"
              class="backup-file"
              type="file"
              accept="application/json,.json"
              @change="onPickFile"
            />
          </div>

          <div class="field-hint backup-note">
            导入会<span class="hl">整体替换当前设置</span>；监控目录按<strong>路径</strong>合并
            （已存在的更新成导入内容，没有的新增）；归档目录记录只增不减。
            路径不在容器已挂载目录内的监控目录会被跳过。<strong>视频文件本身不受影响。</strong>
          </div>
        </div>
      </section>
    </template>

    <!-- 开启「覆盖同名切片」前的二次确认：这是不可逆的，箭头必须停在「关」这一侧 -->
    <Modal v-model="confirmOverwrite" title="确认开启覆盖">
      <p>开启后，切分时遇到<strong>同名文件会直接覆盖</strong>，被覆盖的内容找不回来。</p>
      <p>
        同名切片多半是重复扫描造成的，那倒没关系；但如果那个名字下正好是你自己放进去的
        文件，就一起没了。
      </p>
      <p>
        保持关闭的话，一旦发现同名切片就<strong>中止这次切分</strong>，已有文件一个都不动，
        等你确认后再处理 —— 稳妥得多。
      </p>
      <template #footer>
        <button class="btn" @click="confirmOverwrite = false">保持关闭（推荐）</button>
        <button class="btn btn--danger" @click="onOverwriteOk">仍然开启</button>
      </template>
    </Modal>

    <!-- 改监听参数前的提醒：这一改服务就按新值监听，事后才发现会很麻烦 -->
    <Modal v-model="confirmServerChange" title="监听参数已改动">
      <p>你改了服务的<strong>监听地址或端口</strong>，要重启服务才按新值监听。</p>
      <p>
        保存后<span class="hl">当前这个网址就失效了</span>，得用新地址访问控制台。
        如果跑在 Docker 里，还要把端口映射改成一致的 —— 容器内监听的端口和外部映射对不上，
        控制台就打不开。
      </p>
      <p>确认改对了再保存；改错了得进容器把配置改回来。</p>
      <template #footer>
        <button class="btn" @click="confirmServerChange = false">我再检查一下</button>
        <button class="btn btn--primary" @click="onServerChangeOk">确认，仍要保存</button>
      </template>
    </Modal>

    <!-- 删除源文件二次确认 -->
    <Modal v-model="confirmDeleteSource" title="危险操作确认">
      <p>
        你选择了「删除源文件」。切分完成后，<strong>原始视频将被永久删除且无法恢复</strong>。
        请确认你确实希望如此，并建议已开启输出目录与源文件分离。
      </p>
      <template #footer>
        <button class="btn" @click="confirmDeleteSource = false">我再想想</button>
        <button class="btn btn--danger" @click="onDeleteSourceOk">确认删除源文件</button>
      </template>
    </Modal>

    <!-- 离开页面时还有没提交的输入。这里不直接自动提交：改监听参数那类确认门
         需要人回答，页面走了就来不及问 -->
    <Modal v-model="confirmLeave" title="还有输入没保存">
      <p>这个页面里有<strong>还没提交的输入框内容</strong>，直接离开会丢掉它们。</p>
      <template #footer>
        <button class="btn" @click="leaveNow(false)">不保存，直接离开</button>
        <button class="btn btn--primary" @click="leaveNow(true)">保存后离开</button>
      </template>
    </Modal>

    <!-- 导入配置二次确认：会替换设置，必须先说清楚再动手 -->
    <Modal v-model="confirmImport" title="确认导入配置">
      <p>将用这个文件里的配置覆盖当前设置：</p>
      <p v-if="pendingName"><code class="import-file">{{ pendingName }}</code></p>
      <p><strong>设置</strong>整体替换（切分、监控、服务参数）；<strong>监控目录</strong>按路径合并 ——
        已存在的更新成导入内容，没有的新增；<strong>归档目录记录</strong>只增不减。</p>
      <p>视频文件本身不受影响。</p>
      <template #footer>
        <button class="btn" @click="confirmImport = false">取消</button>
        <button class="btn btn--primary" :disabled="importing" @click="doImport">
          <span v-if="importing" class="spinner" /> 确认导入
        </button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
/* ---------- 分栏 ---------- */
/* 一栏一个功能块。分栏不只是视觉分组，它同时是保存边界：
   开关下拉类改完即存，文本框留在栏底由按钮统一提交。 */
.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  border-bottom: 1px solid var(--color-border);
  margin-bottom: calc(-1 * var(--space-2));
}
.tab {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--color-text-soft);
  font-size: var(--font-size);
  font-family: inherit;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
.tab:hover {
  color: var(--color-text);
}
.tab--active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}
/* 这一栏里有没保存的输入。没有它，用户切走之后就再也想不起来哪栏没存 */
.tab-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-warning);
}
.tab-panel {
  display: block;
}

/* ---------- 保存状态 ---------- */
.save-state {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 28px;
  padding: 0 var(--space-3);
  border-radius: var(--radius);
  background: var(--color-surface-2);
  color: var(--color-text-faint);
  font-size: var(--font-size-sm);
}
.save-state--ok {
  background: var(--color-success-soft);
  color: var(--color-success);
}
.save-state--error {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}

/* ---------- 别处改了设置 ---------- */
.remote-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-warning);
  border-radius: var(--radius);
  background: var(--color-warning-soft);
  color: var(--color-warning);
  font-size: var(--font-size-sm);
}

/* ---------- 栏底保存条 ---------- */
.savebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-5);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}
.savebar-state {
  font-size: var(--font-size-sm);
  color: var(--color-text-faint);
}
.savebar-state--dirty {
  color: var(--color-warning);
}

/* 标出哪些字段是「输完还得点保存」的。没有这个标记，用户会以为
   输入框跟开关一样改完就生效，然后奇怪为什么设置没变 */
.need-save {
  margin-left: var(--space-2);
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--color-muted-soft);
  color: var(--color-text-faint);
  font-size: var(--font-size-xs);
  font-weight: 400;
}

/* ---------- 备份恢复 ---------- */
/* 备份恢复的两个操作并排成一组：各自占一行时左边空一大截，谁跟谁是一对也看不出来。
   高度仍用 .btn 的 36px，跟页面上其它按钮齐平。 */
.backup-actions {
  position: relative; /* 藏起来的 file input 相对这里定位 */
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}
.backup-actions .btn {
  min-width: 132px;
  /* 比纯白描边多一点按钮感，hover 时整块转成主色浅底 */
  background: var(--color-surface-2);
}
.backup-actions .btn:hover:not(:disabled) {
  background: var(--color-primary-soft);
}
/* 图标用 currentColor，跟着按钮文字一起变色 */
.backup-ico {
  flex-shrink: 0;
}
/* 原生 file input 长什么样由浏览器决定，跟这套按钮凑不到一起 ——
   藏起来（仍能被 JS 触发、也仍能被读屏读到），改由「导入配置」按钮点开 */
.backup-file {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  border: 0;
  overflow: hidden;
  white-space: nowrap;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
}
/* 逐条说明摆在按钮组下面，紧跟着它解释的对象 */
.backup-note {
  margin-top: var(--space-3);
}
/* 确认框里的文件名：长名字要能换行，别把对话框撑破 */
.import-file {
  display: inline-block;
  max-width: 100%;
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--color-muted-soft);
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--color-text);
  word-break: break-all;
}
/* 被部署锁死的项（监听地址 / 端口）：灰底表示改不了，但字色不能跟着淡下去 ——
   用户正是要看清「实际生效的是哪个值」 */
.input--locked:disabled {
  background: var(--color-surface-2);
  color: var(--color-text-soft);
  cursor: not-allowed;
}
</style>

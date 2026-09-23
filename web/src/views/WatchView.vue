<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from '../components/DataTable.vue'
import Toggle from '../components/Toggle.vue'
import Modal from '../components/Modal.vue'
import EmptyState from '../components/EmptyState.vue'
import DirPicker from '../components/DirPicker.vue'
import ScanModePicker from '../components/ScanModePicker.vue'
import MarkSourcePicker from '../components/MarkSourcePicker.vue'
import OriginRescueModal from '../components/OriginRescueModal.vue'
import { listWatchpoints, createWatchpoint, updateWatchpoint, deleteWatchpoint, scanWatchpoint } from '../api/watchpoints'
import { getSettings } from '../api/settings'
import { useToast } from '../composables/useToast'
import { useScanRescue } from '../composables/useScanRescue'
import { useScanOverride } from '../composables/useScanOverride'
import { describeMark } from '../composables/useMarkSource'
import { formatDateTime } from '../composables/useFormat'
import type {
  MarkValue,
  ScanMode,
  ScanOptions,
  ScanResult,
  WatchPoint,
  WatchPointCreate,
} from '../api/types'

const toast = useToast()
const rescue = useScanRescue()
const router = useRouter()

interface FormState {
  path: string
  recursive: boolean
  scanMode: ScanMode
  scanIntervalHours: number
  scanTime: string
  /** 原片处理方式。空串 = 跟随系统设置，这是有意的未设置状态 */
  markSource: MarkValue
  sourceDir: string
  note: string
}
const emptyForm = (): FormState => ({
  path: '',
  recursive: true,
  scanMode: 'realtime',
  scanIntervalHours: 6,
  scanTime: '03:00',
  markSource: '',
  sourceDir: '',
  note: '',
})

const list = ref<WatchPoint[]>([])
const loading = ref(true)
const allowedRoots = ref<string[]>([])
/**
 * 系统设置里的默认原片处理方式。
 * 表格行和新增/编辑弹窗里的「跟随系统设置」都要当场说明它会落到哪一种，
 * 否则用户只能跑去「设置」页对答案。
 */
const systemMark = ref<MarkValue>('')
const systemSourceDir = ref('')
/** 「跟随系统设置」的落点说明；系统设置本身异常（取到空串）时留空，不显示 */
const followDetail = computed(() =>
  systemMark.value ? describeMark(systemMark.value, systemSourceDir.value) : '',
)

const formOpen = ref(false)
const editingId = ref<string | null>(null)
const form = ref<FormState>(emptyForm())
const saving = ref(false)
const pickerOpen = ref(false)

const deleteTarget = ref<WatchPoint | null>(null)

// 正在提交改动的行：行内控件是「改完立刻生效」，靠它挡住同一行的并发提交
const pending = ref<Set<string>>(new Set())
const scanningId = ref<string | null>(null)

/**
 * 手动扫描的「就这一次」覆盖。空串 = 跟随该目录设置，这是默认也是常态；
 * 用完必须复位，理由见 useScanOverride 的说明。
 */
const scanOverride = useScanOverride()
const { mark: scanMark, dir: scanDir, willDelete: scanWillDelete } = scanOverride
/** 本次扫描会删源文件时，先拦一道确认 */
const deleteScanTarget = ref<WatchPoint | null>(null)

async function load(): Promise<void> {
  loading.value = true
  try {
    const [wps, settings] = await Promise.all([listWatchpoints(), getSettings()])
    list.value = wps
    allowedRoots.value = settings.watch.allowedRoots
    systemMark.value = settings.split.markSource
    systemSourceDir.value = settings.split.sourceDir
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载监控目录失败')
  } finally {
    loading.value = false
  }
}

function openAdd(): void {
  editingId.value = null
  form.value = emptyForm()
  formOpen.value = true
}

function openEdit(wp: WatchPoint): void {
  editingId.value = wp.id
  form.value = {
    path: wp.path,
    recursive: wp.recursive,
    scanMode: wp.scanMode,
    scanIntervalHours: wp.scanIntervalHours,
    scanTime: wp.scanTime,
    markSource: wp.markSource,
    sourceDir: wp.sourceDir,
    note: wp.note,
  }
  formOpen.value = true
}

async function save(): Promise<void> {
  // 新增必须选择白名单内的路径
  if (!editingId.value && !form.value.path) {
    toast.error('请先选择监控目录')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      const updated = await updateWatchpoint(editingId.value, {
        recursive: form.value.recursive,
        scanMode: form.value.scanMode,
        scanIntervalHours: form.value.scanIntervalHours,
        scanTime: form.value.scanTime,
        markSource: form.value.markSource,
        sourceDir: form.value.sourceDir,
        note: form.value.note,
      })
      replaceRow(updated)
      toast.success('已保存监控目录设置')
    } else {
      const body: WatchPointCreate = {
        path: form.value.path,
        recursive: form.value.recursive,
        scanMode: form.value.scanMode,
        scanIntervalHours: form.value.scanIntervalHours,
        scanTime: form.value.scanTime,
        markSource: form.value.markSource,
        sourceDir: form.value.sourceDir,
        note: form.value.note,
      }
      await createWatchpoint(body)
      toast.success(`已添加监控目录：${form.value.path}`)
    }
    formOpen.value = false
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

function replaceRow(updated: WatchPoint): void {
  const idx = list.value.findIndex((x) => x.id === updated.id)
  if (idx >= 0) list.value[idx] = updated
}

/**
 * 行内改动：立刻提交，用后端返回的对象替换这一行。
 * 后端会把非法值纠正回来（比如手改配置文件写成 7 小时），
 * 所以必须用它返回的结果刷新界面，而不是乐观地按用户的选择显示。
 */
async function patch(wp: WatchPoint, body: Partial<WatchPoint>): Promise<void> {
  if (pending.value.has(wp.id)) return
  pending.value.add(wp.id)
  const snapshot = { ...wp }
  replaceRow({ ...wp, ...body } as WatchPoint)
  try {
    replaceRow(await updateWatchpoint(wp.id, body))
  } catch (e) {
    replaceRow(snapshot)
    toast.error(e instanceof Error ? e.message : '更新失败')
  } finally {
    pending.value.delete(wp.id)
  }
}

function onMode(wp: WatchPoint, mode: ScanMode): void {
  void patch(wp, { scanMode: mode })
}

function onInterval(wp: WatchPoint, hours: number): void {
  void patch(wp, { scanIntervalHours: hours })
}

function onTime(wp: WatchPoint, time: string): void {
  if (!time) return
  void patch(wp, { scanTime: time })
}

/** 行内改「原片处理」：立刻提交，值由后端纠正后再回填（见 patch 的说明） */
function onMarkSource(wp: WatchPoint, value: MarkValue): void {
  void patch(wp, { markSource: value })
}

/** 归档子目录名。留空 = 跟随系统设置里的目录名，不是「没有目录」 */
function onSourceDir(wp: WatchPoint, value: string): void {
  void patch(wp, { sourceDir: value })
}

async function toggleRecursive(wp: WatchPoint): Promise<void> {
  await patch(wp, { recursive: !wp.recursive })
}

/** 扫描入口：本次要删源文件时先拦一道确认，其余照直扫 */
function askScan(wp: WatchPoint): void {
  if (scanWillDelete.value) {
    deleteScanTarget.value = wp
    return
  }
  void scanOne(wp)
}

async function scanOne(wp: WatchPoint): Promise<void> {
  if (scanningId.value) return
  scanningId.value = wp.id
  // 取走本次覆盖：options 发给后端，text 留给提示语。
  // 必须在 await 之前抓快照 —— 扫描期间用户可能又去改了下拉框
  const snap = scanOverride.take()
  try {
    const r = await scanWatchpoint(wp.id, snap.options)
    // 一次性覆盖用完即清，见 useScanOverride 的说明
    if (snap.options) scanOverride.reset()
    await load()
    // 扫到「切片已不在的原片」时交给兜底流程，见 useScanRescue
    if (rescue.offer(r, () => rescanOne(wp, snap.options))) return
    // 用后端原话汇报：它会把「跳过 N 个」「M 个还在拷贝中需等待」一并说清
    const summary = r.message || `扫描完成：发现 ${r.found} 个视频，入队 ${r.queued} 个`
    toast.success(scanOverride.annotate(summary, snap))
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '扫描失败')
  } finally {
    scanningId.value = null
  }
}

/** 确认「本次会删源文件」之后再往下走 */
async function confirmDeleteScan(): Promise<void> {
  const wp = deleteScanTarget.value
  deleteScanTarget.value = null
  if (!wp) return
  await scanOne(wp)
}

/**
 * 恢复原名之后再扫一遍。刻意不再走 offer，免得来回弹窗。
 * override 沿用发起这次动作时的取值，保证「一次点击」内部行为一致。
 */
async function rescanOne(wp: WatchPoint, override?: ScanOptions): Promise<ScanResult> {
  const r = await scanWatchpoint(wp.id, override)
  await load()
  return r
}

function askDelete(wp: WatchPoint): void {
  deleteTarget.value = wp
}

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return
  try {
    await deleteWatchpoint(deleteTarget.value.id)
    toast.success(`已移除监控目录：${deleteTarget.value.path}`)
    deleteTarget.value = null
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  }
}

/** 「下次扫描」列：实时和仅手动没有可预告的时间点，直接说明状态更清楚。 */
function nextScanText(wp: WatchPoint): string {
  if (wp.scanMode === 'realtime') return '随时（实时监听）'
  if (wp.scanMode === 'manual') return '不自动扫描'
  return formatDateTime(wp.nextScanAt)
}

/** 表头说明：一个目录当前是「自动」还是「手动」，一眼能看出来。 */
function modeHint(mode: ScanMode): string {
  if (mode === 'realtime') return '文件落进目录就会被发现并自动切分'
  if (mode === 'interval') return '按固定间隔扫一次，适合边传边等的场景'
  if (mode === 'daily') return '每天固定时间扫一次，适合夜间批处理'
  return '不自动扫描，只在你点「扫描」时才处理'
}

onMounted(load)

const columns = [
  { key: 'path', label: '路径' },
  { key: 'recursive', label: '递归', width: '80px' },
  { key: 'scanMode', label: '扫描方式', width: '290px' },
  { key: 'markSource', label: '原片处理', width: '250px' },
  { key: 'lastScanAt', label: '上次扫描', width: '170px' },
  { key: 'nextScanAt', label: '下次扫描', width: '170px' },
  { key: 'note', label: '备注' },
  { key: 'ops', label: '操作', width: '170px' },
]
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">监控目录</h1>
        <div class="page-subtitle">
          选择每个目录的扫描方式，自动切分新落盘的大视频
          <template v-if="allowedRoots.length">
            · 允许根目录见「设置」，容器内挂载的目录自动可选
          </template>
        </div>
      </div>
      <!-- 撤销分割是这一页的子页面（/watch/undo），入口只放在这里 -->
      <div class="row">
        <button class="btn" @click="router.push('/watch/undo')">撤销分割 →</button>
        <button class="btn btn--primary" @click="openAdd">+ 新增监控目录</button>
      </div>
    </div>

    <!--
      手动扫描的「就这一次」覆盖。刻意做成页级控件而不是塞进每一行：
      它的语义是「这一批手动扫描按这个来」，放进行里会被误读成能长期生效。
      长期偏好放在表格的「原片处理」列。
    -->
    <div class="scan-bar">
      <span class="scan-bar-label">手动扫描本次处理源片：</span>
      <MarkSourcePicker
        v-model="scanMark"
        v-model:source-dir="scanDir"
        follow-label="跟随各级设置"
        compact
      />
      <span v-if="scanMark" class="scan-bar-flag">仅本次 · 扫完自动复位</span>
    </div>

    <div class="card">
      <div v-if="loading" class="card-loading"><span class="spinner" /> 加载中…</div>
      <EmptyState
        v-else-if="list.length === 0"
        text="还没有监控目录"
        hint="点击右上角「新增监控目录」，选择需要自动切分的文件夹"
      />
      <DataTable v-else :columns="columns">
        <tr v-for="wp in list" :key="wp.id">
          <td class="text-ellipsis" :title="wp.path">{{ wp.path }}</td>
          <td><Toggle :model-value="wp.recursive" @update:model-value="() => toggleRecursive(wp)" /></td>
          <td>
            <ScanModePicker
              :scan-mode="wp.scanMode"
              :scan-interval-hours="wp.scanIntervalHours"
              :scan-time="wp.scanTime"
              :disabled="pending.has(wp.id)"
              @update:scan-mode="(v) => onMode(wp, v)"
              @update:scan-interval-hours="(v) => onInterval(wp, v)"
              @update:scan-time="(v) => onTime(wp, v)"
            />
          </td>
          <td>
            <MarkSourcePicker
              :model-value="wp.markSource"
              :source-dir="wp.sourceDir"
              follow-label="跟随系统设置"
              :follow-detail="followDetail"
              :disabled="pending.has(wp.id)"
              compact
              @update:model-value="(v) => onMarkSource(wp, v)"
              @update:source-dir="(v) => onSourceDir(wp, v)"
            />
          </td>
          <td class="faint">{{ formatDateTime(wp.lastScanAt) }}</td>
          <td class="faint">{{ nextScanText(wp) }}</td>
          <td class="text-ellipsis" :title="wp.note">{{ wp.note || '—' }}</td>
          <td>
            <div class="row">
              <button class="btn btn--sm" :disabled="scanningId === wp.id" @click="askScan(wp)">
                <span v-if="scanningId === wp.id" class="spinner" /> 扫描
              </button>
              <button class="btn btn--sm" @click="openEdit(wp)">编辑</button>
              <button class="btn btn--sm btn--danger" @click="askDelete(wp)">删除</button>
            </div>
          </td>
        </tr>
      </DataTable>
    </div>

    <p class="page-note">
      「扫描」按钮不受扫描方式限制：哪怕设成「仅手动」，点它就立刻扫一次。
      自动扫描只是帮你省掉这一下点击，两者互不影响。
    </p>

    <!-- 新增 / 编辑 -->
    <Modal v-model="formOpen" :title="editingId ? '编辑监控目录' : '新增监控目录'">
      <div class="field">
        <label class="field-label">目录路径</label>
        <div class="row">
          <input class="input" :value="form.path" placeholder="请选择目录" readonly />
          <button class="btn" type="button" @click="pickerOpen = true">浏览…</button>
        </div>
        <div v-if="!editingId" class="field-hint">仅可选择白名单根目录下的路径；挂载进容器的目录会自动并入白名单</div>
      </div>
      <div class="field">
        <label class="field-label">扫描方式</label>
        <ScanModePicker
          v-model:scan-mode="form.scanMode"
          v-model:scan-interval-hours="form.scanIntervalHours"
          v-model:scan-time="form.scanTime"
        />
        <div class="field-hint">{{ modeHint(form.scanMode) }}</div>
      </div>
      <div class="field">
        <label class="field-label">递归子目录</label>
        <Toggle v-model="form.recursive" />
        <div class="field-hint">开启后会一并扫描该目录下的所有子目录</div>
      </div>
      <div class="field">
        <label class="field-label">原片处理方式</label>
        <MarkSourcePicker
          v-model="form.markSource"
          v-model:source-dir="form.sourceDir"
          :follow-detail="followDetail"
        />
        <div class="field-hint">
          切分成功后怎么处置原片。留「跟随系统设置」就按系统设置里的默认值来；
          这里设了就以这里为准。
        </div>
      </div>
      <div class="field">
        <label class="field-label">备注</label>
        <input v-model="form.note" class="input" placeholder="如：相机导入目录" />
      </div>

      <template #footer>
        <button class="btn" @click="formOpen = false">取消</button>
        <button class="btn btn--primary" :disabled="saving" @click="save">
          <span v-if="saving" class="spinner" /> 保存
        </button>
      </template>
    </Modal>

    <DirPicker v-model="form.path" v-model:open="pickerOpen" />

    <!-- 删除确认 -->
    <Modal :model-value="deleteTarget !== null" title="移除监控目录" @update:model-value="(v) => { if (!v) deleteTarget = null }">
      <p>
        确定要移除监控目录
        <strong>{{ deleteTarget?.path }}</strong>
        吗？已切分的历史任务不受影响。
      </p>
      <p class="faint">如果只是想让它别再自动扫，把「扫描方式」改成「仅手动」即可，不需要删掉。</p>
      <template #footer>
        <button class="btn" @click="deleteTarget = null">取消</button>
        <button class="btn btn--danger" @click="confirmDelete">移除</button>
      </template>
    </Modal>

    <!-- 本次扫描会删源文件：不可逆，动手前必须先问一句 -->
    <Modal
      :model-value="deleteScanTarget !== null"
      title="确认删除源文件"
      @update:model-value="(v) => { if (!v) deleteScanTarget = null }"
    >
      <p>
        本次扫描会把
        <strong>{{ deleteScanTarget?.path }}</strong>
        下切分成功的原片<strong class="danger-text">永久删除</strong>，无法恢复。
      </p>
      <p class="faint">
        只影响这一次手动扫描，扫完「本次处理」会自动复位，目录的长期设置不变。
      </p>
      <template #footer>
        <button class="btn" @click="deleteScanTarget = null">取消</button>
        <button class="btn btn--danger" @click="confirmDeleteScan">确认并扫描</button>
      </template>
    </Modal>

    <!-- 扫到「切片已不在的原片」时的动作入口 -->
    <OriginRescueModal
      v-model="rescue.state.open"
      :total="rescue.state.total"
      :names="rescue.state.names"
      :busy="rescue.state.busy"
      @confirm="rescue.confirm"
    />
  </div>
</template>

<style scoped>
.card-loading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-soft);
  padding: var(--space-4);
}
.scan-bar {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
}
.scan-bar-label {
  font-size: var(--font-size-sm);
  color: var(--color-text-soft);
  /* 与紧凑控件等高，让文字基线跟下拉框对齐 */
  line-height: 30px;
  white-space: nowrap;
}
.scan-bar-flag {
  align-self: center;
  font-size: var(--font-size-xs);
  color: var(--color-warning);
  background: var(--color-warning-soft);
  border-radius: var(--radius-sm);
  padding: 2px var(--space-2);
  white-space: nowrap;
}
.danger-text {
  color: var(--color-danger);
}
.page-note {
  margin-top: var(--space-3);
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  line-height: 1.6;
}
</style>

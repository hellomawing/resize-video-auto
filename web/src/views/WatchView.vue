<script setup lang="ts">
import { onMounted, ref } from 'vue'
import DataTable from '../components/DataTable.vue'
import Toggle from '../components/Toggle.vue'
import Modal from '../components/Modal.vue'
import EmptyState from '../components/EmptyState.vue'
import DirPicker from '../components/DirPicker.vue'
import ScanModePicker from '../components/ScanModePicker.vue'
import OriginRescueModal from '../components/OriginRescueModal.vue'
import { listWatchpoints, createWatchpoint, updateWatchpoint, deleteWatchpoint, scanWatchpoint } from '../api/watchpoints'
import { getSettings } from '../api/settings'
import { useToast } from '../composables/useToast'
import { useScanRescue } from '../composables/useScanRescue'
import { formatDateTime } from '../composables/useFormat'
import type { ScanMode, ScanResult, WatchPoint, WatchPointCreate, Settings } from '../api/types'

const toast = useToast()
const rescue = useScanRescue()

interface FormState {
  path: string
  recursive: boolean
  scanMode: ScanMode
  scanIntervalHours: number
  scanTime: string
  note: string
}
const emptyForm = (): FormState => ({
  path: '',
  recursive: true,
  scanMode: 'realtime',
  scanIntervalHours: 6,
  scanTime: '03:00',
  note: '',
})

const list = ref<WatchPoint[]>([])
const loading = ref(true)
const allowedRoots = ref<string[]>([])

const formOpen = ref(false)
const editingId = ref<string | null>(null)
const form = ref<FormState>(emptyForm())
const saving = ref(false)
const pickerOpen = ref(false)

const deleteTarget = ref<WatchPoint | null>(null)

// 正在提交改动的行：行内控件是「改完立刻生效」，靠它挡住同一行的并发提交
const pending = ref<Set<string>>(new Set())
const scanningId = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  try {
    const [wps, settings] = await Promise.all([listWatchpoints(), getSettings()])
    list.value = wps
    allowedRoots.value = settings.watch.allowedRoots
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

async function toggleRecursive(wp: WatchPoint): Promise<void> {
  await patch(wp, { recursive: !wp.recursive })
}

async function scanOne(wp: WatchPoint): Promise<void> {
  if (scanningId.value) return
  scanningId.value = wp.id
  try {
    const r = await scanWatchpoint(wp.id)
    await load()
    // 扫到「切片已不在的原片」时交给兜底流程，见 useScanRescue
    if (rescue.offer(r, () => rescanOne(wp))) return
    // 用后端原话汇报：它会把「跳过 N 个」「M 个还在拷贝中需等待」一并说清
    toast.success(r.message || `扫描完成：发现 ${r.found} 个视频，入队 ${r.queued} 个`)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '扫描失败')
  } finally {
    scanningId.value = null
  }
}

/** 恢复原名之后再扫一遍。刻意不再走 offer，免得来回弹窗 */
async function rescanOne(wp: WatchPoint): Promise<ScanResult> {
  const r = await scanWatchpoint(wp.id)
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
            · 允许根目录：{{ allowedRoots.join('、') }}
          </template>
        </div>
      </div>
      <button class="btn btn--primary" @click="openAdd">+ 新增监控目录</button>
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
          <td class="faint">{{ formatDateTime(wp.lastScanAt) }}</td>
          <td class="faint">{{ nextScanText(wp) }}</td>
          <td class="text-ellipsis" :title="wp.note">{{ wp.note || '—' }}</td>
          <td>
            <div class="row">
              <button class="btn btn--sm" :disabled="scanningId === wp.id" @click="scanOne(wp)">
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
        <div v-if="!editingId" class="field-hint">仅可选择白名单根目录（{{ allowedRoots.join('、') || '无' }}）下的路径</div>
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
.page-note {
  margin-top: var(--space-3);
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  line-height: 1.6;
}
</style>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import DataTable from '../components/DataTable.vue'
import Toggle from '../components/Toggle.vue'
import Modal from '../components/Modal.vue'
import EmptyState from '../components/EmptyState.vue'
import ScanModePicker from '../components/ScanModePicker.vue'
import MarkSourcePicker from '../components/MarkSourcePicker.vue'
import OriginRescueModal from '../components/OriginRescueModal.vue'
import { listWatchpoints, deleteWatchpoint, scanWatchpoint, updateWatchpoint } from '../api/watchpoints'
import { getSettings } from '../api/settings'
import { useToast } from '../composables/useToast'
import { useScanRescue } from '../composables/useScanRescue'
import { describeMark } from '../composables/useMarkSource'
import { formatDateTime } from '../composables/useFormat'
import type {
  FilterRule,
  MarkValue,
  ScanMode,
  ScanResult,
  WatchPoint,
} from '../api/types'

const toast = useToast()
const rescue = useScanRescue()
const router = useRouter()

const list = ref<WatchPoint[]>([])
const loading = ref(true)
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

const deleteTarget = ref<WatchPoint | null>(null)

// 正在提交改动的行：行内控件是「改完立刻生效」，靠它挡住同一行的并发提交
const pending = ref<Set<string>>(new Set())
const scanningId = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  try {
    const [wps, settings] = await Promise.all([listWatchpoints(), getSettings()])
    list.value = wps
    systemMark.value = settings.split.markSource
    systemSourceDir.value = settings.split.sourceDir
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载监控目录失败')
  } finally {
    loading.value = false
  }
}

/**
 * 新增 / 编辑都去**独立页面**。内容里有一整块过滤规则（只看/不看哪些类型与名字），
 * 弹窗装不下 —— 详见表单页自己的说明。路径也不再在这里改。
 */
function openAdd(): void {
  void router.push('/watch/new')
}

function openEdit(wp: WatchPoint): void {
  void router.push(`/watch/edit/${wp.id}`)
}

/** 规则的简短写法：正则用 /…/ 包起来，好和「包含」区分 */
function ruleText(rule: FilterRule): string {
  return rule.mode === 'regex' ? `/${rule.value}/` : rule.value
}

/**
 * 表格「过滤」列：一句话说清这个目录配了什么规则。
 * 空规则 = 不过滤，显示「—」而不是留白 —— 留白会让人以为是没加载出来。
 */
function filterSummary(wp: WatchPoint): string {
  const f = wp.filters
  if (!f) return '—'
  const parts: string[] = []
  if (f.extInclude?.length) parts.push('仅 ' + f.extInclude.join(' '))
  if (f.extExclude?.length) parts.push('排除 ' + f.extExclude.join(' '))
  const inc = (f.nameInclude || []).filter((r) => r.value.trim())
  const exc = (f.nameExclude || []).filter((r) => r.value.trim())
  if (inc.length) parts.push('仅 ' + inc.map(ruleText).join('、'))
  if (exc.length) parts.push('排除 ' + exc.map(ruleText).join('、'))
  return parts.length ? parts.join(' · ') : '—'
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

/**
 * 手动扫描一个监控目录。原片怎么处理**不在这里选** —— 由该行「原片处理」
 * 列的长期设置决定：接口不传覆盖参数时，后端按「目录 → 系统设置」逐级解析，
 * 与自动扫描走的是同一条路，不会出现「手动扫和自动扫行为不一样」。
 */
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

/** 恢复原名之后再扫一遍。刻意不再走 offer，免得来回弹窗。 */
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

onMounted(load)

const columns = [
  { key: 'path', label: '路径' },
  { key: 'recursive', label: '递归', width: '80px' },
  { key: 'filters', label: '过滤', width: '220px' },
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
          · 可浏览范围为容器已挂载的目录
        </div>
      </div>
      <!-- 撤销分割是这一页的子页面（/watch/undo），入口只放在这里 -->
      <div class="row">
        <button class="btn" @click="router.push('/watch/undo')">撤销分割 →</button>
        <button class="btn btn--primary" @click="openAdd">+ 新增监控目录</button>
      </div>
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
          <td class="text-ellipsis" :title="filterSummary(wp)">{{ filterSummary(wp) }}</td>
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
      扫出来的原片怎么处理，看这一行「原片处理」列 —— 手动与自动扫描用的是同一个设置。
    </p>

    <!-- 新增 / 编辑都在独立页面（/watch/new 与 /watch/edit/:id）：
         过滤规则那一整块塞不进弹窗，路径也会被滚出视野 -->
    <!-- 删除确认 -->
    <Modal :model-value="deleteTarget !== null" title="移除监控目录" @update:model-value="(v) => { if (!v) deleteTarget = null }">
      <p>
        确定要移除监控目录
        <strong class="path-break">{{ deleteTarget?.path }}</strong>
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
/* 弹窗正文里出现的路径：没有空格可断，只能逐字符断行，否则横向顶出对话框 */
.path-break {
  word-break: break-all;
}
.page-note {
  margin-top: var(--space-3);
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  line-height: 1.6;
}
</style>

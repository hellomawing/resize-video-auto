<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import DataTable from '../components/DataTable.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ProgressBar from '../components/ProgressBar.vue'
import Modal from '../components/Modal.vue'
import EmptyState from '../components/EmptyState.vue'
import LogViewer from '../components/LogViewer.vue'
import { listJobs, getJobLog, retryJob, cancelJob, deleteJob, clearJobs } from '../api/jobs'
import { listFailures, retryFailure, clearFailures } from '../api/failures'
import { useToast } from '../composables/useToast'
import { useWebSocket } from '../composables/useWebSocket'
import { useJobStore } from '../composables/useJobStore'
import { formatBytes, formatDateTime, formatDuration } from '../composables/useFormat'
import type { FailureRecord, Job, JobStatus } from '../api/types'

const toast = useToast()
const ws = useWebSocket()
const jobStore = useJobStore()

const STATUS_OPTIONS: { value: JobStatus | ''; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'queued', label: '排队中' },
  { value: 'running', label: '运行中' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
  { value: 'canceled', label: '已取消' },
  { value: 'skipped', label: '已跳过' },
]

const filterStatus = ref<JobStatus | ''>('')
const query = ref('')
const limit = ref(20)
const offset = ref(0)
const total = ref(0)
const loading = ref(true)
const items = ref<Job[]>([])

const detailId = ref<string | null>(null)
const logLoading = ref(false)
const deleteTarget = ref<Job | null>(null)
const clearConfirm = ref(false)

// 「处理失败的文件」：切不动的原片原样留在磁盘上，清单只是登记与解释。
// 页面顶部只有清单非空时才出现，平时不占地方。
const failures = ref<FailureRecord[]>([])
const failOpen = ref(true)
const failBusy = ref('')
const failClearConfirm = ref(false)

const TRIGGER_LABEL: Record<Job['trigger'], string> = {
  watch: '监控',
  manual: '手动',
  retry: '重试',
}
const PHASE_LABEL: Record<Job['phase'], string> = {
  waiting: '等待中',
  probe: '探测中',
  splitting: '切分中',
  verifying: '校验中',
  marking: '标记源文件',
  done: '完成',
}

// 表格行优先取全局 store 中的最新状态（含 WS 实时进度），否则用分页返回的值
const rows = computed<Job[]>(() =>
  items.value.map((it) => jobStore.byId.get(it.id) ?? it),
)

const detailJob = computed<Job | null>(() =>
  detailId.value ? (jobStore.byId.get(detailId.value) ?? null) : null,
)
const detailLogs = computed<string[]>(() =>
  detailId.value ? (jobStore.logs.get(detailId.value) ?? []) : [],
)

// 搜索输入做 300ms 防抖，避免每次按键都打接口
let debounceTimer: number | null = null
watch(query, () => {
  if (debounceTimer !== null) window.clearTimeout(debounceTimer)
  debounceTimer = window.setTimeout(() => {
    offset.value = 0
    void load()
  }, 300)
})

async function load(): Promise<void> {
  loading.value = true
  try {
    const res = await listJobs({
      status: filterStatus.value,
      q: query.value.trim() || undefined,
      limit: limit.value,
      offset: offset.value,
    })
    items.value = res.items
    total.value = res.total
    res.items.forEach((it) => jobStore.upsert(it))
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载任务列表失败')
  } finally {
    loading.value = false
  }
}

/**
 * 失败清单是辅助信息，加载失败不该把任务列表一起拖垮 —— 所以单独 try，
 * 出错就保持上一次的内容。
 */
async function loadFailures(): Promise<void> {
  try {
    const res = await listFailures()
    failures.value = res.items
  } catch {
    // 忽略：任务队列本身仍可正常使用
  }
}

async function retryFailureItem(f: FailureRecord): Promise<void> {
  failBusy.value = f.path
  try {
    await retryFailure(f.path)
    toast.success('已重新入队，等待切分')
    await Promise.all([loadFailures(), load()])
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '重试失败')
    await loadFailures()
  } finally {
    failBusy.value = ''
  }
}

async function ignoreFailure(f: FailureRecord): Promise<void> {
  failBusy.value = f.path
  try {
    await clearFailures([f.path])
    toast.success('已从列表移除（磁盘文件未改动）')
    await loadFailures()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '操作失败')
  } finally {
    failBusy.value = ''
  }
}

async function confirmIgnoreAllFailures(): Promise<void> {
  try {
    await clearFailures()
    toast.success('已清空失败清单（磁盘文件未改动）')
    failClearConfirm.value = false
    await loadFailures()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '操作失败')
  }
}

function changeStatus(): void {
  offset.value = 0
  void load()
}

function prevPage(): void {
  if (offset.value === 0) return
  offset.value = Math.max(0, offset.value - limit.value)
  void load()
}

function nextPage(): void {
  if (offset.value + limit.value >= total.value) return
  offset.value += limit.value
  void load()
}

async function openDetail(job: Job): Promise<void> {
  detailId.value = job.id
  logLoading.value = true
  try {
    const log = await getJobLog(job.id)
    jobStore.setLog(job.id, log.lines)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载日志失败')
  } finally {
    logLoading.value = false
  }
}

function closeDetail(): void {
  detailId.value = null
}

async function retry(job: Job): Promise<void> {
  try {
    await retryJob(job.id)
    toast.success('已重新入队，等待切分')
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '重试失败')
  }
}

async function cancel(job: Job): Promise<void> {
  try {
    await cancelJob(job.id)
    toast.success('已发送取消请求')
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '取消失败')
  }
}

function askDelete(job: Job): void {
  deleteTarget.value = job
}

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return
  try {
    await deleteJob(deleteTarget.value.id)
    toast.success('已删除任务记录')
    deleteTarget.value = null
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  }
}

async function confirmClear(): Promise<void> {
  try {
    await clearJobs(['success', 'failed', 'canceled', 'skipped'])
    toast.success('已清理已结束的任务')
    clearConfirm.value = false
    offset.value = 0
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '清理失败')
  }
}

// 任务实时事件到达时，若当前页不含该任务也刷新一次（保持总数与统计准确）
function onWsMessage(msg: { type: string; job?: Job }): void {
  if (msg.type === 'job.created' || msg.type === 'job.updated' || msg.type === 'job.progress') {
    if (detailId.value === null) void load()
  }
  // 任务收尾都会影响失败清单：失败会新增一条，成功/取消会清掉一条。
  // 只在终态刷新，避免每来一次进度事件就打一遍接口。
  const status = msg.job?.status
  if (msg.type === 'job.updated' && (status === 'failed' || status === 'success' ||
      status === 'canceled')) {
    void loadFailures()
  }
}
let unsub: (() => void) | null = null

onMounted(() => {
  void load()
  void loadFailures()
  unsub = ws.on(onWsMessage)
})
onUnmounted(() => unsub?.())

const columns = [
  { key: 'name', label: '文件名' },
  { key: 'size', label: '大小', width: '110px', align: 'right' as const },
  { key: 'status', label: '状态', width: '90px' },
  { key: 'progress', label: '进度', width: '170px' },
  { key: 'mode', label: '模式', width: '80px' },
  { key: 'trigger', label: '来源', width: '80px' },
  { key: 'duration', label: '耗时', width: '90px' },
  { key: 'createdAt', label: '创建时间', width: '160px' },
]
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">任务队列</h1>
        <div class="page-subtitle">共 {{ total }} 个任务</div>
      </div>
      <button class="btn" @click="clearConfirm = true">清理已完成</button>
    </div>

    <!--
      处理失败的文件：切不动的文件原样留在磁盘上（本工具绝不因为失败而改动原片），
      这里集中列出来 —— 否则用户只能一条条翻任务日志才知道哪些没处理成。
      清单为空时整块不出现。
    -->
    <div v-if="failures.length" class="card fail-panel">
      <div class="fail-head">
        <div class="fail-title">
          <h2 class="card-title">处理失败的文件</h2>
          <span class="fail-count">{{ failures.length }}</span>
        </div>
        <div class="row">
          <button class="btn btn--sm" @click="failOpen = !failOpen">
            {{ failOpen ? '收起' : '展开' }}
          </button>
          <button class="btn btn--sm" @click="failClearConfirm = true">全部忽略</button>
        </div>
      </div>
      <div class="field-hint fail-hint">
        这些文件没能完成无损切分，<strong>原片已原样保留</strong> —— 没有被改名、移动或删除。
        「重试」会再试一次；「忽略」只是把它们从这份清单里去掉，不碰磁盘文件。
        文件一旦被替换或改动（大小或修改时间变了），后端会自动重新尝试，不必手动清理记录。
      </div>
      <div v-if="failOpen" class="fail-list">
        <div v-for="f in failures" :key="f.path" class="fail-item">
          <div class="fail-main">
            <div class="fail-name mono" :title="f.path">{{ f.name }}</div>
            <div class="fail-reason">{{ f.reason }}</div>
            <div class="fail-meta faint">
              {{ formatBytes(f.size) }} · 失败于 {{ formatDateTime(f.at) }}
              <template v-if="f.jobId"> · 任务 {{ f.jobId }}</template>
            </div>
          </div>
          <div class="row">
            <button class="btn btn--sm" :disabled="failBusy === f.path" @click="retryFailureItem(f)">
              重试
            </button>
            <button class="btn btn--sm" :disabled="failBusy === f.path" @click="ignoreFailure(f)">
              忽略
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 筛选 -->
    <div class="card toolbar">
      <select v-model="filterStatus" class="select" style="width: 160px" @change="changeStatus">
        <option v-for="o in STATUS_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
      <input v-model="query" class="input" placeholder="按文件名搜索…" style="max-width: 280px" />
    </div>

    <div class="card">
      <div v-if="loading && items.length === 0" class="card-loading"><span class="spinner" /> 加载中…</div>
      <EmptyState
        v-else-if="rows.length === 0"
        text="没有符合条件的任务"
        hint="调整筛选条件，或到「监控目录」触发新的切分"
      />
      <DataTable v-else :columns="columns">
        <tr v-for="job in rows" :key="job.id" class="is-clickable" @click="openDetail(job)">
          <td class="text-ellipsis" :title="job.srcName">{{ job.srcName }}</td>
          <td style="text-align: right">{{ formatBytes(job.srcSize) }}</td>
          <td><StatusBadge :status="job.status" /></td>
          <td>
            <ProgressBar
              v-if="job.status === 'running'"
              :value="job.progress"
              :parts-done="job.partsDone"
              :parts-total="job.partsTotal"
            />
            <span v-else class="faint">—</span>
          </td>
          <td>{{ job.usedMode || job.mode }}</td>
          <td>{{ TRIGGER_LABEL[job.trigger] }}</td>
          <td>{{ formatDuration(job.durationSec) }}</td>
          <td class="faint">{{ formatDateTime(job.createdAt) }}</td>
        </tr>
      </DataTable>

      <!-- 分页 -->
      <div v-if="total > limit" class="pager">
        <span class="faint">第 {{ offset / limit + 1 }} / {{ Math.ceil(total / limit) }} 页</span>
        <div class="row">
          <button class="btn btn--sm" :disabled="offset === 0" @click="prevPage">上一页</button>
          <button class="btn btn--sm" :disabled="offset + limit >= total" @click="nextPage">下一页</button>
        </div>
      </div>
    </div>

    <!-- 任务详情 -->
    <Modal :model-value="detailId !== null" title="任务详情" wide @update:model-value="closeDetail">
      <template v-if="detailJob">
        <div class="detail-grid">
          <div><span class="k">任务 ID</span><span class="v mono">{{ detailJob.id }}</span></div>
          <div><span class="k">源文件</span><span class="v text-ellipsis" :title="detailJob.src">{{ detailJob.src }}</span></div>
          <div><span class="k">大小</span><span class="v">{{ formatBytes(detailJob.srcSize) }}</span></div>
          <div><span class="k">状态</span><span class="v"><StatusBadge :status="detailJob.status" /></span></div>
          <div><span class="k">阶段</span><span class="v">{{ PHASE_LABEL[detailJob.phase] }}</span></div>
          <div>
            <span class="k">进度</span>
            <span class="v">
              <ProgressBar
                v-if="detailJob.status === 'running'"
                :value="detailJob.progress"
                :parts-done="detailJob.partsDone"
                :parts-total="detailJob.partsTotal"
              />
              <span v-else class="faint">—</span>
            </span>
          </div>
          <div><span class="k">模式</span><span class="v">{{ detailJob.usedMode || detailJob.mode }}</span></div>
          <div><span class="k">来源</span><span class="v">{{ TRIGGER_LABEL[detailJob.trigger] }}</span></div>
          <div><span class="k">输出目录</span><span class="v text-ellipsis" :title="detailJob.outdir">{{ detailJob.outdir }}</span></div>
          <div><span class="k">创建时间</span><span class="v">{{ formatDateTime(detailJob.createdAt) }}</span></div>
          <div><span class="k">开始时间</span><span class="v">{{ formatDateTime(detailJob.startedAt) }}</span></div>
          <div><span class="k">结束时间</span><span class="v">{{ formatDateTime(detailJob.finishedAt) }}</span></div>
          <div><span class="k">耗时</span><span class="v">{{ formatDuration(detailJob.durationSec) }}</span></div>
        </div>

        <div v-if="detailJob.message" class="detail-msg">{{ detailJob.message }}</div>
        <div v-if="detailJob.error" class="detail-err">错误：{{ detailJob.error }}</div>

        <div v-if="detailJob.produced.length" class="detail-produced">
          <div class="field-label">产出切片</div>
          <div v-for="p in detailJob.produced" :key="p.name" class="produced-row">
            <span class="text-ellipsis">{{ p.name }}</span>
            <span class="faint">{{ formatBytes(p.size) }}</span>
          </div>
        </div>

        <div class="detail-log">
          <div class="field-label">运行日志（实时）</div>
          <LogViewer :lines="detailLogs" :loading="logLoading" />
        </div>
      </template>

      <template #footer>
        <div class="row-between grow" v-if="detailJob">
          <div class="row">
            <button
              v-if="detailJob.status === 'queued' || detailJob.status === 'running'"
              class="btn btn--sm btn--danger"
              @click="cancel(detailJob); closeDetail()"
            >
              取消
            </button>
            <button
              v-if="detailJob.status === 'failed' || detailJob.status === 'canceled'"
              class="btn btn--sm"
              @click="retry(detailJob)"
            >
              重试
            </button>
            <button class="btn btn--sm btn--danger" @click="askDelete(detailJob); detailId = null">删除</button>
          </div>
          <button class="btn" @click="closeDetail">关闭</button>
        </div>
      </template>
    </Modal>

    <!-- 删除确认 -->
    <Modal :model-value="deleteTarget !== null" title="删除任务" @update:model-value="(v) => { if (!v) deleteTarget = null }">
      <p>确定要删除任务 <strong>{{ deleteTarget?.srcName }}</strong> 的记录及其日志吗？</p>
      <template #footer>
        <button class="btn" @click="deleteTarget = null">取消</button>
        <button class="btn btn--danger" @click="confirmDelete">删除</button>
      </template>
    </Modal>

    <!-- 清理确认 -->
    <Modal v-model="clearConfirm" title="清理已完成任务">
      <p>将删除所有已结束（成功 / 失败 / 已取消 / 已跳过）的任务记录与日志，进行中的任务不受影响。确定继续吗？</p>
      <template #footer>
        <button class="btn" @click="clearConfirm = false">取消</button>
        <button class="btn btn--danger" @click="confirmClear">清理</button>
      </template>
    </Modal>

    <!-- 清空失败清单前问一句：虽然不动文件，但清掉之后提示就没了 -->
    <Modal v-model="failClearConfirm" title="清空失败清单">
      <p>将把 {{ failures.length }} 条失败记录从清单里移除。</p>
      <p class="faint">
        只会删记录，<strong>不会动磁盘上的任何文件</strong>。被移出清单的文件如果还在监控目录里，
        下次扫描会重新尝试；仍然切不动的话，它会再次出现在这份清单里。
      </p>
      <template #footer>
        <button class="btn" @click="failClearConfirm = false">取消</button>
        <button class="btn btn--primary" @click="confirmIgnoreAllFailures">确认移除</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
}
/* 「处理失败的文件」——只在清单非空时出现的置顶卡片 */
.fail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4) var(--space-1);
}
.fail-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.fail-count {
  background: var(--color-danger-soft);
  color: var(--color-danger);
  border-radius: var(--radius-sm);
  padding: 1px var(--space-2);
  font-size: var(--font-size-xs);
  font-weight: 600;
}
.fail-hint {
  padding: 0 var(--space-4) var(--space-3);
}
.fail-list {
  border-top: 1px solid var(--color-border);
}
.fail-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
}
.fail-item:last-child {
  border-bottom: none;
}
.fail-main {
  min-width: 0;
}
.fail-name {
  font-size: var(--font-size-sm);
  word-break: break-all;
}
.fail-reason {
  font-size: var(--font-size-sm);
  color: var(--color-danger);
  margin-top: 2px;
  word-break: break-word;
}
.fail-meta {
  font-size: var(--font-size-xs);
  margin-top: 2px;
}
.card-loading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-soft);
  padding: var(--space-4);
}
.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) var(--space-4);
}
.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-3) var(--space-5);
  margin-bottom: var(--space-4);
}
.detail-grid .k {
  display: block;
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  margin-bottom: 2px;
}
.detail-grid .v {
  font-size: var(--font-size-sm);
}
.detail-msg {
  background: var(--color-primary-soft);
  color: var(--color-primary-dark);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  margin-bottom: var(--space-3);
}
.detail-err {
  background: var(--color-danger-soft);
  color: var(--color-danger);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
  margin-bottom: var(--space-3);
}
.detail-produced {
  margin-bottom: var(--space-4);
}
.produced-row {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--font-size-sm);
  padding: var(--space-1) 0;
  border-bottom: 1px dashed var(--color-border);
}
.detail-log {
  margin-top: var(--space-2);
}
.mono {
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  word-break: break-all;
}
</style>

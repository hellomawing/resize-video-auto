<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import StatCard from '../components/StatCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ProgressBar from '../components/ProgressBar.vue'
import EmptyState from '../components/EmptyState.vue'
import DataTable from '../components/DataTable.vue'
import Modal from '../components/Modal.vue'
import MarkSourcePicker from '../components/MarkSourcePicker.vue'
import OriginRescueModal from '../components/OriginRescueModal.vue'
import { getHealth, getStats, scanAll } from '../api/system'
import { listJobs } from '../api/jobs'
import { useToast } from '../composables/useToast'
import { useWebSocket } from '../composables/useWebSocket'
import { useJobStore } from '../composables/useJobStore'
import { useScanRescue } from '../composables/useScanRescue'
import { useScanOverride } from '../composables/useScanOverride'
import { formatBytes, formatDateTime, formatDuration } from '../composables/useFormat'
import type { Health, Stats, Job, ScanOptions, ScanResult } from '../api/types'

const router = useRouter()
const toast = useToast()
const ws = useWebSocket()
const jobStore = useJobStore()
const rescue = useScanRescue()

/**
 * 手动扫描的「就这一次」原片处理方式。默认空串 = 各目录按自己的设置、再退回系统设置。
 * 语义与复位规则见 useScanOverride。
 */
const scanOverride = useScanOverride()
const { mark: scanMark, dir: scanDir, willDelete: scanWillDelete } = scanOverride
/** 本次会删源文件时先拦一道确认 */
const confirmDeleteScan = ref(false)

const health = ref<Health | null>(null)
const stats = ref<Stats | null>(null)
const recent = ref<Job[]>([])
const loading = ref(true)
const scanning = ref(false)

async function loadAll(): Promise<void> {
  loading.value = true
  try {
    const [h, s, j] = await Promise.all([getHealth(), getStats(), listJobs({ limit: 10 })])
    health.value = h
    stats.value = s
    recent.value = j.items
    // 同步进全局任务 map，便于其它页面保持最新
    j.items.forEach((it) => jobStore.upsert(it))
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载概览数据失败')
  } finally {
    loading.value = false
  }
}

/** 扫描入口：本次要删源文件时先拦一道确认，其余照直扫 */
function askScan(): void {
  if (scanWillDelete.value) {
    confirmDeleteScan.value = true
    return
  }
  void doScan()
}

async function doScan(): Promise<void> {
  scanning.value = true
  // 取走本次覆盖：options 发给后端，text 留给提示语。
  // 必须在 await 之前抓快照 —— 扫描期间用户可能又去改了下拉框
  const snap = scanOverride.take()
  try {
    const r = await scanAll(snap.options)
    // 一次性覆盖用完即清，见 useScanOverride 的说明
    if (snap.options) scanOverride.reset()
    // 任务数会变化，稍后由 WS 或直接刷新统计
    await loadAll()
    // 扫到「切片已不在的原片」时交给兜底流程：这种结果有明确的后续动作，
    // 不该只丢一句「已跳过」就完事——用户会以为工具不管它了
    if (rescue.offer(r, () => rescanNow(snap.options))) return
    // 用后端原话汇报：它会把「跳过了 N 个已处理文件、为什么跳过」一并说清。
    // 前端另拼一句「发现 0 个视频」会把真正的原因盖掉，用户就无从判断了。
    const summary = r.message || `扫描完成：发现 ${r.found} 个视频，入队 ${r.queued} 个`
    toast.success(scanOverride.annotate(summary, snap))
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '扫描失败')
  } finally {
    scanning.value = false
  }
}

/**
 * 恢复原名之后再扫一遍。刻意不再走 offer，免得来回弹窗。
 * options 沿用发起这次动作时的取值，保证「一次点击」内部行为一致。
 */
async function rescanNow(options?: ScanOptions): Promise<ScanResult> {
  const r = await scanAll(options)
  await loadAll()
  return r
}

/** 确认「本次会删源文件」之后再往下走 */
async function confirmDeleteThenScan(): Promise<void> {
  confirmDeleteScan.value = false
  await doScan()
}

// 实时事件：任务或扫描变化时刷新统计卡片（不强制刷新最近任务，避免列表跳动）
function onWsMessage(msg: { type: string }): void {
  if (msg.type === 'job.created' || msg.type === 'job.updated' || msg.type === 'scan.finished') {
    void getStats()
      .then((s) => {
        stats.value = s
      })
      .catch(() => {})
  }
}

let unsub: (() => void) | null = null

onMounted(() => {
  void loadAll()
  unsub = ws.on(onWsMessage)
})
onUnmounted(() => {
  unsub?.()
})

const uptimeText = computed(() => {
  if (!health.value) return '-'
  return formatDuration(health.value.uptimeSec)
})

// `ffmpeg -version` 的第一行各平台格式差别很大：Linux 发行版版还算短，
// Windows 版会拖一大串官网地址与版权年份，整行塞进卡片会把版面撑变形。
// 这里只抽出「版本号」三个字，检测不到就退化成一句状态。
const ffmpegVersion = computed(() => {
  const hit = health.value?.ffmpeg.version?.match(/\d+(?:\.\d+)+/)
  if (hit) return hit[0]
  return health.value?.ffmpeg.ok ? '已就绪' : '未检测'
})

const recentColumns = [
  { key: 'name', label: '文件名' },
  { key: 'size', label: '大小', width: '120px' },
  { key: 'status', label: '状态', width: '90px' },
  { key: 'progress', label: '进度', width: '160px' },
  { key: 'duration', label: '耗时', width: '90px' },
  { key: 'createdAt', label: '创建时间', width: '160px' },
]
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">概览</h1>
        <div class="page-subtitle">服务运行状态与近期任务一览</div>
      </div>
      <!--
        手动扫描的一次性原片处理方式。默认「跟随各级设置」，选了就只作用于
        这一次「立即扫描」，扫完自动复位（见 useScanOverride）。
      -->
      <div class="scan-head">
        <MarkSourcePicker
          v-model="scanMark"
          v-model:source-dir="scanDir"
          follow-label="本次跟随各级设置"
          compact
        />
        <span v-if="scanMark" class="scan-flag">仅本次 · 扫完自动复位</span>
        <button class="btn btn--primary" :disabled="scanning" @click="askScan">
          <span v-if="scanning" class="spinner" />
          {{ scanning ? '扫描中…' : '立即扫描' }}
        </button>
      </div>
    </div>

    <div v-if="loading && !health" class="card"><span class="spinner" /> 加载中…</div>

    <template v-else>
      <!-- 服务状态卡片 -->
      <div class="stat-grid">
        <StatCard
          title="服务状态"
          :value="health?.ok ? '正常' : '异常'"
          :accent="health?.ok ? 'success' : 'danger'"
          :hint="`版本 ${health?.version ?? '-'} · 已运行 ${uptimeText}`"
        />
        <StatCard
          title="FFmpeg"
          :value="ffmpegVersion"
          accent="primary"
          :hint="health?.ffmpeg.ok ? `路径 ${health?.ffmpeg.path}` : '未检测到 FFmpeg'"
        />
        <StatCard title="排队中" :value="stats?.jobs.queued ?? 0" accent="muted" />
        <StatCard title="运行中" :value="stats?.jobs.running ?? 0" accent="primary" />
        <StatCard title="成功" :value="stats?.jobs.success ?? 0" accent="success" />
        <StatCard title="失败" :value="stats?.jobs.failed ?? 0" accent="danger" />
        <StatCard
          title="今日处理量"
          :value="formatBytes(stats?.todayBytes)"
          accent="primary"
          :hint="`累计切片 ${stats?.totalParts ?? 0} 段`"
        />
        <StatCard
          title="监控目录"
          :value="stats?.watchpoints ?? 0"
          accent="muted"
          hint="正在自动发现的目录数"
        />
      </div>

      <!-- 最近任务 -->
      <div class="card">
        <div class="row-between card-title">
          <span>最近 10 条任务</span>
          <button class="btn btn--sm btn--ghost" @click="router.push('/jobs')">查看全部 →</button>
        </div>
        <EmptyState v-if="recent.length === 0" text="暂无任务记录" hint="扫描监控目录后，处理过的文件会显示在这里" />
        <DataTable v-else :columns="recentColumns">
          <tr
            v-for="job in recent"
            :key="job.id"
            class="is-clickable"
            @click="router.push('/jobs')"
          >
            <td class="text-ellipsis" :title="job.srcName">{{ job.srcName }}</td>
            <td>{{ formatBytes(job.srcSize) }}</td>
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
            <td>{{ formatDuration(job.durationSec) }}</td>
            <td class="faint">{{ formatDateTime(job.createdAt) }}</td>
          </tr>
        </DataTable>
      </div>
    </template>
  </div>

  <!-- 本次扫描会删源文件：不可逆，动手前必须先问一句 -->
  <Modal v-model="confirmDeleteScan" title="确认删除源文件">
    <p>
      本次「立即扫描」入队的任务，会在切分成功后把原片
      <strong class="danger-text">永久删除</strong>，无法恢复。
    </p>
    <p class="faint">
      只影响这一次，扫完这个选择会自动复位；各监控目录的长期设置不受影响。
    </p>
    <template #footer>
      <button class="btn" @click="confirmDeleteScan = false">取消</button>
      <button class="btn btn--danger" @click="confirmDeleteThenScan">确认并扫描</button>
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
</template>

<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-4);
}
.scan-head {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.scan-flag {
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
</style>

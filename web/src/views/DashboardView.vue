<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import StatCard from '../components/StatCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ProgressBar from '../components/ProgressBar.vue'
import EmptyState from '../components/EmptyState.vue'
import DataTable from '../components/DataTable.vue'
import { getHealth, getStats, scanAll } from '../api/system'
import { listJobs } from '../api/jobs'
import { useToast } from '../composables/useToast'
import { useWebSocket } from '../composables/useWebSocket'
import { useJobStore } from '../composables/useJobStore'
import { formatBytes, formatDateTime, formatDuration } from '../composables/useFormat'
import type { Health, Stats, Job } from '../api/types'

const router = useRouter()
const toast = useToast()
const ws = useWebSocket()
const jobStore = useJobStore()

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

async function doScan(): Promise<void> {
  scanning.value = true
  try {
    const r = await scanAll()
    toast.success(`扫描完成：发现 ${r.found} 个视频，入队 ${r.queued} 个`)
    // 任务数会变化，稍后由 WS 或直接刷新统计
    await loadAll()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '扫描失败')
  } finally {
    scanning.value = false
  }
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
      <button class="btn btn--primary" :disabled="scanning" @click="doScan">
        <span v-if="scanning" class="spinner" />
        {{ scanning ? '扫描中…' : '立即扫描' }}
      </button>
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
          title="监控 / 定时"
          :value="`${stats?.watchpoints ?? 0} / ${stats?.schedules ?? 0}`"
          accent="muted"
          hint="监控目录 / 定时任务"
        />
      </div>

      <!-- 最近任务 -->
      <div class="card">
        <div class="row-between card-title">
          <span>最近 10 条任务</span>
          <button class="btn btn--sm btn--ghost" @click="router.push('/jobs')">查看全部 →</button>
        </div>
        <EmptyState v-if="recent.length === 0" text="暂无任务记录" hint="扫描监控目录或等待定时任务触发后将在此显示" />
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
</template>

<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-4);
}
</style>

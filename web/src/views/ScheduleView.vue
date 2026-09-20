<script setup lang="ts">
import { onMounted, ref } from 'vue'
import DataTable from '../components/DataTable.vue'
import Toggle from '../components/Toggle.vue'
import Modal from '../components/Modal.vue'
import EmptyState from '../components/EmptyState.vue'
import { listSchedules, createSchedule, updateSchedule, deleteSchedule, runSchedule } from '../api/schedules'
import { listWatchpoints } from '../api/watchpoints'
import { useToast } from '../composables/useToast'
import { formatDateTime } from '../composables/useFormat'
import type { Schedule, WatchPoint } from '../api/types'

const toast = useToast()

interface FormState {
  name: string
  cron: string
  enabled: boolean
  watchpointIds: string[]
}
const emptyForm = (): FormState => ({ name: '', cron: '0 3 * * *', enabled: true, watchpointIds: [] })

// cron 常用预设：点选直接填入输入框
const PRESETS: { label: string; cron: string }[] = [
  { label: '每小时', cron: '0 * * * *' },
  { label: '每天凌晨 3 点', cron: '0 3 * * *' },
  { label: '每周一 9 点', cron: '0 9 * * 1' },
  { label: '每 6 小时', cron: '0 */6 * * *' },
]

const list = ref<Schedule[]>([])
const watchpoints = ref<WatchPoint[]>([])
const loading = ref(true)

const formOpen = ref(false)
const editingId = ref<string | null>(null)
const form = ref<FormState>(emptyForm())
const saving = ref(false)

const deleteTarget = ref<Schedule | null>(null)
const runningId = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  try {
    const [schedules, wps] = await Promise.all([listSchedules(), listWatchpoints()])
    list.value = schedules
    watchpoints.value = wps
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载定时任务失败')
  } finally {
    loading.value = false
  }
}

function openAdd(): void {
  editingId.value = null
  form.value = emptyForm()
  formOpen.value = true
}

function openEdit(s: Schedule): void {
  editingId.value = s.id
  form.value = {
    name: s.name,
    cron: s.cron,
    enabled: s.enabled,
    watchpointIds: [...s.watchpointIds],
  }
  formOpen.value = true
}

function applyPreset(cron: string): void {
  form.value.cron = cron
}

async function save(): Promise<void> {
  if (!form.value.name.trim()) {
    toast.error('请填写任务名称')
    return
  }
  if (!form.value.cron.trim()) {
    toast.error('请填写 cron 表达式')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await updateSchedule(editingId.value, {
        name: form.value.name,
        cron: form.value.cron,
        enabled: form.value.enabled,
        watchpointIds: form.value.watchpointIds,
      })
      toast.success('已保存定时任务')
    } else {
      await createSchedule({
        name: form.value.name,
        cron: form.value.cron,
        enabled: form.value.enabled,
        watchpointIds: form.value.watchpointIds,
      })
      toast.success(`已创建定时任务：${form.value.name}`)
    }
    formOpen.value = false
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(s: Schedule): Promise<void> {
  try {
    await updateSchedule(s.id, { enabled: !s.enabled })
    s.enabled = !s.enabled
    toast.success(s.enabled ? '已启用该定时任务' : '已停用该定时任务')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '更新失败')
  }
}

async function runOne(s: Schedule): Promise<void> {
  runningId.value = s.id
  try {
    await runSchedule(s.id)
    toast.success(`已触发定时任务「${s.name}」，请到任务队列查看进度`)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '执行失败')
  } finally {
    runningId.value = null
  }
}

function askDelete(s: Schedule): void {
  deleteTarget.value = s
}

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return
  try {
    await deleteSchedule(deleteTarget.value.id)
    toast.success(`已删除定时任务：${deleteTarget.value.name}`)
    deleteTarget.value = null
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '删除失败')
  }
}

function wpLabel(id: string): string {
  return watchpoints.value.find((w) => w.id === id)?.path ?? id
}

onMounted(load)

const columns = [
  { key: 'name', label: '名称' },
  { key: 'cron', label: 'Cron / 说明', width: '240px' },
  { key: 'next', label: '下次执行', width: '170px' },
  { key: 'scope', label: '生效目录' },
  { key: 'enabled', label: '启用', width: '80px' },
  { key: 'ops', label: '操作', width: '210px' },
]
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">定时任务</h1>
        <div class="page-subtitle">按 cron 周期性扫描监控目录并自动切分</div>
      </div>
      <button class="btn btn--primary" @click="openAdd">+ 新增定时任务</button>
    </div>

    <div class="card">
      <div v-if="loading" class="card-loading"><span class="spinner" /> 加载中…</div>
      <EmptyState
        v-else-if="list.length === 0"
        text="还没有定时任务"
        hint="点击右上角「新增定时任务」，设置周期与生效目录"
      />
      <DataTable v-else :columns="columns">
        <tr v-for="s in list" :key="s.id">
          <td class="text-ellipsis" :title="s.name">{{ s.name }}</td>
          <td>
            <div class="mono">{{ s.cron }}</div>
            <div class="faint">{{ s.cronText }}</div>
          </td>
          <td class="faint">{{ formatDateTime(s.nextRunAt) }}</td>
          <td class="text-ellipsis">
            <span v-if="s.watchpointIds.length === 0">全部启用目录</span>
            <span v-else>{{ s.watchpointIds.map(wpLabel).join('、') }}</span>
          </td>
          <td><Toggle :model-value="s.enabled" @update:model-value="() => toggleEnabled(s)" /></td>
          <td>
            <div class="row">
              <button class="btn btn--sm" :disabled="runningId === s.id" @click="runOne(s)">
                {{ runningId === s.id ? '执行中…' : '立即执行' }}
              </button>
              <button class="btn btn--sm" @click="openEdit(s)">编辑</button>
              <button class="btn btn--sm btn--danger" @click="askDelete(s)">删除</button>
            </div>
          </td>
        </tr>
      </DataTable>
    </div>

    <!-- 新增 / 编辑 -->
    <Modal v-model="formOpen" :title="editingId ? '编辑定时任务' : '新增定时任务'">
      <div class="field">
        <label class="field-label">任务名称</label>
        <input v-model="form.name" class="input" placeholder="如：每天凌晨整理相机视频" />
      </div>
      <div class="field">
        <label class="field-label">Cron 表达式（分 时 日 月 周）</label>
        <input v-model="form.cron" class="input mono" placeholder="0 3 * * *" />
        <div class="preset-row">
          <span class="field-hint">常用预设：</span>
          <button
            v-for="p in PRESETS"
            :key="p.cron"
            type="button"
            class="btn btn--sm"
            :class="{ 'btn--primary': form.cron === p.cron }"
            @click="applyPreset(p.cron)"
          >
            {{ p.label }}
          </button>
        </div>
      </div>
      <div class="field">
        <label class="field-label">生效的监控目录</label>
        <div class="wp-select">
          <label class="wp-option">
            <input type="checkbox" :checked="form.watchpointIds.length === 0" @change="form.watchpointIds = []" />
            全部启用目录
          </label>
          <label v-for="w in watchpoints" :key="w.id" class="wp-option">
            <input
              type="checkbox"
              :value="w.id"
              :checked="form.watchpointIds.includes(w.id)"
              @change="
                (e) => {
                  const t = e.target as HTMLInputElement
                  form.watchpointIds = t.checked
                    ? [...form.watchpointIds, w.id]
                    : form.watchpointIds.filter((id) => id !== w.id)
                }
              "
            />
            {{ w.path }}
          </label>
          <div v-if="watchpoints.length === 0" class="field-hint">暂无监控目录，将作用于「全部启用目录」</div>
        </div>
      </div>
      <div class="field">
        <label class="field-label">启用</label>
        <Toggle v-model="form.enabled" />
      </div>

      <template #footer>
        <button class="btn" @click="formOpen = false">取消</button>
        <button class="btn btn--primary" :disabled="saving" @click="save">
          <span v-if="saving" class="spinner" /> 保存
        </button>
      </template>
    </Modal>

    <!-- 删除确认 -->
    <Modal :model-value="deleteTarget !== null" title="删除定时任务" @update:model-value="(v) => { if (!v) deleteTarget = null }">
      <p>确定要删除定时任务 <strong>{{ deleteTarget?.name }}</strong> 吗？此操作无法撤销。</p>
      <template #footer>
        <button class="btn" @click="deleteTarget = null">取消</button>
        <button class="btn btn--danger" @click="confirmDelete">删除</button>
      </template>
    </Modal>
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
.mono {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
}
.preset-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-2);
}
.wp-select {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  padding: var(--space-3);
  max-height: 200px;
  overflow-y: auto;
}
.wp-option {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-sm);
  cursor: pointer;
}
</style>

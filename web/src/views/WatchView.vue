<script setup lang="ts">
import { onMounted, ref } from 'vue'
import DataTable from '../components/DataTable.vue'
import Toggle from '../components/Toggle.vue'
import Modal from '../components/Modal.vue'
import EmptyState from '../components/EmptyState.vue'
import DirPicker from '../components/DirPicker.vue'
import { listWatchpoints, createWatchpoint, updateWatchpoint, deleteWatchpoint, scanWatchpoint } from '../api/watchpoints'
import { getSettings } from '../api/settings'
import { useToast } from '../composables/useToast'
import { formatDateTime } from '../composables/useFormat'
import type { WatchPoint, WatchPointCreate, Settings } from '../api/types'

const toast = useToast()

interface FormState {
  path: string
  recursive: boolean
  note: string
}
const emptyForm = (): FormState => ({ path: '', recursive: true, note: '' })

const list = ref<WatchPoint[]>([])
const loading = ref(true)
const allowedRoots = ref<string[]>([])

const formOpen = ref(false)
const editingId = ref<string | null>(null)
const form = ref<FormState>(emptyForm())
const saving = ref(false)
const pickerOpen = ref(false)

const deleteTarget = ref<WatchPoint | null>(null)

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
  form.value = { path: wp.path, recursive: wp.recursive, note: wp.note }
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
      await updateWatchpoint(editingId.value, {
        recursive: form.value.recursive,
        note: form.value.note,
      })
      toast.success('已保存监控目录设置')
    } else {
      const body: WatchPointCreate = {
        path: form.value.path,
        recursive: form.value.recursive,
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

async function toggleRecursive(wp: WatchPoint): Promise<void> {
  try {
    await updateWatchpoint(wp.id, { recursive: !wp.recursive })
    wp.recursive = !wp.recursive
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '更新失败')
  }
}

async function toggleEnabled(wp: WatchPoint): Promise<void> {
  try {
    await updateWatchpoint(wp.id, { enabled: !wp.enabled })
    wp.enabled = !wp.enabled
    toast.success(wp.enabled ? '已启用该监控目录' : '已停用该监控目录')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '更新失败')
  }
}

async function scanOne(wp: WatchPoint): Promise<void> {
  try {
    const r = await scanWatchpoint(wp.id)
    toast.success(`扫描「${wp.path}」完成：发现 ${r.found} 个视频，入队 ${r.queued} 个`)
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '扫描失败')
  }
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

onMounted(load)

const columns = [
  { key: 'path', label: '路径' },
  { key: 'recursive', label: '递归', width: '80px' },
  { key: 'enabled', label: '启用', width: '80px' },
  { key: 'lastScanAt', label: '上次扫描', width: '170px' },
  { key: 'note', label: '备注' },
  { key: 'ops', label: '操作', width: '200px' },
]
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">监控目录</h1>
        <div class="page-subtitle">
          实时或定时扫描这些目录，自动切分新落盘的大视频
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
          <td><Toggle :model-value="wp.enabled" @update:model-value="() => toggleEnabled(wp)" /></td>
          <td class="faint">{{ formatDateTime(wp.lastScanAt) }}</td>
          <td class="text-ellipsis" :title="wp.note">{{ wp.note || '—' }}</td>
          <td>
            <div class="row">
              <button class="btn btn--sm" @click="scanOne(wp)">扫描</button>
              <button class="btn btn--sm" @click="openEdit(wp)">编辑</button>
              <button class="btn btn--sm btn--danger" @click="askDelete(wp)">删除</button>
            </div>
          </td>
        </tr>
      </DataTable>
    </div>

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
      <template #footer>
        <button class="btn" @click="deleteTarget = null">取消</button>
        <button class="btn btn--danger" @click="confirmDelete">移除</button>
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
</style>

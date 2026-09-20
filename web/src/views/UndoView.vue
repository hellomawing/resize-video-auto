<script setup lang="ts">
import { computed, ref } from 'vue'
import Modal from '../components/Modal.vue'
import EmptyState from '../components/EmptyState.vue'
import DataTable from '../components/DataTable.vue'
import Toggle from '../components/Toggle.vue'
import DirPicker from '../components/DirPicker.vue'
import { undoPreview, undoApply } from '../api/undo'
import { useToast } from '../composables/useToast'
import { formatBytes } from '../composables/useFormat'
import type { UndoPreview, UndoResult } from '../api/types'

const toast = useToast()

const path = ref('')
const recursive = ref(true)
const pickerOpen = ref(false)
const previewing = ref(false)
const preview = ref<UndoPreview | null>(null)

// 勾选参与本次撤销的分组（仅 ok 的可勾选，bad 恒为禁用并默认不勾选）
const selected = ref<Set<string>>(new Set())
const applyConfirm = ref(false)
const result = ref<UndoResult | null>(null)
const applying = ref(false)

// 应用时的安全开关
const flags = ref({ deleteSlices: true, restoreOrigin: true, trash: true })

const okGroups = computed(() => preview.value?.groups.filter((g) => g.ok) ?? [])
const badGroups = computed(() => preview.value?.groups.filter((g) => !g.ok) ?? [])

function togglePick(base: string): void {
  const next = new Set(selected.value)
  if (next.has(base)) next.delete(base)
  else next.add(base)
  selected.value = next
}

function pickAllOk(): void {
  selected.value = new Set(okGroups.value.map((g) => g.base))
}

async function runPreview(): Promise<void> {
  if (!path.value) {
    toast.error('请先选择要撤销的目录')
    return
  }
  previewing.value = true
  result.value = null
  try {
    const res = await undoPreview({ path: path.value, recursive: recursive.value })
    preview.value = res
    // 默认勾选全部通过的分组
    selected.value = new Set(res.groups.filter((g) => g.ok).map((g) => g.base))
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '预览失败')
  } finally {
    previewing.value = false
  }
}

function openApply(): void {
  if (selected.value.size === 0) {
    toast.error('请至少勾选一个通过校验的分组')
    return
  }
  applyConfirm.value = true
}

async function doApply(): Promise<void> {
  if (!path.value) return
  applying.value = true
  try {
    const res = await undoApply({
      path: path.value,
      recursive: recursive.value,
      deleteSlices: flags.value.deleteSlices,
      restoreOrigin: flags.value.restoreOrigin,
      trash: flags.value.trash,
    })
    result.value = res
    applyConfirm.value = false
    // 撤销完成后重新预览，反映最新状态
    await runPreview()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '撤销失败')
  } finally {
    applying.value = false
  }
}

const columns = [
  { key: 'origin', label: '原片名' },
  { key: 'originSize', label: '原片大小', width: '120px', align: 'right' as const },
  { key: 'slices', label: '切片数 / 总和', width: '160px' },
  { key: 'check', label: '校验', width: '90px' },
  { key: 'reason', label: '判定依据' },
  { key: 'pick', label: '参与', width: '70px' },
]
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">撤销分割</h1>
        <div class="page-subtitle">
          将切片合并还原为原片并清理切片。<strong>危险操作</strong>，执行前会二次确认，校验不通过的分组绝不会动。
        </div>
      </div>
    </div>

    <!-- 目录选择 -->
    <div class="card">
      <div class="field" style="margin-bottom: 0">
        <label class="field-label">目标目录</label>
        <div class="row">
          <input v-model="path" class="input" placeholder="选择需要撤销分割的目录" />
          <button class="btn" type="button" @click="pickerOpen = true">浏览…</button>
        </div>
      </div>
      <div class="row" style="margin-top: var(--space-3)">
        <Toggle v-model="recursive" />
        <span class="field-label" style="margin: 0">递归子目录</span>
        <button class="btn btn--primary" :disabled="previewing" @click="runPreview">
          <span v-if="previewing" class="spinner" /> 预览可撤销分组
        </button>
      </div>
    </div>

    <!-- 预览结果 -->
    <div v-if="preview" class="card">
      <div class="row-between card-title">
        <span>
          共 {{ preview.groups.length }} 组：通过 {{ preview.okCount }} · 不通过 {{ preview.badCount }}
        </span>
        <button class="btn btn--sm" :disabled="okGroups.length === 0" @click="pickAllOk">全选通过项</button>
      </div>

      <EmptyState
        v-if="preview.groups.length === 0"
        text="该目录下没有可撤销的切片分组"
        hint="仅当存在「原片 + 对应切片」时才可撤销"
      />

      <template v-else>
        <DataTable :columns="columns">
          <tr v-for="g in preview.groups" :key="g.base" :class="{ 'row-bad': !g.ok }">
            <td class="text-ellipsis" :title="`${g.base}${g.suffix}`">{{ g.base }}{{ g.suffix }}</td>
            <td style="text-align: right">{{ formatBytes(g.originSize) }}</td>
            <td>
              {{ g.slices.length }} 片 / {{ formatBytes(g.sliceSum) }}
            </td>
            <td>
              <span v-if="g.ok" class="badge" style="color: var(--color-success); background: var(--color-success-soft)">通过</span>
              <span v-else class="badge" style="color: var(--color-danger); background: var(--color-danger-soft)">不通过</span>
            </td>
            <td class="text-ellipsis faint" :title="g.reason">{{ g.reason }}</td>
            <td>
              <input
                type="checkbox"
                :checked="selected.has(g.base)"
                :disabled="!g.ok"
                :title="g.ok ? '勾选参与本次撤销' : '校验不通过，已禁用'"
                @change="togglePick(g.base)"
              />
            </td>
          </tr>
        </DataTable>

        <div v-if="preview.orphans.length" class="orphans">
          发现 {{ preview.orphans.length }} 个无法配对的孤立切片（不参与撤销）：{{ preview.orphans.slice(0, 3).join('、') }}
          <span v-if="preview.orphans.length > 3"> 等</span>
        </div>

        <div class="row" style="margin-top: var(--space-4)">
          <button class="btn btn--primary" :disabled="selected.size === 0" @click="openApply">
            撤销选中分组（{{ selected.size }}）
          </button>
        </div>
      </template>
    </div>

    <!-- 结果报告 -->
    <div v-if="result" class="card">
      <h2 class="card-title">撤销结果</h2>
      <div class="result-grid">
        <div><span class="k">删除切片</span><span class="v">{{ result.deleted }}</span></div>
        <div><span class="k">恢复原片</span><span class="v">{{ result.restored }}</span></div>
        <div><span class="k">跳过</span><span class="v">{{ result.skipped }}</span></div>
        <div><span class="k">释放空间</span><span class="v">{{ formatBytes(result.freedBytes) }}</span></div>
      </div>
      <div v-if="result.problems.length" class="problems">
        <div class="field-label">问题（{{ result.problems.length }}）</div>
        <div v-for="(p, i) in result.problems" :key="i" class="problem-item">· {{ p }}</div>
      </div>
    </div>

    <DirPicker v-model="path" v-model:open="pickerOpen" />

    <!-- 执行二次确认 -->
    <Modal v-model="applyConfirm" title="确认执行撤销（危险）">
      <p>
        即将对目录 <strong>{{ path }}</strong> 执行撤销分割，操作涉及<strong>删除切片</strong>与<strong>恢复原片</strong>。
        以下选项决定具体行为：
      </p>
      <div class="field">
        <label class="field-label">删除切片文件</label>
        <Toggle v-model="flags.deleteSlices" />
      </div>
      <div class="field">
        <label class="field-label">恢复（改名回）原片</label>
        <Toggle v-model="flags.restoreOrigin" />
      </div>
      <div class="field">
        <label class="field-label">移入回收站而非直接删除</label>
        <Toggle v-model="flags.trash" />
        <div class="field-hint">建议保持开启，便于误操作时找回</div>
      </div>
      <p class="warn">校验不通过的分组将被跳过，原片改名等非破坏性动作仍会执行。</p>
      <template #footer>
        <button class="btn" @click="applyConfirm = false">取消</button>
        <button class="btn btn--danger" :disabled="applying" @click="doApply">
          <span v-if="applying" class="spinner" /> 确认撤销
        </button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.row-bad {
  background: var(--color-danger-soft);
}
.orphans {
  margin-top: var(--space-3);
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
}
.result-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}
.result-grid .k {
  display: block;
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
}
.result-grid .v {
  font-size: var(--font-size-xl);
  font-weight: 600;
}
.problems {
  margin-top: var(--space-2);
}
.problem-item {
  font-size: var(--font-size-sm);
  color: var(--color-danger);
}
.warn {
  background: var(--color-warning-soft);
  color: var(--color-warning);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
}
</style>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import Modal from '../components/Modal.vue'
import EmptyState from '../components/EmptyState.vue'
import DataTable from '../components/DataTable.vue'
import Toggle from '../components/Toggle.vue'
import { undoPreview, undoApply } from '../api/undo'
import { listWatchpoints } from '../api/watchpoints'
import { useToast } from '../composables/useToast'
import { formatBytes, formatDateTime } from '../composables/useFormat'
import type { UndoPreview, UndoResult, UndoLoneOrigin, WatchPoint } from '../api/types'

const toast = useToast()

const path = ref('')
const recursive = ref(true)
const previewing = ref(false)
const preview = ref<UndoPreview | null>(null)

// 目标目录只从「监控目录」里选，不提供自由浏览。
// 原因：fnOS 的存储池根目录 /vol1 不可枚举（权限位 000），「从根逐级点」第一级就是
// 死路，用户点进去只会看到「没有权限」然后卡住（详见 DirPicker.vue 的注释）。
// 而撤销的对象本来就只可能落在已经加进来监控的那些目录里，所以与其让人去浏览，
// 不如直接把候选列全 —— 少一步操作，也少一个踩坑的机会。
const watchpoints = ref<WatchPoint[]>([])
const loadingWatchpoints = ref(false)

async function loadWatchpoints(): Promise<void> {
  loadingWatchpoints.value = true
  try {
    const list = await listWatchpoints()
    watchpoints.value = list
    // v-model 的值若不在任何 option 里，浏览器会「显示」第一项但 model 仍是空串，
    // 于是界面上看着选好了、点预览却报「请先选择目录」。所以这里显式落一个值。
    if (!list.some((w) => w.path === path.value)) {
      path.value = list[0]?.path ?? ''
    }
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '读取监控目录失败')
  } finally {
    loadingWatchpoints.value = false
  }
}

onMounted(loadWatchpoints)

/** 下拉里显示什么：有备注就用「备注 — 路径」，否则只显示路径 */
function label(w: WatchPoint): string {
  return w.note ? `${w.note} — ${w.path}` : w.path
}

// 选中的目录（必然是某条监控目录）。
const picked = computed(() => watchpoints.value.find((w) => w.path === path.value) ?? null)

const SCAN_MODE_LABELS: Record<WatchPoint['scanMode'], string> = {
  realtime: '实时监听',
  interval: '定时扫描',
  daily: '每天定时',
  manual: '仅手动',
}

/**
 * 「撤销完会不会马上被重切」的当场提醒。
 *
 * 撤销的校验依据就是「原片已改名成 #origin」这个隐式标记；一旦把名字改回去，
 * 在扫描器眼里它就是一个还没处理过的普通视频 —— 于是撤销刚做完就会被重新切一遍
 * （真机实测 37 秒后开始），看起来像「撤销没生效」。
 *
 * 目录选择器改成监控目录下拉框之后这条提醒成了必须：候选目录全是自动扫描的，
 * 而以前还能选一个不在监控里的目录避开这件事。
 */
const rescanWarning = computed(() => {
  const mode = picked.value?.scanMode
  if (!mode || mode === 'manual') return ''
  const when = mode === 'realtime' ? '被立刻重新分割一次'
    : mode === 'interval' ? '在下一次自动扫描时被重新分割'
      : '在下一次每天定时扫描时被重新分割'
  return `该目录是「${SCAN_MODE_LABELS[mode]}」：撤销时若保留了「恢复原片名」，`
    + `这个原片会${when}。想避开的话，先在「监控目录」页把它改成「仅手动」。`
})

// 勾选参与本次撤销的分组（仅 ok 的可勾选，bad 恒为禁用并默认不勾选）
const selected = ref<Set<string>>(new Set())
const applyConfirm = ref(false)
const result = ref<UndoResult | null>(null)
const applying = ref(false)

// 应用时的安全开关
const flags = ref({ deleteSlices: true, restoreOrigin: true, trash: true })

const okGroups = computed(() => preview.value?.groups.filter((g) => g.ok) ?? [])
const badGroups = computed(() => preview.value?.groups.filter((g) => !g.ok) ?? [])

// 切片已经不在了的原片。它们撤销不了，但必须列出来——否则这些文件在界面上
// 是隐身的（扫描跳过它们，groups/orphans 里也没有它们），只能手工改文件名。
const loneOrigins = computed(() => preview.value?.originOnly ?? [])

// 恢复原片名（单独一条路，不走「撤销」那套勾选）
const restoreTarget = ref<UndoLoneOrigin | null>(null)
const restoring = ref(false)

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
    toast.error(watchpoints.value.length === 0
      ? '还没有监控目录，请先到「监控目录」页添加一个'
      : '请先选择要撤销的目录')
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

/** 恢复某个「切片已不在」的原片的名字，让它重新变回待处理的普通文件。 */
async function doRestore(): Promise<void> {
  if (!path.value || !restoreTarget.value) return
  restoring.value = true
  try {
    const res = await undoApply({
      path: path.value,
      recursive: recursive.value,
      deleteSlices: false,
      restoreOrigin: false,
      restoreOriginOnly: true,
      trash: flags.value.trash,
    })
    result.value = res
    restoreTarget.value = null
    if (res.restoredOrphans > 0) {
      toast.success(`已恢复 ${res.restoredOrphans} 个原片的名字，它现在是可以被扫描到的普通文件了`)
    } else {
      toast.error(res.problems[0] ?? '未能恢复，请检查该文件是否已被占用')
    }
    await runPreview()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '恢复失败')
  } finally {
    restoring.value = false
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
        <RouterLink class="back-link" to="/watch">← 监控目录</RouterLink>
        <h1 class="page-title">撤销分割</h1>
        <div class="page-subtitle">
          删除切片，并把原片的名字改回去。<strong>危险操作</strong>，执行前会二次确认，校验不通过的分组绝不会动。
        </div>
      </div>
    </div>

    <!-- 目录选择 -->
    <div class="card">
      <div class="field" style="margin-bottom: 0">
        <label class="field-label">目标目录</label>
        <select v-model="path" class="select" :disabled="watchpoints.length === 0 || loadingWatchpoints">
          <option v-if="watchpoints.length === 0" value="" disabled>
            还没有监控目录，请先到「监控目录」页添加
          </option>
          <option v-for="w in watchpoints" :key="w.id" :value="w.path">{{ label(w) }}</option>
        </select>
        <div class="field-hint">
          这里只列「监控目录」页里已添加的目录 —— 撤销的对象本来就只会在这些目录里。
          要处理别处的切片，先去那一页把它的目录加进来。
        </div>
        <div v-if="rescanWarning" class="field-hint field-hint--warn">{{ rescanWarning }}</div>
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
        v-if="preview.groups.length === 0 && loneOrigins.length === 0"
        text="该目录下没有可撤销的切片分组"
        hint="仅当存在「原片 + 对应切片」时才可撤销"
      />

      <template v-if="preview.groups.length">
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

      <!--
        切片已不在的原片。它们没有可撤销的内容，但必须显示出来：
        扫描会跳过它们（防止把半成品再切一遍），如果不在这里列出来，
        用户在界面上就完全看不到这些文件，只能去命令行改名。
      -->
      <div v-if="loneOrigins.length" class="lone">
        <div class="lone-title">切片已不在的原片（{{ loneOrigins.length }}）</div>
        <div class="lone-hint">
          这些文件已被分割过，但切片已经不在了，所以没有可撤销的内容。
          它们现在不会出现在扫描结果里——跳过是为了防止把自己切出来的半成品再切一遍。
          想让它们重新参与分割，点「恢复原名」。
        </div>
        <div v-for="item in loneOrigins" :key="item.origin" class="lone-row">
          <div class="lone-name text-ellipsis" :title="item.name">{{ item.name }}</div>
          <div class="lone-meta">
            {{ formatBytes(item.size) }}<span v-if="item.mtime"> · {{ formatDateTime(item.mtime) }}</span>
          </div>
          <button class="btn btn--sm" @click="restoreTarget = item">恢复原名</button>
        </div>
      </div>
    </div>

    <!-- 结果报告 -->
    <div v-if="result" class="card">
      <h2 class="card-title">执行结果</h2>
      <div class="result-grid">
        <div><span class="k">删除切片</span><span class="v">{{ result.deleted }}</span></div>
        <div><span class="k">恢复原片</span><span class="v">{{ result.restored }}</span></div>
        <div v-if="result.restoredOrphans">
          <span class="k">恢复无切片原片</span><span class="v">{{ result.restoredOrphans }}</span>
        </div>
        <div><span class="k">跳过</span><span class="v">{{ result.skipped }}</span></div>
        <div><span class="k">释放空间</span><span class="v">{{ formatBytes(result.freedBytes) }}</span></div>
      </div>
      <div v-if="result.problems.length" class="problems">
        <div class="field-label">问题（{{ result.problems.length }}）</div>
        <div v-for="(p, i) in result.problems" :key="i" class="problem-item">· {{ p }}</div>
      </div>
    </div>

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

    <!-- 恢复原片名的二次确认 -->
    <Modal :model-value="restoreTarget !== null" title="确认恢复原片名？" @update:model-value="restoreTarget = null">
      <template v-if="restoreTarget">
        <p>
          即将把 <strong>{{ restoreTarget.name }}</strong> 改回
          <strong>{{ restoreTarget.base }}{{ restoreTarget.suffix }}</strong>（{{ formatBytes(restoreTarget.size) }}）。
        </p>
        <p class="warn">
          恢复原名后，它就不再是「已处理」状态了。如果你的监控目录设的是「实时监听」，
          它会被<strong>立刻重新分割一次</strong>；设成「仅手动」则要你自己点扫描。
          改名本身可逆，随时可以再改回去。
        </p>
        <p v-if="loneOrigins.length > 1" class="hint-line">
          注意：该目录下共有 {{ loneOrigins.length }} 个这类原片，本次会全部恢复原名。
        </p>
      </template>
      <template #footer>
        <button class="btn" @click="restoreTarget = null">取消</button>
        <button class="btn btn--primary" :disabled="restoring" @click="doRestore">
          <span v-if="restoring" class="spinner" /> 确认恢复
        </button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.back-link {
  display: inline-block;
  margin-bottom: var(--space-2);
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
}
.back-link:hover {
  color: var(--color-primary);
}
.row-bad {
  background: var(--color-danger-soft);
}
.orphans {
  margin-top: var(--space-3);
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
}
.lone {
  margin-top: var(--space-5);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}
.lone-title {
  font-size: var(--font-size);
  font-weight: 500;
  margin-bottom: var(--space-2);
}
.lone-hint {
  font-size: var(--font-size-xs);
  color: var(--color-text-soft);
  line-height: 1.7;
  margin-bottom: var(--space-3);
}
.lone-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border);
}
.lone-row:last-child {
  border-bottom: none;
}
.lone-name {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-sm);
}
.lone-meta {
  font-size: var(--font-size-xs);
  color: var(--color-text-faint);
  white-space: nowrap;
}
.result-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
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
.hint-line {
  font-size: var(--font-size-xs);
  color: var(--color-text-soft);
}
</style>

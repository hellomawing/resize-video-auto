<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DirPicker from '../components/DirPicker.vue'
import FilterRulesEditor from '../components/FilterRulesEditor.vue'
import ScanModePicker from '../components/ScanModePicker.vue'
import MarkSourcePicker from '../components/MarkSourcePicker.vue'
import Toggle from '../components/Toggle.vue'
import { createWatchpoint, listWatchpoints, updateWatchpoint } from '../api/watchpoints'
import { getSettings } from '../api/settings'
import { useToast } from '../composables/useToast'
import { describeMark } from '../composables/useMarkSource'
import type {
  MarkValue,
  ScanMode,
  WatchFilters,
  WatchPoint,
  WatchPointCreate,
} from '../api/types'

// 「新增 / 编辑监控目录」是一个**独立页面**而不是弹窗。
//
// 改成页面的原因是内容变多了：除了路径、扫描方式、原片处理，现在还多了一整块
// 过滤规则（只看/不看某些类型、只看/不看某些名字）。塞进弹窗要么被压得看不清，
// 要么长到需要内部滚动 —— 而「最后会监控哪个路径」这类关键信息一滚就看不见了。
//
// 路径选择仍然复用 DirPicker（行内三步走）；编辑时路径只读展示：后端也不支持
// 改路径（改路径等于换一个目录，新建更清楚）。
const route = useRoute()
const router = useRouter()
const toast = useToast()

const editingId = computed(() => String(route.params.id || ''))
const isEdit = computed(() => !!editingId.value)

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
  filters: WatchFilters
}

/** 每次都要给一份全新的对象：规则数组不能被两个表单共享引用 */
const emptyFilters = (): WatchFilters => ({
  extInclude: [],
  extExclude: [],
  nameInclude: [],
  nameExclude: [],
})

const emptyForm = (): FormState => ({
  path: '',
  recursive: true,
  scanMode: 'realtime',
  scanIntervalHours: 6,
  scanTime: '03:00',
  markSource: '',
  sourceDir: '',
  note: '',
  filters: emptyFilters(),
})

const form = ref<FormState>(emptyForm())
const loading = ref(true)
const saving = ref(false)
const rulesRef = ref<InstanceType<typeof FilterRulesEditor> | null>(null)

/** 系统设置里已启用的格式：过滤规则里可选的文件类型只从这里出 */
const availableExts = ref<string[]>([])
const systemMark = ref<MarkValue>('')
const systemSourceDir = ref('')
const followDetail = computed(() =>
  systemMark.value ? describeMark(systemMark.value, systemSourceDir.value) : '',
)

/** 当前配了几条规则 —— 空规则 = 不过滤，得让人一眼看出来 */
const ruleCount = computed(() => {
  const f = form.value.filters
  return (
    f.extInclude.length +
    f.extExclude.length +
    f.nameInclude.filter((r) => r.value.trim()).length +
    f.nameExclude.filter((r) => r.value.trim()).length
  )
})

function fill(wp: WatchPoint): void {
  form.value = {
    path: wp.path,
    recursive: wp.recursive,
    scanMode: wp.scanMode,
    scanIntervalHours: wp.scanIntervalHours,
    scanTime: wp.scanTime,
    markSource: wp.markSource,
    sourceDir: wp.sourceDir,
    note: wp.note,
    // 后端可能补过字段，这里兜一次底，别让老数据把页面搞崩
    filters: { ...emptyFilters(), ...(wp.filters || {}) },
  }
}

onMounted(async () => {
  loading.value = true
  try {
    const settings = await getSettings()
    availableExts.value = settings.split.ext
    systemMark.value = settings.split.markSource
    systemSourceDir.value = settings.split.sourceDir
    if (isEdit.value) {
      const list = await listWatchpoints()
      const wp = list.find((w) => w.id === editingId.value)
      if (!wp) {
        toast.error('这个监控目录已经不存在了')
        await router.push('/watch')
        return
      }
      fill(wp)
    }
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
})

function modeHint(mode: ScanMode): string {
  if (mode === 'realtime') return '文件落进目录就会被发现并自动切分'
  if (mode === 'interval') return '按固定间隔扫一次，适合边传边等的场景'
  if (mode === 'daily') return '每天固定时间扫一次，适合夜间批处理'
  return '不自动扫描，只在你点「扫描」时才处理'
}

async function save(): Promise<void> {
  // 正则写错了先拦下来：后端也会 400，但让用户在页面上当场看到是哪一条更省事
  const bad = rulesRef.value?.invalidRule
  if (bad) {
    toast.error(`正则写错了，请先修正：${bad}`)
    return
  }
  if (!isEdit.value && !form.value.path) {
    toast.error('请先选择监控目录')
    return
  }
  saving.value = true
  try {
    const payload = {
      recursive: form.value.recursive,
      scanMode: form.value.scanMode,
      scanIntervalHours: form.value.scanIntervalHours,
      scanTime: form.value.scanTime,
      markSource: form.value.markSource,
      sourceDir: form.value.sourceDir,
      note: form.value.note,
      filters: form.value.filters,
    }
    if (isEdit.value) {
      await updateWatchpoint(editingId.value, payload)
      toast.success('已保存监控目录设置')
    } else {
      const body: WatchPointCreate = { path: form.value.path, ...payload }
      await createWatchpoint(body)
      toast.success(`已添加监控目录：${form.value.path}`)
    }
    await router.push('/watch')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">{{ isEdit ? '编辑监控目录' : '新增监控目录' }}</h1>
        <div class="page-subtitle">
          {{ isEdit
            ? '路径不可修改；改路径等于换一个目录，直接新增更清楚'
            : '选择要自动切分的文件夹，再决定这个目录里「处理哪些、不处理哪些」' }}
        </div>
      </div>
      <button class="btn" @click="router.push('/watch')">← 返回监控目录</button>
    </div>

    <div v-if="loading" class="card card-loading"><span class="spinner" /> 加载中…</div>

    <template v-else>
      <div class="card">
        <div class="card-title">基本设置</div>

        <div class="field">
          <label class="field-label">目录路径</label>
          <!-- 新增：行内三步走（选存储位置 → 选使用方式 → 需要时挑子文件夹），
               选完由组件自己回填。编辑：路径不可改，只如实展示 -->
          <div v-if="isEdit" class="picked-path">
            <span class="picked-icon">📁</span>
            <span class="picked-text">{{ form.path }}</span>
          </div>
          <DirPicker v-else v-model="form.path" :active="true" />
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
      </div>

      <div class="card">
        <div class="card-title">
          过滤规则
          <span class="faint">
            · {{ ruleCount ? `已配置 ${ruleCount} 条` : '未配置，目录里的视频全部处理' }}
          </span>
        </div>
        <div class="card-intro">
          只影响这个目录下「哪些文件会被切分」，与系统设置里的扩展名互相独立：
          两边都通过的文件才会被处理。被规则挡下的文件不会消失，会出现在扫描结果的
          「跳过明细」里。
        </div>
        <FilterRulesEditor
          ref="rulesRef"
          v-model="form.filters"
          :available-exts="availableExts"
          :base-path="form.path"
        />
      </div>

      <!-- 底部固定操作栏：规则区很长，保存按钮不该被滚到看不见 -->
      <div class="form-actions">
        <span class="foot-path faint" :title="form.path">
          将监控：{{ form.path || '（尚未选择）' }}
        </span>
        <button class="btn" :disabled="saving" @click="router.push('/watch')">取消</button>
        <button class="btn btn--primary" :disabled="saving" @click="save">
          <span v-if="saving" class="spinner" /> {{ isEdit ? '保存' : '添加' }}
        </button>
      </div>
    </template>
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
/* 已选目录的展示块：编辑时路径只读，摆出来比一个填不进去的输入框有用 */
.picked-path {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-primary-soft);
}
.picked-icon {
  flex-shrink: 0;
}
.picked-text {
  flex: 1;
  min-width: 0;
  font-size: var(--font-size-sm);
  word-break: break-all;
}
.form-actions {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-4);
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
/* 长路径只许省略号截断，绝不把「取消 / 保存」顶出去 */
.foot-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
  font-size: var(--font-size-xs);
}
.form-actions .btn {
  flex-shrink: 0;
}
</style>

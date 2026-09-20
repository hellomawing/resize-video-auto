<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import Toggle from '../components/Toggle.vue'
import Modal from '../components/Modal.vue'
import TagInput from '../components/TagInput.vue'
import { getSettings, updateSettings } from '../api/settings'
import { useToast } from '../composables/useToast'
import { useWebSocket } from '../composables/useWebSocket'
import type { Settings, SplitMode, OutdirMode, MarkSource } from '../api/types'

const toast = useToast()
const ws = useWebSocket()

const loading = ref(true)
const saving = ref(false)
// 初始为空对象（类型断言），加载完成前由 loading 遮罩拦截访问，避免模板里到处判空
const form = ref<Settings>({} as Settings)
// markSource 选到 delete 时，提交前必须二次确认
const confirmDeleteSource = ref(false)

const MODE_OPTIONS: { value: SplitMode; label: string }[] = [
  { value: 'auto', label: '自动（优先 copy，否则字节切）' },
  { value: 'copy', label: '流拷贝（不重新编码）' },
  { value: 'bytes', label: '纯字节切割' },
]
const MARK_OPTIONS: { value: MarkSource; label: string }[] = [
  { value: 'rename', label: '重命名源文件' },
  { value: 'move', label: '移动到子目录' },
  { value: 'none', label: '不处理源文件' },
  { value: 'delete', label: '删除源文件（危险）' },
]
const OUTDIR_OPTIONS: { value: OutdirMode; label: string }[] = [
  { value: 'same', label: '与源文件同目录' },
  { value: 'custom', label: '自定义输出目录' },
]

async function load(): Promise<void> {
  loading.value = true
  try {
    form.value = await getSettings()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载设置失败')
  } finally {
    loading.value = false
  }
}

// 后端广播 settings.updated 时刷新（其它端修改后保持一致）
function onWsMessage(msg: { type: string }): void {
  if (msg.type === 'settings.updated') void load()
}
let unsub: (() => void) | null = null

onMounted(() => {
  void load()
  unsub = ws.on(onWsMessage)
})

onUnmounted(() => unsub?.())

async function doSave(): Promise<void> {
  if (!form.value) return
  saving.value = true
  try {
    const saved = await updateSettings(form.value)
    form.value = saved
    toast.success('设置已保存，服务已应用新配置')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

function save(): void {
  if (!form.value) return
  // 删除源文件是破坏性操作，提交前强制二次确认
  if (form.value.split.markSource === 'delete') {
    confirmDeleteSource.value = true
    return
  }
  void doSave()
}
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">设置</h1>
        <div class="page-subtitle">切分参数、监控参数与服务的全局配置</div>
      </div>
      <button class="btn btn--primary" :disabled="saving || !form" @click="save">
        <span v-if="saving" class="spinner" /> 保存设置
      </button>
    </div>

    <div v-if="loading" class="card"><span class="spinner" /> 加载中…</div>

    <template v-else>
      <!-- 切分参数 -->
      <div class="card">
        <h2 class="card-title">切分参数</h2>
        <div class="field">
          <label class="field-label">切分模式</label>
          <select v-model="form.split.mode" class="select">
            <option v-for="o in MODE_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </div>

        <div class="field">
          <label class="field-label">按大小切 / 按时间切</label>
          <Toggle v-model="form.split.bySize" />
          <div class="field-hint">开启：按单个文件大小切分；关闭：按每段时长（秒）切分</div>
        </div>

        <div v-if="form.split.bySize" class="field">
          <label class="field-label">单段大小</label>
          <input v-model="form.split.size" class="input" placeholder="如 3.9G" />
          <div class="field-hint">支持单位：B / K / M / G（如 3.9G）</div>
        </div>
        <div v-else class="field">
          <label class="field-label">单段时长（秒）</label>
          <input v-model.number="form.split.seconds" type="number" min="1" class="input" />
        </div>

        <div class="field">
          <label class="field-label">不筛大小，全部切</label>
          <Toggle v-model="form.split.all" />
          <div class="field-hint">开启后忽略大小阈值，所有匹配视频都切分</div>
        </div>

        <div class="field">
          <label class="field-label">监控文件扩展名</label>
          <TagInput v-model="form.split.ext" placeholder="输入扩展名后回车，如 .mp4" />
        </div>

        <div class="field">
          <label class="field-label">递归扫描子目录</label>
          <Toggle v-model="form.split.recursive" />
        </div>

        <div class="field">
          <label class="field-label">输出目录</label>
          <select v-model="form.split.outdirMode" class="select">
            <option v-for="o in OUTDIR_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
          <input
            v-if="form.split.outdirMode === 'custom'"
            v-model="form.split.outdir"
            class="input"
            placeholder="自定义输出目录绝对路径"
          />
        </div>

        <div class="field">
          <label class="field-label">源文件处理方式</label>
          <select v-model="form.split.markSource" class="select">
            <option v-for="o in MARK_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
          <div v-if="form.split.markSource === 'move'" class="field">
            <label class="field-label">移动到的子目录名</label>
            <input v-model="form.split.sourceDir" class="input" placeholder="如 origin" />
          </div>
        </div>

        <div class="field">
          <label class="field-label">保留元数据</label>
          <Toggle v-model="form.split.keepMetadata" />
        </div>
        <div class="field">
          <label class="field-label">覆盖已存在的输出</label>
          <Toggle v-model="form.split.overwrite" />
        </div>
      </div>

      <!-- 监控参数 -->
      <div class="card">
        <h2 class="card-title">监控参数</h2>
        <div class="field">
          <label class="field-label">实时监听（inotify）</label>
          <Toggle v-model="form.watch.realtime" />
          <div class="field-hint">网络共享目录会自动退化为轮询</div>
        </div>
        <div class="field">
          <label class="field-label">轮询间隔（秒）</label>
          <input v-model.number="form.watch.pollInterval" type="number" min="1" class="input" />
        </div>
        <div class="field">
          <label class="field-label">文件稳定检测（秒）</label>
          <input v-model.number="form.watch.settleSeconds" type="number" min="0" class="input" />
          <div class="field-hint">大小与修改时间连续这么多秒不变才入队，避免切到半成品</div>
        </div>
        <div class="field">
          <label class="field-label">最小文件大小（忽略更小的）</label>
          <input v-model="form.watch.minSize" class="input" placeholder="如 0 或 100M" />
        </div>
        <div class="field">
          <label class="field-label">忽略的后缀</label>
          <TagInput v-model="form.watch.ignoreSuffixes" placeholder="输入后缀后回车，如 .tmp" />
        </div>
        <div class="field">
          <label class="field-label">可访问根目录白名单</label>
          <TagInput v-model="form.watch.allowedRoots" placeholder="输入根目录后回车，如 /vol1" />
          <div class="field-hint">约束目录浏览与监控目录添加，超出范围的路径会被后端拒绝</div>
        </div>
      </div>

      <!-- 服务参数 -->
      <div class="card">
        <h2 class="card-title">服务参数</h2>
        <div class="field">
          <label class="field-label">监听地址</label>
          <input v-model="form.server.host" class="input" />
        </div>
        <div class="field">
          <label class="field-label">监听端口</label>
          <input v-model.number="form.server.port" type="number" min="1" max="65535" class="input" />
        </div>
        <div class="field">
          <label class="field-label">任务日志保留行数</label>
          <input v-model.number="form.server.jobLogLines" type="number" min="1" class="input" />
        </div>
      </div>
    </template>

    <!-- 删除源文件二次确认 -->
    <Modal v-model="confirmDeleteSource" title="危险操作确认">
      <p>
        你选择了「删除源文件」。切分完成后，<strong>原始视频将被永久删除且无法恢复</strong>。
        请确认你确实希望如此，并建议已开启输出目录与源文件分离。
      </p>
      <template #footer>
        <button class="btn" @click="confirmDeleteSource = false">我再想想</button>
        <button class="btn btn--danger" @click="confirmDeleteSource = false; doSave()">确认删除源文件并保存</button>
      </template>
    </Modal>
  </div>
</template>

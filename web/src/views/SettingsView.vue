<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import Toggle from '../components/Toggle.vue'
import Modal from '../components/Modal.vue'
import TagInput from '../components/TagInput.vue'
import MarkSourcePicker from '../components/MarkSourcePicker.vue'
import { getSettings, updateSettings } from '../api/settings'
import { useToast } from '../composables/useToast'
import { useWebSocket } from '../composables/useWebSocket'
import type { Settings, SplitMode, OutdirMode } from '../api/types'

const toast = useToast()
const ws = useWebSocket()

const loading = ref(true)
const saving = ref(false)
// 初始为空对象（类型断言），加载完成前由 loading 遮罩拦截访问，避免模板里到处判空
const form = ref<Settings>({} as Settings)
// markSource 选到 delete 时，提交前必须二次确认
const confirmDeleteSource = ref(false)

// 选项文案与解释写在一起：下面几处提示都由同一份定义渲染，改文案时不会出现
// 「下拉框里的说法」和「下面的解释」对不上号。
const MODE_OPTIONS: { value: SplitMode; label: string; hint: string }[] = [
  {
    value: 'auto',
    label: '自动（推荐）',
    hint: '能用流拷贝就用流拷贝，用不了自动退回纯字节切割。拿不准就选这个。',
  },
  {
    value: 'copy',
    label: '流拷贝 · 每段都能单独播放',
    hint:
      '切点对齐关键帧，所以每段都是能独立播放的完整视频，播放器兼容性最好。' +
      '代价是切点被关键帧位置牵着走，每段实际时长会围绕目标值浮动；' +
      '源文件里 mp4 装不下的附加流（大疆的遥测数据、封面缩略图这类）会被跳过。',
  },
  {
    value: 'bytes',
    label: '纯字节切割 · 一个字节都不丢',
    hint:
      '直接把字节流切成几份，拼回来和原片完全相同，也不依赖 ffmpeg。' +
      '代价是切点可能落在帧中间，单段未必能独立播放，而且做不到「按时间切」。',
  },
]
const modeHint = computed(
  () => MODE_OPTIONS.find((o) => o.value === form.value.split?.mode)?.hint ?? '',
)

// bySize 是个布尔字段：true = 按大小切，false = 按时长切。这里用下拉框而不是开关——
// 开关关上时的含义（「按时长」）正好是标签的反义，很容易看反。
const basis = computed<'size' | 'seconds'>({
  get: () => (form.value.split?.bySize ? 'size' : 'seconds'),
  set: (value) => {
    if (form.value.split) form.value.split.bySize = value === 'size'
  },
})

// 纯字节切割只认大小、不认时长（引擎会明确忽略秒数），所以「实际按什么切」才是
// 该展示的单位：字节模式下固定按大小，免得露出一个填了也不生效的输入框。
const effectiveBasis = computed<'size' | 'seconds'>(() =>
  form.value.split?.mode === 'bytes' ? 'size' : basis.value,
)

const OUTDIR_OPTIONS: { value: OutdirMode; label: string; hint: string }[] = [
  {
    value: 'same',
    label: '与源文件同目录',
    hint: '切片就放在原片所在的文件夹里；原片本身按上面「源文件处理方式」处置。',
  },
  {
    value: 'custom',
    label: '统一放到指定目录',
    hint: '下面填绝对路径（如 /vol1/split-out），目录不存在会自动创建。留空等同于「与源文件同目录」。',
  },
]
const outdirHint = computed(
  () => OUTDIR_OPTIONS.find((o) => o.value === form.value.split?.outdirMode)?.hint ?? '',
)

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
        <div class="field-hint card-intro">
          决定「切成什么样、切哪些、切出来的放哪」。改完点右上角「保存设置」即时生效。
        </div>

        <div class="field">
          <label class="field-label">切分模式</label>
          <select v-model="form.split.mode" class="select">
            <option v-for="o in MODE_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
          <div class="field-hint">{{ modeHint }}</div>
        </div>

        <div class="field">
          <label class="field-label">每段按什么切</label>
          <select v-model="basis" class="select">
            <option value="size">按大小 —— 每段不超过设定体积</option>
            <option value="seconds">按时长 —— 每段大约设定秒数</option>
          </select>
          <div class="field-hint">
            它同时决定下面填哪一格：按大小填「单段大小」，按时长填「单段时长」。
          </div>
          <div
            v-if="form.split.mode === 'bytes' && basis === 'seconds'"
            class="field-hint field-hint--warn"
          >
            纯字节切割做不到按时长切分，这里选的时长会被忽略，实际仍按「单段大小」切。
            想要按时长，请把上面的切分模式改成「流拷贝」或「自动」。
          </div>
        </div>

        <div v-if="effectiveBasis === 'size'" class="field">
          <label class="field-label">单段大小</label>
          <input v-model="form.split.size" class="input" placeholder="如 3.9G" />
          <div class="field-hint">
            身兼两职：① 比它小的视频直接跳过、不切；② 切分时以它为每段的目标上限。
            支持 B / K / M / G，如 500M、3.9G，不区分大小写。
          </div>
        </div>
        <div v-else class="field">
          <label class="field-label">单段时长（秒）</label>
          <input v-model.number="form.split.seconds" type="number" min="1" class="input" />
          <div class="field-hint">
            每段的目标秒数。切点必须落在关键帧上，所以每段实际时长会围绕这个值浮动，通常略长一点。
          </div>
        </div>

        <div class="field">
          <label class="field-label">忽略大小，全部切分</label>
          <Toggle v-model="form.split.all" />
          <div class="field-hint">
            开启后不再要求「超过单段大小才切」，短视频也会被切。
            它只放行「大小」这一关，仍受「监控参数」里最小文件大小的限制。
          </div>
        </div>

        <div class="field">
          <label class="field-label">处理的扩展名</label>
          <TagInput v-model="form.split.ext" placeholder="输入扩展名后回车，如 .mp4" />
          <div class="field-hint">
            只处理这些后缀的文件，不区分大小写；漏写前面的点会自动补上。
            本工具自己切出来的片段、已加标记的原片、归档目录里的文件始终跳过，与此项无关。
          </div>
        </div>

        <div class="field">
          <label class="field-label">递归扫描子目录</label>
          <Toggle v-model="form.split.recursive" />
          <div class="field-hint">
            开启：连各级子文件夹一起找；关闭：只看监控目录这一层，子目录整个忽略。
          </div>
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
          <div class="field-hint">{{ outdirHint }}</div>
        </div>

        <div class="field">
          <label class="field-label">源文件处理方式（默认值）</label>
          <MarkSourcePicker
            v-model="form.split.markSource"
            v-model:source-dir="form.split.sourceDir"
            hide-follow
          />
          <div class="field-hint">
            这里定的是全局默认值，而且可以被覆盖：每个监控目录能在「监控目录」页单独设一个，
            手动扫描时还能只改这一次。生效顺序是「这一次手动指定 &gt; 该目录的设置 &gt; 这里」，
            并以任务入队那一刻为准 —— 已排队的任务不受之后改动影响。
          </div>
        </div>

        <div class="field">
          <label class="field-label">保留元数据</label>
          <Toggle v-model="form.split.keepMetadata" />
          <div class="field-hint">
            把源文件的容器信息一并写进切片：拍摄时间、相机 / 机型、定位这类标签都靠它留住。
            关闭后切片只剩基本属性。少数标签 ffmpeg 会强制改写，开着也留不住。
            只对「流拷贝」有意义；纯字节切割原样搬运字节，这一项不参与。
          </div>
        </div>
        <div class="field">
          <label class="field-label">覆盖已存在的同名切片</label>
          <Toggle v-model="form.split.overwrite" />
          <div class="field-hint" :class="{ 'field-hint--warn': form.split.overwrite }">
            关闭（推荐）：一旦发现同名切片就中止这次切分，已有文件一个都不动，留你确认。
            开启：直接覆盖同名文件，被覆盖的内容找不回来。同名切片多半是重复扫描造成的，
            但如果那个名字下正好是你自己放的文件，就一起没了。
          </div>
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

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import Toggle from '../components/Toggle.vue'
import Modal from '../components/Modal.vue'
import TagInput from '../components/TagInput.vue'
import MarkSourcePicker from '../components/MarkSourcePicker.vue'
import { getSettings, updateSettings, exportConfig, importConfig } from '../api/settings'
import { getEnv } from '../api/system'
import { useToast } from '../composables/useToast'
import { useWebSocket } from '../composables/useWebSocket'
import type { ConfigBundle, Settings, OutdirMode, EnvInfo } from '../api/types'

const toast = useToast()
const ws = useWebSocket()

const loading = ref(true)
const saving = ref(false)
// 初始为空对象（类型断言），加载完成前由 loading 遮罩拦截访问，避免模板里到处判空
const form = ref<Settings>({} as Settings)
// markSource 选到 delete 时，提交前必须二次确认
const confirmDeleteSource = ref(false)
// 容器运行环境（数据目录、uid/gid）：只读展示，取不到就不显示这一块，
// 它是「怎么部署的」的参考信息，不该影响设置页本身可用
const env = ref<EnvInfo | null>(null)

// 配置导入导出：导出直接下载 JSON；导入先读文件、弹确认框，确认后才提交
const exporting = ref(false)
const importing = ref(false)
const confirmImport = ref(false)
const pendingBundle = ref<ConfigBundle | null>(null)
// 原生 file input 的外观没法跟这套按钮统一，所以藏起来、由「导入配置」按钮触发；
// 文件名要在确认框里回显 —— 藏了 input 之后就只剩这一个地方能告诉用户选的是哪个文件
const fileRef = ref<HTMLInputElement | null>(null)
const pendingName = ref('')

// 取消导入（或导入完成后关闭）就把待导入内容和文件名一起丢掉，
// 免得下次打开确认框还挂着上一次选的文件
watch(confirmImport, (open) => {
  if (!open) {
    pendingBundle.value = null
    pendingName.value = ''
  }
})

// bySize 是个布尔字段：true = 按大小切，false = 按时长切。这里用下拉框而不是开关——
// 开关关上时的含义（「按时长」）正好是标签的反义，很容易看反。
const basis = computed<'size' | 'seconds'>({
  get: () => (form.value.split?.bySize ? 'size' : 'seconds'),
  set: (value) => {
    if (form.value.split) form.value.split.bySize = value === 'size'
  },
})

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
  void getEnv()
    .then((e) => { env.value = e })
    .catch(() => { env.value = null })
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

function fileStamp(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`
}

async function doExport(): Promise<void> {
  exporting.value = true
  try {
    const data = await exportConfig()
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
    )
    const a = document.createElement('a')
    a.href = url
    a.download = `video-splitter-config-${fileStamp()}.json`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('配置已导出（含设置、监控目录、归档目录记录）')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '导出失败')
  } finally {
    exporting.value = false
  }
}

/** 「导入配置」按钮：转去点那个藏起来的 file input，真正的处理在 onPickFile 里 */
function pickFile(): void {
  fileRef.value?.click()
}

function onPickFile(e: Event): void {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const fileName = file.name
  const reader = new FileReader()
  reader.onload = () => {
    // 先清空：否则再选同一个文件不会再触发 change
    input.value = ''
    try {
      const parsed = JSON.parse(String(reader.result)) as ConfigBundle
      if (!parsed || typeof parsed !== 'object' || !parsed.settings) {
        toast.error('这不像本工具导出的配置文件（缺少 settings 字段）')
        return
      }
      pendingBundle.value = parsed
      pendingName.value = fileName
      confirmImport.value = true
    } catch {
      toast.error('文件不是有效的 JSON，无法导入')
    }
  }
  reader.readAsText(file)
}

async function doImport(): Promise<void> {
  if (!pendingBundle.value) return
  importing.value = true
  try {
    const result = await importConfig(pendingBundle.value)
    toast.success(result.message || '导入完成')
    await load()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '导入失败')
  } finally {
    importing.value = false
    // 关掉确认框；待导入内容与文件名由上面的 watch 一并清掉
    confirmImport.value = false
  }
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
        <div class="field-hint">
          切割方式固定为 <strong>ffmpeg 无损流拷贝</strong>：只换容器、不重新编码，
          画质音质零损失，每段都是能独立播放的完整视频。切不动的文件不会被硬切，
          而是原样保留原片、记进「任务队列 → 处理失败的文件」里等你处理。
        </div>

        <div class="field">
          <label class="field-label">每段按什么切</label>
          <select v-model="basis" class="select">
            <option value="size">按大小 —— 每段不超过设定体积</option>
            <option value="seconds">按时长 —— 每段大约设定秒数</option>
          </select>
          <div class="field-hint">
            它同时决定下面填哪一格：按大小填「单段大小」，按时长填「单段时长」。
            也决定扫描门槛：按大小时只切超过「单段大小」的视频；按时长时所有视频都入队，
            仍受「监控参数 → 最小文件大小」的限制。
          </div>
        </div>

        <div v-if="basis === 'size'" class="field">
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
          <label class="field-label">处理的扩展名</label>
          <TagInput v-model="form.split.ext" placeholder="输入扩展名后回车，如 .mp4" />
          <div class="field-hint">
            只处理这些后缀的文件，不区分大小写；漏写前面的点会自动补上。
            本工具自己切出来的片段、已加标记的原片、归档目录里的文件始终跳过，与此项无关。
          </div>
          <div class="field-hint">
            可选范围受限于「能无损切分的格式」：
            <span class="mono">.mp4 .m4v .mov .mkv .webm .ts .m2ts .mts</span>。
            其它后缀（avi / wmv / flv / rmvb 等）不在其列 —— 切开它们只能重新编码（有损）
            或按字节硬劈（第 2 段起播不了），本工具两者都不做，所以它们既不会被扫描，
            填进这里也会在保存时被剔除。
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

        <!-- 只读：告诉用户「状态存在哪儿」，备份与迁移时才找得到 -->
        <div v-if="env" class="field">
          <label class="field-label">数据目录（只读）</label>
          <input class="input" :value="env.dataDir" readonly />
          <div class="field-hint">
            设置、监控目录列表、任务历史、失败清单、归档目录记录都在这个目录里。
            它由 Docker 自己管理（卷 <strong>video-splitter-data</strong>），
            不用配置、升级容器也不会丢；飞牛应用则固定在安装所在存储空间的
            <strong>@appdata/video-splitter</strong>。备份用下面的「导出配置」，
            要连任务历史一起搬才需要打包整个卷。
            当前属主为 <strong>{{ env.uid }}:{{ env.gid }}</strong>，切片在文件管理里改不动时，
            用它对照部署时填的 PUID / PGID。
          </div>
        </div>
      </div>

      <!-- 配置备份与恢复 -->
      <div class="card">
        <h2 class="card-title">配置备份与恢复</h2>
        <div class="field-hint card-intro">
          导出一份 JSON：含<strong>设置</strong>、<strong>监控目录</strong>、
          <strong>归档目录记录</strong>。换机器或重装后在另一台导入，不用重新配一遍。
          任务历史不在里面 —— 那是运行数据，要一起搬得打包整个数据卷。
        </div>
        <div class="backup-actions">
          <button class="btn" :disabled="exporting" @click="doExport">
            <span v-if="exporting" class="spinner" />
            <svg v-else class="backup-ico" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path
                d="M8 2.5v8M4.8 7.3 8 10.5l3.2-3.2M3 13.2h10"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            导出配置
          </button>

          <button class="btn" :disabled="importing" @click="pickFile">
            <svg class="backup-ico" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path
                d="M8 10.5v-8M4.8 5.7 8 2.5l3.2 3.2M3 13.2h10"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            导入配置
          </button>

          <!-- 原生 file input 的外观跟这套按钮凑不到一起，藏起来由上面的按钮点开 -->
          <input
            ref="fileRef"
            class="backup-file"
            type="file"
            accept="application/json,.json"
            @change="onPickFile"
          />
        </div>

        <div class="field-hint backup-note">
          导入会<strong>整体替换当前设置</strong>；监控目录按<strong>路径</strong>合并
          （已存在的更新成导入内容，没有的新增）；归档目录记录只增不减。
          路径不在容器已挂载目录内的监控目录会被跳过。<strong>视频文件本身不受影响。</strong>
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

    <!-- 导入配置二次确认：会替换设置，必须先说清楚再动手 -->
    <Modal v-model="confirmImport" title="确认导入配置">
      <p>将用这个文件里的配置覆盖当前设置：</p>
      <p v-if="pendingName"><code class="import-file">{{ pendingName }}</code></p>
      <p><strong>设置</strong>整体替换（切分、监控、服务参数）；<strong>监控目录</strong>按路径合并 ——
        已存在的更新成导入内容，没有的新增；<strong>归档目录记录</strong>只增不减。</p>
      <p>视频文件本身不受影响。</p>
      <template #footer>
        <button class="btn" @click="confirmImport = false">取消</button>
        <button class="btn btn--primary" :disabled="importing" @click="doImport">
          <span v-if="importing" class="spinner" /> 确认导入
        </button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
/* 备份恢复的两个操作并排成一组：各自占一行时左边空一大截，谁跟谁是一对也看不出来。
   高度仍用 .btn 的 36px，跟页面上其它按钮齐平。 */
.backup-actions {
  position: relative; /* 藏起来的 file input 相对这里定位 */
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}
.backup-actions .btn {
  min-width: 132px;
  /* 比纯白描边多一点按钮感，hover 时整块转成主色浅底 */
  background: var(--color-surface-2);
}
.backup-actions .btn:hover:not(:disabled) {
  background: var(--color-primary-soft);
}
/* 图标用 currentColor，跟着按钮文字一起变色 */
.backup-ico {
  flex-shrink: 0;
}
/* 原生 file input 长什么样由浏览器决定，跟这套按钮凑不到一起 ——
   藏起来（仍能被 JS 触发、也仍能被读屏读到），改由「导入配置」按钮点开 */
.backup-file {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  border: 0;
  overflow: hidden;
  white-space: nowrap;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
}
/* 逐条说明摆在按钮组下面，紧跟着它解释的对象 */
.backup-note {
  margin-top: var(--space-3);
}
/* 确认框里的文件名：长名字要能换行，别把对话框撑破 */
.import-file {
  display: inline-block;
  max-width: 100%;
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--color-muted-soft);
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--color-text);
  word-break: break-all;
}
</style>

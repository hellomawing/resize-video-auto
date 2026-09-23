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
// 开启「覆盖同名切片」同样是破坏性操作（被覆盖的内容找不回来）：
// 关掉不需要问，开启前必须问一次
const confirmOverwrite = ref(false)
// 改监听地址/端口会让当前访问地址失效，保存前问一次
const confirmServerChange = ref(false)
// 监听参数进入页面时的值，用来判断这次保存有没有动过它；
// 用户点过确认后置 ack，免得后面的检查再弹一遍
const loadedServer = ref<{ host: string; port: number } | null>(null)
const serverAck = ref(false)
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

// 监听地址/端口在容器里被环境变量锁死了：启动逻辑是「环境变量优先于设置」，
// 而 Dockerfile 已经写死了 VS_HOST / VS_PORT，所以这两个框改了没有任何作用。
// 取到值就置为只读并说明该去哪儿改 —— 别让人对着一个没反应的输入框反复试。
const hostLocked = computed(() => !!env.value?.hostEnv)
const portLocked = computed(() => !!env.value?.portEnv)

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await getSettings()
    form.value = data
    // 记下服务端给的监听参数，作为「这一次有没有改过」的基准
    loadedServer.value = { host: data.server.host, port: data.server.port }
    serverAck.value = false
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
    // 存下了就以新值作基准，下次保存重新比对
    loadedServer.value = { host: saved.server.host, port: saved.server.port }
    serverAck.value = false
    toast.success('设置已保存，服务已应用新配置')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

/** 监听地址/端口这一次有没有被动过（进页面时的值 vs 当前值） */
function serverTouched(): boolean {
  const base = loadedServer.value
  const now = form.value?.server
  if (!base || !now) return false
  return base.host !== now.host || base.port !== now.port
}

function save(): void {
  if (!form.value) return
  // 先问最要紧的：改监听参数会让当前地址失效，其次才是不可逆的数据操作
  if (!serverAck.value && serverTouched()) {
    confirmServerChange.value = true
    return
  }
  // 删除源文件是破坏性操作，提交前强制二次确认
  if (form.value.split.markSource === 'delete') {
    confirmDeleteSource.value = true
    return
  }
  void doSave()
}

/** 确认框里选「继续保存」：记下提醒过了，再走一遍剩下的检查 */
function onServerChangeOk(): void {
  serverAck.value = true
  confirmServerChange.value = false
  save()
}

/**
 * 覆盖开关不能直接 v-model：开启是不可逆的（同名文件直接被盖掉），
 * 必须先弹确认；关闭是往安全方向走，直接生效。
 */
function onOverwriteChange(value: boolean): void {
  if (!form.value?.split) return
  if (value) {
    confirmOverwrite.value = true
    return
  }
  form.value.split.overwrite = false
}

function onOverwriteOk(): void {
  if (form.value?.split) form.value.split.overwrite = true
  confirmOverwrite.value = false
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
          <span class="hl">画质音质零损失</span>，每段都能独立播放。
          切不动的文件不会被硬切，而是原样保留原片、记进「任务队列 → 处理失败的文件」里等你处理。
        </div>

        <div class="field">
          <label class="field-label">每段按什么切</label>
          <select v-model="basis" class="select">
            <option value="size">按大小 —— 每段不超过设定体积</option>
            <option value="seconds">按时长 —— 每段大约设定秒数</option>
          </select>
          <div class="field-hint">
            它同时决定下面填哪一格：<strong>按大小</strong>填「单段大小」、<strong>按时长</strong>填「单段时长」。
            也决定扫描门槛 —— 按大小时只切超过「单段大小」的视频，按时长时所有视频都入队
            （仍受「监控参数 → 最小文件大小」限制）。
          </div>
        </div>

        <div v-if="basis === 'size'" class="field">
          <label class="field-label">单段大小</label>
          <input v-model="form.split.size" class="input" placeholder="如 3.9G" />
          <div class="field-hint">
            身兼两职：① <strong>比它小的视频直接跳过</strong>、不切；② 切分时以它为<strong>每段的上限</strong>。
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
            本工具自己切出来的片段、已加标记的原片、归档目录里的文件<strong>始终跳过</strong>，与此项无关。
          </div>
          <div class="field-hint">
            可选范围限「能无损切分的格式」：
            <span class="mono">.mp4 .m4v .mov .mkv .webm .ts .m2ts .mts</span>。
            其它后缀（avi / wmv / flv / rmvb 等）只能重新编码（有损）或按字节硬劈（第 2 段起播不了），
            本工具两者都不做 —— 所以它们既不会被扫描，<span class="hl">填进来也会在保存时被剔除</span>。
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
            这是<strong>全局默认值</strong>，但可以被覆盖：每个监控目录能在「监控目录」页单独设一个，
            手动扫描时还能只改这一次。
            生效顺序是 <span class="hl">这一次手动指定 &gt; 该目录的设置 &gt; 这里</span>，
            并以<strong>任务入队那一刻</strong>为准 —— 已排队的任务不受之后改动影响。
          </div>
        </div>

        <div class="field">
          <label class="field-label">保留元数据</label>
          <Toggle v-model="form.split.keepMetadata" />
          <div class="field-hint">
            把源文件的容器信息一并写进切片：<strong>拍摄时间、相机 / 机型、定位</strong>这类标签都靠它留住。
            关闭后切片只剩基本属性。少数标签 ffmpeg 会强制改写，开着也留不住。
          </div>
        </div>
        <div class="field">
          <label class="field-label">覆盖已存在的同名切片</label>
          <Toggle :model-value="form.split.overwrite" @update:model-value="onOverwriteChange" />
          <div class="field-hint" :class="{ 'field-hint--warn': form.split.overwrite }">
            关闭（推荐）：一旦发现同名切片就<strong>中止这次切分</strong>，已有文件一个都不动，留你确认。
            开启：直接覆盖同名文件，<span class="hl hl--danger">被覆盖的内容找不回来</span>。
            同名切片多半是重复扫描造成的，但如果那个名字下正好是你自己放的文件，就一起没了。
          </div>
        </div>
      </div>

      <!-- 监控参数 -->
      <div class="card">
        <h2 class="card-title">监控参数</h2>
        <div class="field-hint card-intro">
          决定「多久发现一次新文件」与「什么样的文件才值得切」。
          筛选门槛（稳定检测、最小大小、忽略后缀）<strong>对手动点「扫描」同样生效</strong>。
        </div>

        <div class="field">
          <label class="field-label">实时监听（inotify）</label>
          <Toggle v-model="form.watch.realtime" />
          <div class="field-hint">
            开着时新文件<strong>几秒内</strong>就被发现。网络共享目录（SMB / NFS）的系统通知不可靠，
            会自动退化为轮询。
            <span class="hl">关掉不等于停止扫描</span> —— 仍按下面的间隔轮询，只是不再即时。
          </div>
        </div>
        <div class="field">
          <label class="field-label">轮询间隔（秒）</label>
          <input v-model.number="form.watch.pollInterval" type="number" min="5" max="86400" class="input" />
          <div class="field-hint">
            每隔这么久把「实时监听」模式的监控目录整个扫一遍。这一项<strong>始终生效</strong>，
            是 inotify 的兜底（网络共享上它就是唯一的发现手段）。
            范围 5 ~ 86400；定时扫描 / 仅手动的目录不受它影响。
          </div>
        </div>
        <div class="field">
          <label class="field-label">文件稳定检测（秒）</label>
          <input v-model.number="form.watch.settleSeconds" type="number" min="0" max="86400" class="input" />
          <div class="field-hint">
            大小与修改时间连续这么多秒不变才入队，避免切到还没拷完的半成品。
            填 <span class="hl">0 = 不做检测</span>：正在写入的文件会被直接切走，不太建议。
          </div>
        </div>
        <div class="field">
          <label class="field-label">最小文件大小（忽略更小的）</label>
          <input v-model="form.watch.minSize" class="input" placeholder="如 0 或 100M" />
          <div class="field-hint">
            比它小的文件不切。<strong>填 0 = 不限制</strong>。支持 B / K / M / G，如 100M。
          </div>
        </div>
        <div class="field">
          <label class="field-label">忽略的后缀</label>
          <TagInput v-model="form.watch.ignoreSuffixes" placeholder="输入后缀后回车，如 .tmp" />
          <div class="field-hint">
            这些后缀的文件<strong>永远不处理</strong>，对所有监控目录一并生效（不必逐个去配过滤规则）。
            默认挡的是下载中的临时文件：
            <span class="mono">.tmp .part .crdownload .!qb .download</span>。
          </div>
        </div>
      </div>

      <!-- 服务参数 -->
      <div class="card">
        <h2 class="card-title">服务参数</h2>
        <div class="field-hint card-intro">
          服务本身的监听与日志。<strong>这一栏改错会让控制台打不开</strong>，不确定就保持默认。
        </div>
        <div class="field">
          <label class="field-label">监听地址</label>
          <!-- 锁定态直接显示环境变量里的真实取值：设置里那份可能是历史值，跟实际对不上 -->
          <input v-if="hostLocked" class="input input--locked" :value="env?.hostEnv ?? ''" disabled />
          <input v-else v-model="form.server.host" class="input" />
          <div v-if="hostLocked" class="field-hint field-hint--warn">
            实际生效值来自部署给的 <span class="mono">VS_HOST</span>，<strong>在这里改了不会生效</strong>。
            要改得动 Docker 的 environment 后重建容器。
          </div>
          <div v-else class="field-hint">
            容器内监听哪块网卡，默认 <span class="mono">0.0.0.0</span>（全都听）。<strong>一般不用改</strong>。
          </div>
        </div>
        <div class="field">
          <label class="field-label">监听端口</label>
          <input
            v-if="portLocked"
            class="input input--locked"
            :value="env?.portEnv ?? ''"
            disabled
          />
          <input
            v-else
            v-model.number="form.server.port"
            type="number"
            min="1"
            max="65535"
            class="input"
          />
          <div v-if="portLocked" class="field-hint field-hint--warn">
            实际生效值来自部署给的 <span class="mono">VS_PORT</span>，<strong>在这里改了不会生效</strong>。
            想换访问端口就改 Docker 端口映射的<strong>宿主端口</strong>那一侧（如
            <span class="mono">18099:{{ env?.portEnv }}</span>），容器内的不用动。
          </div>
          <div v-else class="field-hint">
            容器内监听的端口。外部访问走的是 Docker 端口映射（如 <span class="mono">8099:8099</span>），
            <span class="hl">两边必须一致</span> —— 只改这里而不同步改映射，控制台就打不开了。
          </div>
        </div>
        <div class="field">
          <label class="field-label">任务日志保留行数</label>
          <input v-model.number="form.server.jobLogLines" type="number" min="100" max="100000" class="input" />
          <div class="field-hint">
            每个任务的日志最多留这么多行，超出丢掉最早的。范围 100 ~ 100000。
          </div>
        </div>

        <!-- 只读：告诉用户「状态存在哪儿」，备份与迁移时才找得到 -->
        <div v-if="env" class="field">
          <label class="field-label">数据目录（只读）</label>
          <input class="input" :value="env.dataDir" readonly />
          <div class="field-hint">
            设置、监控目录列表、任务历史、失败清单、归档目录记录都在这里。
            它由 Docker 自己管理（卷 <span class="mono">video-splitter-data</span>），
            <strong>不用配置、升级容器也不会丢</strong>；飞牛应用则固定在安装所在存储空间的
            <span class="mono">@appdata/video-splitter</span>。
            备份用下面的「导出配置」；要连任务历史一起搬，才需要打包整个卷。
            当前属主为 <span class="mono">{{ env.uid }}:{{ env.gid }}</span> ——
            切片在文件管理里改不动时，用它对照部署时填的 PUID / PGID。
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
          导入会<span class="hl">整体替换当前设置</span>；监控目录按<strong>路径</strong>合并
          （已存在的更新成导入内容，没有的新增）；归档目录记录只增不减。
          路径不在容器已挂载目录内的监控目录会被跳过。<strong>视频文件本身不受影响。</strong>
        </div>
      </div>
    </template>

    <!-- 开启「覆盖同名切片」前的二次确认：这是不可逆的，箭头必须停在「关」这一侧 -->
    <Modal v-model="confirmOverwrite" title="确认开启覆盖">
      <p>开启后，切分时遇到<strong>同名文件会直接覆盖</strong>，被覆盖的内容找不回来。</p>
      <p>
        同名切片多半是重复扫描造成的，那倒没关系；但如果那个名字下正好是你自己放进去的
        文件，就一起没了。
      </p>
      <p>
        保持关闭的话，一旦发现同名切片就<strong>中止这次切分</strong>，已有文件一个都不动，
        等你确认后再处理 —— 稳妥得多。
      </p>
      <template #footer>
        <button class="btn" @click="confirmOverwrite = false">保持关闭（推荐）</button>
        <button class="btn btn--danger" @click="onOverwriteOk">仍然开启</button>
      </template>
    </Modal>

    <!-- 改监听参数前的提醒：这一改当前网址就废了，事后才发现会很麻烦 -->
    <Modal v-model="confirmServerChange" title="监听参数已改动">
      <p>你改了服务的<strong>监听地址或端口</strong>，要重启服务才按新值监听。</p>
      <p>
        保存后<span class="hl">当前这个网址就失效了</span>，得用新地址访问控制台。
        如果跑在 Docker 里，还要把端口映射改成一致的 —— 容器内监听的端口和外部映射对不上，
        控制台就打不开。
      </p>
      <p>确认改对了再保存；改错了得进容器把配置改回来。</p>
      <template #footer>
        <button class="btn" @click="confirmServerChange = false">我再检查一下</button>
        <button class="btn btn--primary" @click="onServerChangeOk">确认，仍要保存</button>
      </template>
    </Modal>

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
/* 被部署锁死的项（监听地址 / 端口）：灰底表示改不了，但字色不能跟着淡下去 ——
   用户正是要看清「实际生效的是哪个值」 */
.input--locked:disabled {
  background: var(--color-surface-2);
  color: var(--color-text-soft);
  cursor: not-allowed;
}
</style>

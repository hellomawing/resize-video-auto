# video-splitter NAS 版 · 接口契约

前端与后端之间的**唯一约定**。字段名一律 camelCase（后端 Pydantic 用 alias 输出），
时间为 ISO 8601 字符串（本地时区，例：`2026-09-20T12:39:43+08:00`）。
所有响应体为 JSON；错误统一返回 `{ "detail": "人话描述" }` + 合适的 HTTP 状态码。

基础前缀：`/api`

---

## 1. 系统

### GET /api/health
```json
{
  "ok": true,
  "version": "1.0.0",
  "python": "3.12.7",
  "ffmpeg": { "path": "/usr/bin/ffmpeg", "version": "6.1.1", "ok": true },
  "ffprobe": { "path": "/usr/bin/ffprobe", "version": "6.1.1", "ok": true },
  "time": "2026-09-20T12:39:43+08:00",
  "uptimeSec": 1234
}
```

### GET /api/stats
```json
{
  "jobs": { "queued": 1, "running": 1, "success": 42, "failed": 3, "canceled": 0 },
  "watchpoints": 2,
  "schedules": 1,
  "todayBytes": 21474836480,
  "totalParts": 137
}
```

### GET /api/browse?path=/vol1/1000
用于前端「选择目录」（撤销页与监控页共用同一个选择器）。`path` 省略时返回可访问的根目录。
```json
{
  "path": "/vol1/1000",
  "parent": "/vol1",
  "roots": ["/vol1"],
  "dirs": [ { "name": "video-split-in", "path": "/vol1/1000/video-split-in" } ],
  "shortcuts": [
    { "name": "video-split-in", "path": "/vol1/1000/video-split-in",
      "kind": "watchpoint", "note": "相机导入目录" }
  ],
  "videoCount": 0,
  "suggestedRoots": [],
  "error": null
}
```
- 只返回目录，不下发文件列表（目录可能上万条）。
- **可访问范围完全由容器挂载决定**（没有「白名单」这个设置了）：根目录 = 自动探测
  `/proc/self/mountinfo` 得到的挂载点，排除伪文件系统、根 overlay 与 `/data` 卷 ——
  即「你在 docker-compose 的 volumes 里挂了什么，网页上就能选什么」。
  开发/测试环境没有 `/proc` 时，可用环境变量 `VS_EXTRA_ROOTS`（多个路径用系统路径分隔符）
  补几个根，正常部署不需要它。
- 传入路径不在已挂载目录内时返回 `403`（文案会提示去 Docker 里挂载它）。
- 路径不存在、或没有权限列出其子目录时返回 200，但 `error` 字段写明原因，`dirs` 为空。
- `roots` 恒为实际存在的目录（来自挂载探测，不存在的不可能出现）；
  一个都没有时返回空数组，并在 `error` 里提示把目录挂进容器。
- `shortcuts`：**常用目录**，按此顺序 —— 已添加的监控目录（`kind=watchpoint`，
  `note` 取该目录的备注）→ 最近任务出现过的目录（`job`，`outdir` 与 `src` 的父目录）
  → 系统设置里的输出目录（`setting`）。按真实路径去重，**已挂载范围之外的一律不下发**
  （点了也是 403，摆出来只会让人白跑一趟）。
- `suggestedRoots`：某个根**不可枚举**时（fnOS 把 /vol1 权限位设成 000，readdir
  被拒）自动探测到的、存在且能正常列出的数字子目录（`/volX/<uid>` 布局，探测
  范围 0~2999，最多 8 个）。前端摆成「点一下直达」入口 —— 用户不必做任何配置
  就能继续往下选。根可以正常枚举时为空数组。

> **为什么需要 `shortcuts`**：fnOS 这类系统把存储池根目录 `/vol1` 的权限位设成 `000`、
> 连一条扩展 ACL 都没有（`getfacl` 干干净净），内核直接拒绝对它 readdir ——
> 于是**「从根目录往下逐级点」这条路第一级就是死的**，用户看到「没有权限」后再也走不动。
> 但**枚举和访问是两件事**：`/vol1/1000` 及其下所有目录都能正常列出。
> 所以选择器给了一条绕过它的路：点 `shortcuts` 直达（界面上叫「常用目录」）。
>
> 选择器**不提供手动输入路径**：路径入口只保留「根下拉 + 逐级点」一种，免得用户有机会
> 敲进一条走不通的路径。要添加全新目录，把它挂载进容器（docker-compose 的 volumes）即可。
> 「撤销分割」页更彻底 —— 它直接用 `/api/watchpoints` 的监控目录做下拉框，不走本接口。

---

## 2. 设置

### GET /api/settings
### PUT /api/settings（body 为完整 Settings，返回保存后的 Settings）
```json
{
  "split": {
    "bySize": true,
    "size": "3.9G",
    "seconds": 300,
    "all": false,
    "ext": [".mp4", ".m4v", ".mov", ".mkv", ".webm", ".ts", ".m2ts", ".mts"],
    "recursive": true,
    "outdirMode": "same",
    "outdir": "",
    "markSource": "rename",
    "sourceDir": "resize-video-origin-file",
    "keepMetadata": true,
    "overwrite": false
  },
  "watch": {
    "realtime": true,
    "pollInterval": 30,
    "settleSeconds": 60,
    "minSize": "0",
    "ignoreSuffixes": [".tmp", ".part", ".crdownload", ".!qb", ".download"]
  },
  "server": {
    "host": "0.0.0.0",
    "port": 8099,
    "jobLogLines": 2000
  }
}
```
字段说明：
- **没有 `split.mode`**：切割方式固定为 ffmpeg 无损流拷贝（`-c copy`）。历史版本的
  `auto` / `copy` / `bytes` 三选一已取消 —— `bytes`（按字节硬劈）的产物从第 2 段起
  播不了，却会让原片被改名成 `#origin`，看着像切好了；`auto` 更糟，它会在 copy
  失败时**静默**降级成 bytes。旧配置里残留的 `mode` 键会被直接忽略。
- `split.bySize`：true=按大小切，false=按时间切（用 `seconds`）
- `split.all`：**派生字段，等于 `!bySize`，传入值会被忽略**。按时长切时为 true
  （所有视频入队，否则小视频永远达不到大小阈值、按时长切分形同虚设）；
  按大小切时为 false（不超过阈值的不切，否则小视频会被空忙重封装成 1 段）。
  设置页不再提供该开关。
- `split.ext`：支持处理的扩展名。**取值只能是能无损流拷贝的那 8 种**：
  `.mp4 .m4v .mov .mkv .webm .ts .m2ts .mts`（与引擎的 `SEGMENT_FRIENDLY` 单点同步）。
  其它后缀（avi / wmv / flv / mpg / 3gp / rmvb / vob）会被规范化时剔除 —— 切开它们
  只能重新编码（有损）或按字节硬劈，本工具两者都不做，因此它们既不会被扫描，
  也不会进入队列。
- `split.outdirMode`：`same`（与源文件同目录）| `custom`（用 `outdir`）
- `split.markSource`：切分成功后怎么处置原片，取值见下节。
- `split.sourceDir`：`markSource: "move"` 时用的归档子目录名（**单层目录名**，
  相对源文件所在目录，不存在会自动创建）。默认 `resize-video-origin-file`。
  写入时会做规整：斜杠、`.`、`..` 会被处理掉；历史默认名 `origin`
  会自动迁到新默认名。详见 [原片处理方式](#原片处理方式marksource)。
- `watch.realtime`：实时监听（inotify）的**全局能力开关**（网络共享目录会自动退化为轮询）。
  注意这是全局开关，某个目录要不要用实时监听由它自己的 `scanMode` 决定，两者是「与」的关系。
- `watch.settleSeconds`：文件稳定检测秒数——大小与修改时间连续这么多秒不变才入队
- `watch.minSize`：小于该大小的文件直接忽略

### GET /api/settings/export

导出配置，供备份或换机器用。**只导出配置，不导出运行数据**：

```json
{
  "version": 1,
  "exportedAt": "2026-09-22T14:30:00",
  "settings": { "split": { }, "watch": { }, "server": { } },
  "watchpoints": [ { "id": "wp_1a2b3c4d", "path": "/vol1/1000/in", "scanMode": "realtime" } ],
  "archiveDirs": ["resize-video-origin-file", "origin"]
}
```

任务历史（`video-splitter.db`）不在里面 —— 它是运行数据，换机器一般不需要搬；
要连历史一起搬请打包整个数据卷。

### POST /api/settings/import

body 就是上面那份 JSON，返回导入结果：

```json
{
  "settingsApplied": true,
  "watchpointsAdded": 1,
  "watchpointsUpdated": 2,
  "watchpointsSkipped": 0,
  "archiveDirsAdded": 1,
  "message": "导入完成：…"
}
```

三块内容的合并语义**刻意不同**：

| 部分 | 语义 | 为什么 |
|---|---|---|
| `settings` | 整体替换 | 导的就是一台机器的完整设置，逐项合并没有意义。空对象表示「不改设置」 |
| `watchpoints` | 按**路径**合并：已存在的更新成导入内容，没有的新增 | 换机器后 id 必然对不上，按 id 判重会把整份重复添加一遍。本机侧的 `id`、`createdAt`、`lastScanAt`、`videoCount` 一律保留 |
| `archiveDirs` | **只增不减** | 与运行时记名语义一致。少记一个名字就可能把归档里的原片重切一遍 |

路径不在容器已挂载目录内的监控目录会被跳过并计入 `watchpointsSkipped`，
不会让整份导入失败（换机器后挂载不同很常见）。

导入后与保存设置一样会重新装配监控与扫描计划。

---

## 3. 原片处理方式（markSource）

切分成功后原片怎么处置，共四个取值（与 `core/splitter.py` 的 `mark_source`
参数一一对应）：

| 值 | 行为 | 原片去哪 | 风险 |
|---|---|---|---|
| `rename` | 原片改名，加 `#origin` 后缀 | 留在原地 | 无。扫描器认得这个后缀，不会重切 |
| `move` | 原片移到同级的归档子目录 | `<原目录>/<sourceDir>/` | 无。归档目录名会被扫描器整体排除 |
| `none` | 不动原片 | 留在原地 | ⚠️ **下次扫描会把它当新视频再切一遍** |
| `delete` | 切分成功后删除原片 | — | ⚠️ **不可逆**，无法恢复 |

`sourceDir` 只在 `move` 下有实际意义，且必须是**单层目录名**（相对原文件所在目录，
不存在会自动创建）。写入时的规整规则：

- 只取最后一段：`origin/old` → `old`；反斜杠也当分隔符
- `.` / `..` / 空值 → 退回默认名
- 历史默认名 `origin`（不分大小写）→ 迁到新默认名 `resize-video-origin-file`

`origin` 之所以要迁走：它只是早期版本的实现细节，不是用户的刻意选择，
留着只会让老配置永远停在旧名字上。

### 三级优先级

这四选一可以在三个地方设，**前一级压过后一级**：

| 级别 | 在哪设 | 字段 | 作用范围 |
|---|---|---|---|
| 1（最高） | 手动扫描时的请求体 | `ScanIn` | **只影响这一次**入队的任务，不落盘 |
| 2 | 监控目录自己的设置 | `WatchPoint.markSource` / `.sourceDir` | 该目录扫出来的所有任务 |
| 3（兜底） | 系统设置 | `split.markSource` / `.sourceDir` | 全局默认值 |

**「跟随上级」用空串表示**，它不是一个缺省值：

- `WatchPoint.markSource` / `.sourceDir` 为 `""` 表示「跟随系统设置」。
  所以 `PUT /api/watchpoints/{id}` 传空串是一个**有意的取值**，不是「没传」；
  想恢复成跟随就传 `""`（传 `null` 表示「不改这一项」，两者含义不同）。
- `ScanIn` 里不传字段即表示跟随该目录、再退回系统设置。
- 系统设置那一层没有上级可跟随，界面上不提供「跟随」选项。

### 快照语义

解析结果在**入队那一刻**定下来，写进任务行的 `markSource` / `sourceDir`，
执行阶段直接用它，**不再回头读设置**。

理由是任务可能在队列里等很久：中途改一次设置就让排在后面的任务换一套行为，
会出现「同一个目录扫出来的两批任务处理方式不一样」这种没法解释的结果，
而且事后翻任务也看不出它当时用的是哪条规则。所以：

- **改设置只影响之后入队的任务**，已排队的不受影响。
- `POST /api/jobs/{id}/retry` 同样沿用原任务的快照 —— 重试的是同一个文件，
  用户要的是「把上次没做完的事做完」，而不是按现在的设置换一套行为。
- 新增这两个字段之前入队的老任务，快照为 `null`，重试时会安全退回当时的设置。

### 归档目录的排除

扫描器要**整体排除**归档目录名，否则被 `move` 走的原片下次扫描时会被当成
新视频再切一遍（这是防重切的关键一环）。

排除时取的是**所有候选名的集合**：系统默认名 + 各监控目录自己用的名字 +
本次手动指定的名字 + 历史默认名 `origin`（改名之前搬进去的文件还躺在那里）+
**曾经用过的名字**（`data/archive-dirs.json`，只增不减）。
之所以是集合而不是单个名字：归档目录能按监控目录分别设置，只排除一个名字的话，
别处归档走的原片就会在另一个目录里被重切。

最后那一类为什么要落盘：前几类都是**按当前配置现算**的。而 `move` 归档过去的原片
**保留原文件名**，名字一旦被改掉或清空回「跟随系统」，它就从集合里掉出去、看上去
成了个新视频，会被重切一遍再标记一次。真机复现过（清空监控目录的 `sourceDir` 后，
扫描多入队一条指向旧归档目录的任务）。方向刻意偏向多排除：多排除顶多漏扫，
漏排除会让原片和切片一起报废。副作用是集合只增不减 —— 想让某个名字放行，只能手工
编辑 `archive-dirs.json` 并重启（进程内缓存，改文件不会立刻生效）。
`collect_archive_dirs()` 会顺手把这一轮算出来的名字写进去，没有新名字时不写盘。

判断「在不在归档目录里」只看**相对于本次扫描根的路径段**：`core.splitter.is_in_archive_dir`。
所以归档目录取成 `vol1`、`1000` 这类路径上本来就有的段名也不会误伤——只有落在
扫描范围**之内**、且路径上某一层撞名的文件才算归档。唯一的例外是取成**监控目录
自己的名字**：那整个扫描范围确实都在它「里面」。

> 这一点是修过的：早期比的是绝对路径的每一段，于是归档目录一旦叫 `1000`，
> 监控目录下的文件会**全部**被判成「位于原片归档目录」，扫描永远发现 0 个视频，
> 而给的理由（位于原片归档目录）看着还挺合理，极难联想到是名字撞的。
>
> 调用侧要负责把扫描根传进去：`scanner.scan_paths` 传 `valid_dirs`，
> 实时监听那条路（`monitor._drain_pending`）按文件的监控目录 id 取根。
> 都不传时退化成「绝对路径的中间段任一段同名」——刻意偏向多排除：
> 多排除顶多漏扫，漏排除会把归档走的原片重切一遍，数据和原片一起报废。

---

## 4. 监控目录

```json
{
  "id": "wp_ab12cd34",
  "path": "/vol1/media/inbox",
  "recursive": true,
  "scanMode": "realtime",
  "scanIntervalHours": 6,
  "scanTime": "03:00",
  "markSource": "",
  "sourceDir": "",
  "note": "相机导入目录",
  "createdAt": "2026-09-20T12:00:00+08:00",
  "lastScanAt": null,
  "nextScanAt": null,
  "videoCount": 0
}
```

- `markSource` / `sourceDir`：这个目录自己的原片处理方式，**空串 = 跟随系统设置**。
  详见 [原片处理方式](#3-原片处理方式marksource)。

### 扫描方式 `scanMode`

一个字段同时回答「要不要自动扫」「多久扫一次」。**不要**再拆成
「启用 + 自动扫描」两个开关：两者会组合出「启用了但不自动扫」这种需要
停下来想一下的状态，而早期版本正是用一个 `enabled` 同时管着自动与手动，
导致用户取消勾选后连手动扫描都点不动。

| 值 | 含义 | 由谁执行 | `nextScanAt` |
|---|---|---|---|
| `realtime` | 实时监听（inotify）+ 轮询兜底，文件一落盘就切 | `monitor` 服务 | `null`（随时） |
| `interval` | 每隔 N 小时扫一次 | `scheduler` | 下次整点时间 |
| `daily` | 每天 `scanTime` 扫一次 | `scheduler` | 明天/今天的时间点 |
| `manual` | **不自动扫描**，只在点「扫描」时处理 | 无 | `null` |

- `scanIntervalHours`：**只能取 1/2/3/4/6/8/12/24**（24 的约数）。
  只有整除 24，「每 N 小时」才能从每天 0 点起均匀铺满一天，不留空档；
  换来的好处是容器重启不会打乱节奏，`nextScanAt` 也能被准确算出来。
  传入其它值会被就近取到最近的合法档位。
- `scanTime`：`"HH:MM"`，24 小时制。非法值会被纠正为 `"03:00"`。
- `nextScanAt`：**只读**，不落盘，每次查询时按当前扫描计划实时算出。
- 老版本 `watchpoints.json` 里的 `enabled` 字段会自动迁移为此字段
  （`true` → `realtime`，`false` → `manual`），存储中不再保留 `enabled`。

### 自动扫描 vs 手动扫描

两条**互相独立**的路，这一点是刻意设计的：

- **自动**扫描（`realtime` / `interval` / `daily`）由后台服务触发，受 `scanMode` 约束。
- **手动**扫描（下面两个 `scan` 接口）**完全不受 `scanMode` 限制** ——
  用户既然亲手点了按钮，就不该再被「仅手动」之类的设置拦住；
  「仅手动」约束的是自动扫描，不是手动扫描。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/watchpoints` | 列表，返回 `[WatchPoint]` |
| POST | `/api/watchpoints` | body `{path, recursive, scanMode, scanIntervalHours, scanTime, markSource, sourceDir, note}` |
| PUT | `/api/watchpoints/{id}` | 局部更新（上表除 `path`/`id` 外的字段均可） |
| DELETE | `/api/watchpoints/{id}` | 删除（想让它别再自动扫请改 `scanMode`，不必删除） |
| POST | `/api/watchpoints/{id}/scan` | **立即扫描一次**，返回 `ScanResult`（见下），可带 `ScanIn` |

`PUT` 会回读一次再返回，因此响应里的 `scanIntervalHours` / `scanTime` /
`sourceDir` 一定是纠正后的**实际生效值**，前端直接用它刷新界面即可。

### 扫描请求体 `ScanIn`

两个 `scan` 接口共用，**整体可选**（不传就等于全部跟随）：

```json
{ "markSource": "move", "sourceDir": "归档" }
```

| 字段 | 类型 | 缺省时 |
|---|---|---|
| `markSource` | `rename` \| `move` \| `none` \| `delete` | 跟随该监控目录，再退回系统设置 |
| `sourceDir` | string | 同上 |

这是「**就这一次**」的临时指定：只作用于本次扫描入队的任务，不写进任何配置文件，
也不改变监控目录或系统设置。详见 [原片处理方式](#3-原片处理方式marksource)。

### 扫描结果 `ScanResult`

```json
{
  "found": 0, "queued": 0, "skipped": 0, "waiting": 0,
  "ignored": [
    { "name": "DJI_0001#origin.MP4", "reason": "是已经切分过的原片",
      "kind": "origin", "resettable": true }
  ],
  "ignoredTotal": 1,
  "resettableTotal": 1,
  "resettableDirs": [ { "path": "/vol1/media/inbox", "recursive": true } ],
  "message": "扫描完成：没有需要处理的新视频。目录里有 1 个原片的分割结果已经不在（切片被删除或移走），可以重新分割。"
}
```

- `found`：候选视频数（**不含**被跳过的）。
- `skipped`：进了候选、又被过滤规则拒掉的数量。
- `waiting`：还在拷贝中、需要等文件稳定的数量。
- `ignored` / `ignoredTotal`：**收集阶段**就被跳过的文件（本工具的切片、`#origin`
  原片、归档目录里的）。明细最多 50 条，总数以 `ignoredTotal` 为准；
  **可重新分割的项会排在明细最前面**，免得被几十个切片名挤出视野。
- `ignored[].kind`：`slice`（切片）或 `origin`（已切分过的原片）。
- `ignored[].resettable`：**切片已不在**的原片。它挂着 `#origin` 看着像
  「已完成」，其实这次切分的结果已经没了（切片被删或搬走），恢复原名就能重切。
- `resettableTotal` / `resettableDirs`：这类原片的总数与所在目录。
  前端据此逐目录调用 `/api/undo/apply`（`restoreOriginOnly: true`）一键恢复。
- `message`：后端生成的中文总结，**前端直接展示，不要自己另拼文案**——
  它会说清「跳过了几个、为什么跳过、接下来该怎么办」，这正是早期版本只回一句
  「没有发现需要处理的视频」时最缺的信息。

> **为什么要区分 `skipped` 和 `ignored`**：两者都表现为「文件没被处理」，
> 但 `ignored` 是**刻意保护**的结果——不跳过就会把刚切出来的原片或切片再切一遍，
> 数据会废掉。把它们如实报出来，用户才分得清「真的没有视频」和「被有意跳过了」。
>
> **为什么还要再分一层 `kind` / `resettable`**：保护机制只看 `#origin` 后缀，
> 不看切片还在不在，于是把两种相反的状态混成了一句「已跳过」——
> 切片还在 = 这次切分是完整的；切片没了 = 切分结果丢了、原片可以重切。
> 后者是用户最需要知道、也最容易误判的情况，必须单独标出来。

### POST /api/scan
扫描**全部**监控目录并入队（同样不看 `scanMode`），返回 `ScanResult`（字段同上，
数值为各目录合计）。请求体可选，为 `ScanIn` —— 不传时各目录按自己的设置处理原片。

一条都没入队但确实看到了已处理的文件时，`message` 会说明原因，
而不是只回一句「0 个视频」。

> 界面目前**没有**这个接口的入口：手动扫描统一走 `POST /api/watchpoints/{id}/scan`，
> 一次针对一个目录，和「扫描方式」的设置放在同一行，不容易点错对象。
> 这个接口保留给脚本与自动化使用（`tools/verify_scanmode_api.py` 就在用它）。

---

## 5. 任务

```json
{
  "id": "job_9f8e7d6c",
  "src": "/vol1/media/inbox/DJI_0001.MP4",
  "srcName": "DJI_0001.MP4",
  "srcSize": 6442450944,
  "status": "running",
  "phase": "splitting",
  "progress": 0.42,
  "partsTotal": 3,
  "partsDone": 1,
  "mode": "copy",
  "usedMode": "copy",
  "outdir": "/vol1/media/inbox",
  "trigger": "watch",
  "watchpointId": "wp_ab12cd34",
  "markSource": "rename",
  "sourceDir": "resize-video-origin-file",
  "message": "正在切分：第 2/3 段",
  "error": null,
  "produced": [ { "name": "DJI_0001#1.MP4", "size": 2147483648 } ],
  "createdAt": "2026-09-20T12:30:00+08:00",
  "startedAt": "2026-09-20T12:30:01+08:00",
  "finishedAt": null,
  "durationSec": null
}
```
- `status`：`queued` | `running` | `success` | `failed` | `canceled` | `skipped`
- `phase`：`waiting` | `probe` | `splitting` | `verifying` | `marking` | `done`
- `trigger`：`watch` | `manual` | `schedule` | `retry`
- `mode` / `usedMode`：正常都是 `copy`。**保留这两个字段只为让历史任务仍能如实显示**
  （早期版本可能是 `auto` 或 `bytes`），它们不再是可配置项。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/jobs?status=&q=&limit=50&offset=0` | `{total, items:[Job]}`，按创建时间倒序 |
| GET | `/api/jobs/{id}` | 单个任务详情 |
| GET | `/api/jobs/{id}/log` | `{jobId, lines:[string], truncated:boolean}` |
| POST | `/api/jobs/{id}/retry` | 失败/取消的任务重新入队 |
| POST | `/api/jobs/{id}/cancel` | 取消（排队中直接置 canceled；运行中请求中止） |
| DELETE | `/api/jobs/{id}` | 删除任务记录及其日志 |
| POST | `/api/jobs/clear` | body `{statuses:["success","failed"]}` 批量清理 |

---

## 6. 处理失败的文件

切不动的文件（格式不支持、读不出时长、ffmpeg 报错、切完的段校验不过）
**一律保留原片** —— 本工具不会因为处理失败而改名、移动或删除源文件。
失败的原因记在任务上，同时也登记到这份清单里，供页面集中展示。

为什么要有一份独立的记录：自动扫描每隔几分钟就会再次看到那些文件，
没有这层记忆的话就是「入队 → 失败 → 再入队」无限循环，任务列表会被同一个
文件刷满。判断「还是同一个文件」的依据是 **大小 + 修改时间**：
两者都没变就不再入队；文件被替换或改动过（用户换片源、重新拷贝）则**自动**
重新尝试一次，不需要人工清记录。

```json
// GET /api/failures
{
  "total": 1,
  "items": [
    {
      "path": "/vol1/media/inbox/IMG_0001.mp4",
      "name": "IMG_0001.mp4",
      "size": 8192,
      "mtime": 1758470000.0,
      "reason": "无法读取视频时长",
      "jobId": "job_9f16018c",
      "at": "2026-09-22T04:10:00+08:00"
    }
  ]
}
```
- 列表按失败时间倒序；`GET` 时会顺手剔除「文件已经不在」的记录（用户自己删了
  或改名了），只留下还能动手的对象。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/failures?limit=500` | `{total, items:[Failure]}` |
| POST | `/api/failures/retry` | body `{path}`。忘掉失败记录后重新入队；这是**手动**入口，不看 `scanMode`。原片处理方式按该文件所属监控目录的设置（取最长匹配），找不到就用系统默认 |
| POST | `/api/failures/clear` | body `{paths:[...]}`，不传则清空。**只删记录，不动磁盘文件**；清掉后下次扫描会重新尝试，仍失败则再次出现在清单里 |

---

## 7. 撤销分割

### POST /api/undo/preview
body：`{ "path": "/vol1/media/inbox", "recursive": true }`
```json
{
  "path": "/vol1/media/inbox",
  "groups": [
    {
      "base": "DJI_0001", "suffix": ".MP4",
      "origin": "/vol1/media/inbox/DJI_0001#origin.MP4",
      "slices": [ { "path": "...", "name": "...", "size": 1 } ],
      "originSize": 6442450944, "sliceSum": 6442450944,
      "mode": "bytes", "ok": true, "reason": "切片字节之和与原片完全一致（纯字节切割）",
      "originDuration": 0, "sliceDurations": [], "durationSum": 0
    }
  ],
  "originOnly": [
    {
      "origin": "/vol1/media/inbox/DJI_0002#origin.MP4",
      "name": "DJI_0002#origin.MP4",
      "base": "DJI_0002", "suffix": ".MP4",
      "size": 6514105652, "mtime": "2026-09-17T11:58:11+08:00"
    }
  ],
  "orphans": ["/vol1/media/inbox/xxx#1.MP4"],
  "okCount": 1, "badCount": 0
}
```

- `originOnly`：**有 `#origin` 原片、却一个切片都没有**的文件。成因通常是切片被
  手工删掉或移走了。它们没有可撤销的内容，但必须列出来——否则这些文件在界面上
  彻底隐身（扫描会跳过它们，`groups` 和 `orphans` 里也都没有它们），
  用户只能去命令行改名才能让它们重新被处理。
- `orphans`：反过来的情况——有切片、但找不到对应的原片。
- `mode`：`copy`（时长核对通过）| `bytes`（切片字节之和与原片完全一致）| `?`（判不出来，
  即校验不通过 → 禁止删除切片）。它是**扫描时按产物形态判出来的**，与设置无关：
  新切出来的都是 copy，但目录里可能还留着早期用 bytes 切过的组，判定逻辑必须保留。

### POST /api/undo/apply

body：
```json
{ "path": "...", "recursive": true, "deleteSlices": true,
  "restoreOrigin": true, "restoreOriginOnly": false, "trash": true }
```
```json
{
  "deleted": 3, "trashed": 3, "restored": 1, "restoredOrphans": 0,
  "skipped": 0, "freedBytes": 6442450944,
  "problems": ["xxx：校验不通过…"],
  "details": [ { "base": "DJI_0001", "action": "deleted+restored", "message": "…" } ]
}
```
- `restoreOriginOnly` 默认 **false**：是否同时把上面 `originOnly` 里的原片也改回原名。
  恢复原名等于把它们变回待处理的普通文件，`realtime` 模式下会**立刻被重新分割一次**，
  所以必须由用户明确要求（界面上是单独的「恢复原名」按钮，带二次确认）。
  只想恢复原片名、不动别的，传
  `{ "deleteSlices": false, "restoreOrigin": false, "restoreOriginOnly": true }`
  —— 扫描结果里的 `resettableDirs` 正是为它准备的：前端拿到后逐目录调用本接口，
  再把扫描跑一遍，就闭环了「发现 → 恢复 → 重新分割」。
- 目标位置已有同名文件时**拒绝覆盖**，失败原因会如实写进 `problems`。

安全约定：**校验不通过的组一律不动**，原片改名也照做（改名非破坏性）。

---

## 8. WebSocket

`ws://<host>/api/ws`，服务端单向推送 JSON 文本帧。连接建立后先收到一条 `hello`。
前端断线后按 3 秒间隔重连。

```json
{ "type": "hello", "serverTime": "2026-09-20T12:39:43+08:00", "version": "1.0.0" }
{ "type": "job.created",  "job": { ...Job } }
{ "type": "job.updated",  "job": { ...Job } }
{ "type": "job.log",      "jobId": "job_x", "line": "       原大小：6.00 GB" }
{ "type": "job.progress", "jobId": "job_x", "progress": 0.42, "partsDone": 1, "partsTotal": 3, "phase": "splitting" }
{ "type": "scan.finished","watchpointId": "wp_x", "found": 12, "queued": 3 }
{ "type": "watchpoint.scan","watchpointId": "wp_x", "path": "/vol1/media/inbox" }
{ "type": "settings.updated" }
{ "type": "schedule.fired","scheduleId": "sc_x", "name": "每天凌晨 3 点" }
{ "type": "ping",         "serverTime": "2026-09-20T12:39:43+08:00" }
```

---

## 9. 前端约定

- 路由：`/`（概览）、`/watch`（监控目录）、`/jobs`（任务队列）、`/settings`（设置）。
  撤销分割是监控目录的**子页面**，路由为 `/watch/undo`（老地址 `/undo` 自动重定向过去）。
- 左侧固定导航栏 + 右侧内容区；整体浅色主题，主色 `#185FA5`。
- 所有列表为空时给出明确的空状态提示文案。
- 时间统一显示为 `YYYY-MM-DD HH:mm:ss` 本地时间；体积显示为 `6.00 GB` 这种可读格式。
- 任务状态色：成功绿 `#3B6D11`、失败红 `#A32D2D`、运行中蓝 `#185FA5`、排队灰 `#5F5E5A`。
- 不引入任何 UI 框架（Element/Ant），样式自定义，用 CSS 变量集中管理。
- 依赖只用：`vue`、`vue-router`。不引 pinia，状态用 composable 管理。

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

### GET /api/browse?path=/vol1
用于前端「选择监控目录」。`path` 省略时返回各可访问根目录。
```json
{
  "path": "/vol1",
  "parent": "/",
  "roots": ["/vol1", "/vol2"],
  "dirs": [ { "name": "media", "path": "/vol1/media" } ],
  "videoCount": 0,
  "error": null
}
```
- 只返回目录，不下发文件列表（目录可能上万条）。
- 传入路径不在白名单内时返回 `403`。
- 路径不存在或没权限时返回 200，但 `error` 字段写明原因，`dirs` 为空。

---

## 2. 设置

### GET /api/settings
### PUT /api/settings（body 为完整 Settings，返回保存后的 Settings）
```json
{
  "split": {
    "mode": "auto",
    "bySize": true,
    "size": "3.9G",
    "seconds": 300,
    "all": false,
    "ext": [".mp4", ".mov", ".mkv"],
    "recursive": true,
    "outdirMode": "same",
    "outdir": "",
    "markSource": "rename",
    "sourceDir": "origin",
    "keepMetadata": true,
    "overwrite": false
  },
  "watch": {
    "realtime": true,
    "pollInterval": 30,
    "settleSeconds": 60,
    "minSize": "0",
    "ignoreSuffixes": [".tmp", ".part", ".crdownload", ".!qb", ".download"],
    "allowedRoots": ["/vol1", "/vol2", "/vol3", "/vol4"]
  },
  "server": {
    "host": "0.0.0.0",
    "port": 8099,
    "jobLogLines": 2000
  }
}
```
字段说明：
- `split.mode`：`auto`（有 ffmpeg 用 copy，否则 bytes）| `copy` | `bytes`
- `split.bySize`：true=按大小切，false=按时间切（用 `seconds`）
- `split.all`：true=不按大小筛选，所有视频都切
- `split.outdirMode`：`same`（与源文件同目录）| `custom`（用 `outdir`）
- `split.markSource`：`rename` | `move` | `none` | `delete`
- `watch.realtime`：实时监听（inotify）的**全局能力开关**（网络共享目录会自动退化为轮询）。
  注意这是全局开关，某个目录要不要用实时监听由它自己的 `scanMode` 决定，两者是「与」的关系。
- `watch.settleSeconds`：文件稳定检测秒数——大小与修改时间连续这么多秒不变才入队
- `watch.minSize`：小于该大小的文件直接忽略
- `watch.allowedRoots`：可访问根目录白名单，约束目录浏览与监控目录添加

---

## 3. 监控目录

```json
{
  "id": "wp_ab12cd34",
  "path": "/vol1/media/inbox",
  "recursive": true,
  "scanMode": "realtime",
  "scanIntervalHours": 6,
  "scanTime": "03:00",
  "note": "相机导入目录",
  "createdAt": "2026-09-20T12:00:00+08:00",
  "lastScanAt": null,
  "nextScanAt": null,
  "videoCount": 0
}
```

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
| POST | `/api/watchpoints` | body `{path, recursive, scanMode, scanIntervalHours, scanTime, note}` |
| PUT | `/api/watchpoints/{id}` | 局部更新（上表除 `path`/`id` 外的字段均可） |
| DELETE | `/api/watchpoints/{id}` | 删除（想让它别再自动扫请改 `scanMode`，不必删除） |
| POST | `/api/watchpoints/{id}/scan` | **立即扫描一次**，返回 `ScanResult`（见下） |

`PUT` 会回读一次再返回，因此响应里的 `scanIntervalHours` / `scanTime`
一定是纠正后的**实际生效值**，前端直接用它刷新界面即可。

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
数值为各目录合计）。一条都没入队但确实看到了已处理的文件时，`message` 会说明
原因，而不是只回一句「0 个视频」。

---

## 4. 定时任务

```json
{
  "id": "sc_ef56ab78",
  "name": "每天凌晨 3 点",
  "cron": "0 3 * * *",
  "enabled": true,
  "watchpointIds": [],
  "lastRunAt": null,
  "nextRunAt": "2026-09-21T03:00:00+08:00",
  "cronText": "每天 03:00"
}
```
- `cron` 为**标准 5 段** cron 表达式（分 时 日 月 周）。
- `watchpointIds` 为空数组 = 扫描全部监控目录。
- `cronText` 由后端生成的中文可读描述，前端直接展示。

> 和监控目录「扫描方式」的分工：**扫描方式**决定每个目录平时的节奏
> （实时 / 每隔 N 小时 / 每天 / 仅手动）；**定时任务**是额外的补充扫描，
> 适合「目录设成仅手动，但每周一还要全量扫一次」这类需求。两者互不覆盖，
> 触发时都只是「扫一遍」，不会互相取消。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/schedules` | 列表 |
| POST | `/api/schedules` | body `{name, cron, enabled, watchpointIds}` |
| PUT | `/api/schedules/{id}` | 局部更新 |
| DELETE | `/api/schedules/{id}` | 删除 |
| POST | `/api/schedules/{id}/run` | 立即执行一次 |

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

## 6. 撤销分割

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

## 7. WebSocket

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

## 8. 前端约定

- 路由：`/`（概览）、`/watch`（监控目录）、`/schedule`（定时任务）、`/jobs`（任务队列）、`/settings`（设置）、`/undo`（撤销）。
- 左侧固定导航栏 + 右侧内容区；整体浅色主题，主色 `#185FA5`。
- 所有列表为空时给出明确的空状态提示文案。
- 时间统一显示为 `YYYY-MM-DD HH:mm:ss` 本地时间；体积显示为 `6.00 GB` 这种可读格式。
- 任务状态色：成功绿 `#3B6D11`、失败红 `#A32D2D`、运行中蓝 `#185FA5`、排队灰 `#5F5E5A`。
- 不引入任何 UI 框架（Element/Ant），样式自定义，用 CSS 变量集中管理。
- 依赖只用：`vue`、`vue-router`。不引 pinia，状态用 composable 管理。

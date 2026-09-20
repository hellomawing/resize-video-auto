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
- `watch.realtime`：是否启用 inotify 实时监听（网络共享目录会自动退化为轮询）
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
  "enabled": true,
  "note": "相机导入目录",
  "createdAt": "2026-09-20T12:00:00+08:00",
  "lastScanAt": null,
  "videoCount": 0
}
```

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/watchpoints` | 列表，返回 `[WatchPoint]` |
| POST | `/api/watchpoints` | body `{path, recursive, note}`，返回新建对象 |
| PUT | `/api/watchpoints/{id}` | 局部更新（`recursive`/`enabled`/`note`） |
| DELETE | `/api/watchpoints/{id}` | 删除 |
| POST | `/api/watchpoints/{id}/scan` | 立即扫描一次，返回 `{found, queued}` |

### POST /api/scan
扫描**全部启用**的监控目录并入队，返回 `{found, queued}`。

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
- `watchpointIds` 为空数组 = 扫描全部启用的监控目录。
- `cronText` 由后端生成的中文可读描述，前端直接展示。

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
  "orphans": ["/vol1/media/inbox/xxx#1.MP4"],
  "okCount": 1, "badCount": 0
}
```

### POST /api/undo/apply
body：`{ "path": "...", "recursive": true, "deleteSlices": true, "restoreOrigin": true, "trash": true }`
```json
{
  "deleted": 3, "restored": 1, "skipped": 0, "freedBytes": 6442450944,
  "problems": ["xxx：校验不通过…"],
  "details": [ { "base": "DJI_0001", "action": "deleted+restored", "message": "…" } ]
}
```
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
{ "type": "settings.updated" }
{ "type": "schedule.fired","scheduleId": "sc_x", "name": "每天凌晨 3 点" }
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

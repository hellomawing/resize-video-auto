// 格式化工具：体积 / 时间 / 耗时 / cron 文案。全部按契约第 8 节展示要求。

const SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']

/** 体积：1024 进制，保留两位（B 不保留小数）。 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) {
    return '-'
  }
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  const text = unit === 0 ? String(Math.round(value)) : value.toFixed(2)
  return `${text} ${SIZE_UNITS[unit]}`
}

/** 时间：ISO 字符串转本地 YYYY-MM-DD HH:mm:ss，空值返回破折号。 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '-'
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

/** 耗时：秒转「X小时X分X秒」，省略为 0 的更高位。 */
export function formatDuration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || sec < 0 || !Number.isFinite(sec)) return '-'
  const total = Math.floor(sec)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const parts: string[] = []
  if (h > 0) parts.push(`${h}小时`)
  if (m > 0) parts.push(`${m}分`)
  parts.push(`${s}秒`)
  return parts.join('')
}

/** cron 表达式原样展示（可读描述由后端 cronText 提供）。 */
export function formatCron(cron: string): string {
  return cron || '-'
}

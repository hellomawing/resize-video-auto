#!/bin/sh
# ---------------------------------------------------------------------------
# entrypoint.sh —— 处理 NAS 上最烦人的那件事：文件属主
#
# 为什么需要它：
#   容器默认以 root 跑，写出来的切片属主就是 root。在 NAS 的「文件管理」里
#   这些文件会显示成不属于任何用户，你既改不了名也删不掉，只能 SSH 进去
#   chown。所以这里按 PUID/PGID 把进程降权成 NAS 上的普通用户再跑。
#
# 用法（docker-compose 里）：
#   environment:
#     PUID: 1000      # 在 NAS 上用 `id 你的用户名` 查
#     PGID: 1000
#     UMASK: "022"
# ---------------------------------------------------------------------------
set -e

PUID="${PUID:-0}"
PGID="${PGID:-0}"
UMASK="${UMASK:-022}"

log() { echo "[entrypoint] $*"; }

# 已经以非 root 启动（比如 compose 里写了 user:）就不做降权，尊重用户的安排
if [ "$(id -u)" != "0" ] || [ "$PUID" = "0" ]; then
  umask "$UMASK"
  exec "$@"
fi

log "以 PUID=$PUID PGID=$PGID UMASK=$UMASK 启动"

# 组和用户不存在就按指定 id 建一个。用 -o 允许 id 重复：
# NAS 上 PUID 常常就是已存在的 1000，重复也不会报错。
if ! getent group "$PGID" >/dev/null 2>&1; then
  groupadd -g "$PGID" -o app >/dev/null 2>&1 || true
fi
if ! getent passwd "$PUID" >/dev/null 2>&1; then
  useradd -u "$PUID" -g "$PGID" -o -s /bin/sh -M app >/dev/null 2>&1 \
    || useradd -u "$PUID" -o -s /bin/sh -M app >/dev/null 2>&1 || true
fi

# /data 只存配置和数据库，很小，chown 开销可以忽略；
# 但如果属主已经对了就跳过，避免每次都白跑一遍。
if [ -d /data ]; then
  if [ "$(stat -c '%u' /data 2>/dev/null)" != "$PUID" ]; then
    chown -R "$PUID:$PGID" /data 2>/dev/null || \
      log "警告：无法修改 /data 属主，若出现配置无法保存请检查挂载权限"
  fi
fi

umask "$UMASK"
exec gosu "$PUID:$PGID" "$@"

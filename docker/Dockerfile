# video-splitter NAS 版 · 镜像
#
# 两阶段构建：
#   1. web 阶段用 node 把 Vue 前端编译成静态文件
#   2. 运行阶段只保留 Python + ffmpeg + 构建产物，不把 node_modules 带进最终镜像
#
# 同时支持 linux/amd64 与 linux/arm64（飞牛等 NAS 有 ARM 机型），
# 用 docker/buildx 构建多架构镜像，见 docker/build-and-push.sh。

# ---------------------------------------------------------------- 前端构建
FROM node:22-alpine AS web

WORKDIR /web

# 先只拷依赖清单，让 npm install 这一层能被 Docker 缓存住
COPY web/package.json web/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY web/ ./
RUN npm run build


# ---------------------------------------------------------------- 运行环境
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    VS_DATA_DIR=/data \
    VS_HOST=0.0.0.0 \
    VS_PORT=8099

# ffmpeg 是整个工具的核心依赖（流拷贝模式靠它）；gosu 用来在启动时降权到
# PUID/PGID 指定的用户，这是 NAS 场景的刚需——否则容器以 root 写出来的切片
# 在 NAS 的文件管理里会显示为「不属于任何用户」，既改不了也删不掉。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg gosu tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/video-splitter

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY core ./core
COPY --from=web /web/dist ./web/dist

# 显式统一权限，不要依赖源文件的 mode 位。
#
# 踩过的坑：构建上下文如果放在 NAS 的共享目录里（实测飞牛 /vol1/1000/** 就是），
# 那些文件用 stat 看经典权限位是 000 —— 实际访问靠的是 ACL，所以平时读写都正常。
# 而 docker COPY 会原样保留这个 000，镜像里的代码就成了 root 可读、别人全不可读。
# 于是 entrypoint 按 PUID 降权之后直接 PermissionError，容器反复重启。
# 下面这行把它兜住：a+rX 给目录加执行位、给文件加读位，不放开写权限。
RUN chmod -R a+rX app core web requirements.txt

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /data

VOLUME ["/data"]
EXPOSE 8099

# 探针只查 /healthz，不碰数据库，避免健康检查本身拖慢服务
HEALTHCHECK --interval=30s --timeout=8s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/healthz', timeout=5).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "app.main"]

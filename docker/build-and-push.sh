#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 构建多架构镜像并推送到 Docker Hub
#
#   DOCKERHUB_USER=mawing ./docker/build-and-push.sh
#
# 可选环境变量：
#   VERSION     镜像版本号，默认取 app/config.py 里的 APP_VERSION
#   IMAGE       完整镜像名，默认 ${DOCKERHUB_USER}/video-splitter
#   PLATFORMS   目标架构，默认 linux/amd64,linux/arm64
#   LOCAL=1     只在本地构建并载入当前架构（不推送），用来快速验证
#
# 为什么用 buildx：NAS 里 x86 和 ARM 都很常见（飞牛有 ARM 机型、
# 群晖/绿联也有），推一个多架构 manifest 之后，
# `docker pull 你的镜像` 在任何机器上都能自动拿到对的架构。
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

DOCKERHUB_USER="${DOCKERHUB_USER:-yourname}"
IMAGE="${IMAGE:-${DOCKERHUB_USER}/video-splitter}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
VERSION="${VERSION:-$(grep -oP 'APP_VERSION\s*=\s*"\K[^"]+' app/config.py 2>/dev/null || echo 1.0.0)}"

if [ "${DOCKERHUB_USER}" = "yourname" ] && [ "${LOCAL:-0}" != "1" ]; then
  echo "!! 请先设置你的 Docker Hub 用户名，例如："
  echo "   DOCKERHUB_USER=zhangsan $0"
  exit 1
fi

echo "镜像    : ${IMAGE}"
echo "版本    : ${VERSION}  以及 latest"
echo "架构    : ${PLATFORMS}"

# buildx 是构建多架构镜像的前提，没有就提示怎么装
if ! docker buildx version >/dev/null 2>&1; then
  echo "!! 当前 docker 不支持 buildx。"
  echo "   Docker Desktop 自带；Linux 上需要安装 docker-buildx-plugin。"
  exit 1
fi

# 建一个一次性的 builder，避免改到用户现有的 builder 配置
BUILDER="video-splitter-builder"
if ! docker buildx inspect "${BUILDER}" >/dev/null 2>&1; then
  echo "创建 buildx builder: ${BUILDER}"
  docker buildx create --name "${BUILDER}" --use >/dev/null
else
  docker buildx use "${BUILDER}" >/dev/null
fi
docker buildx inspect --bootstrap >/dev/null

if [ "${LOCAL:-0}" = "1" ]; then
  echo "本地构建（不推送）…"
  docker buildx build \
    --platform linux/amd64 \
    -f docker/Dockerfile \
    -t "${IMAGE}:${VERSION}" \
    -t "${IMAGE}:latest" \
    --load .
  echo "完成。可以这样起一个测试容器："
  echo "  docker run --rm -p 8099:8099 -v \"\$(pwd)/data:/data\" ${IMAGE}:latest"
  exit 0
fi

echo "开始构建并推送（首次构建要下 node/python 基础镜像，会慢一些）…"
docker buildx build \
  --platform "${PLATFORMS}" \
  -f docker/Dockerfile \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:latest" \
  --push .

echo
echo "推送完成："
echo "  ${IMAGE}:${VERSION}"
echo "  ${IMAGE}:latest"
echo
echo "接下来把 deploy/.env.example 里的 VS_IMAGE 改成 ${IMAGE}:latest，"
echo "以及 fnos/app/docker/docker-compose.yaml 里的 image 改成同一个地址。"

#!/bin/bash
# macOS 启动器：双击运行会弹出文件夹选择框；
# 也可以把文件夹拖到本文件图标上直接处理。
# 首次使用可能需要先执行一次：chmod +x run_splitter.command
#
# 优先用打包好的免安装可执行文件，没有才回退到 Python 解释器。
# 打包命令：python3 build_exe.py   （产物落在 dist/）
#
# 关于「Mac 是不是都自带 Python」：不是。
#   * macOS 12.3 起系统不再附带 Python 2.7；
#   * /usr/bin/python3 确实存在，但它指向的是 Apple 给开发工具（Xcode /
#     命令行工具）准备的那一份，通常偏旧、功能不全，而且在没装命令行工具的
#     机器上执行它会弹出「安装命令行开发者工具」对话框（约 1.5G 下载）。
#   所以本脚本会跳过这个占位程序，优先找 Homebrew / python.org / pyenv 装的
#   真 Python；一个都找不到就明确告诉用户去用打包好的可执行文件。

cd "$(dirname "$0")" || exit 1

SHIM_SEEN=""
PY_FOUND=""

# 找一个「真能用」的 python3。找到就把路径写进 PY_FOUND 并返回 0。
#
# 注意：这里刻意用「写全局变量」而不是 echo 出路径 —— 若写成
# PY="$(find_python)"，函数就跑在子 shell 里，SHIM_SEEN 的赋值传不出来，
# 「发现的是 Apple 占位程序」这个提示会永远不显示。
find_python() {
  PY_FOUND=""
  for cand in \
      "$(command -v python3 2>/dev/null)" \
      /opt/homebrew/bin/python3 \
      /usr/local/bin/python3 \
      "$HOME/.pyenv/shims/python3" \
      /Library/Frameworks/Python.framework/Versions/Current/bin/python3
  do
    [ -n "$cand" ] || continue
    [ -x "$cand" ] || continue
    case "$cand" in
      /usr/bin/python3)
        # Apple 的占位程序：命令行工具没装时，跑它会弹安装对话框。
        # 先问 xcode-select 要路径，拿不到就说明工具链不在，直接跳过。
        if ! xcode-select -p >/dev/null 2>&1; then
          SHIM_SEEN="/usr/bin/python3"
          continue
        fi
        ;;
    esac
    PY_FOUND="$cand"
    return 0
  done
  return 1
}

if [ -x "./dist/video_splitter/video_splitter" ]; then
  # onedir 版：解压在构建时就做完了，启动约 1 秒
  RUNNER=("./dist/video_splitter/video_splitter")
  echo "[env] runner : dist/video_splitter/video_splitter（免安装 Python）"
elif [ -f "./dist/video_splitter" ] && [ -x "./dist/video_splitter" ]; then
  # onefile 版。注意这里必须带 -f：onedir 产物是个同名目录，
  # 而 [ -x 目录 ] 也是真，不带 -f 会把目录当成可执行文件。
  RUNNER=("./dist/video_splitter")
  echo "[env] runner : dist/video_splitter（免安装 Python）"
elif [ -f "./video_splitter" ] && [ -x "./video_splitter" ]; then
  RUNNER=("./video_splitter")
  echo "[env] runner : video_splitter（免安装 Python）"
elif find_python; then
  RUNNER=("$PY_FOUND" ./video_splitter.py)
  echo "[env] runner : $PY_FOUND ./video_splitter.py"
else
  echo "===================================================================="
  echo "[错误] 没有可用的 Python 3，也没有打包好的可执行文件。"
  echo "===================================================================="
  echo
  if [ -n "$SHIM_SEEN" ]; then
    echo "系统里那个 $SHIM_SEEN 是 Apple 给开发工具用的占位程序，不是"
    echo "可以拿来跑脚本的 Python。直接执行它会弹出「安装命令行开发者工具」"
    echo "对话框，要下载 1.5G 左右 —— 为了跑这一个脚本不值得。"
    echo
  fi
  echo "推荐做法：打包一次免安装的可执行文件，之后就不再需要 Python 了。"
  echo "  1) 先装一个真 Python 3（任选其一）："
  echo "       * 官网安装包  https://www.python.org/downloads/macos/"
  echo "       * Homebrew    brew install python3"
  echo "  2) 然后在本目录执行："
  echo "       python3 build_exe.py"
  echo "     产物是 dist/video_splitter，本文件以后会自动用它。"
  echo
  echo "或者直接下载别人打包好的可执行文件，放到本目录即可。"
  echo
  read -r -p "按回车键关闭..."
  exit 1
fi

"${RUNNER[@]}" "$@"
CODE=$?

# 刻意不加「按任意键退出」：完整输出已经同时写进日志文件，
# 随时可以打开复制，也不会有按键提示挡住终端。
echo
echo "===================================================================="
if [ "$CODE" -eq 0 ]; then
  echo "已完成。以上完整输出已保存到日志文件："
else
  echo "运行结束（退出码 $CODE）。完整输出已保存到日志文件："
fi
echo "  $(pwd)/video_splitter_log.txt"
echo "想在当前窗口直接翻看，可以执行："
echo "  cat \"$(pwd)/video_splitter_log.txt\""
echo "===================================================================="

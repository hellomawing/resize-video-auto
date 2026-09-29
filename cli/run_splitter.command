#!/bin/bash
# macOS 启动器：双击运行会弹出文件夹选择框；
# 也可以把文件夹拖到本文件图标上直接处理。
# 首次使用可能需要先执行一次：chmod +x run_splitter.command

cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "[错误] 未找到 Python 3，请先安装：https://www.python.org/downloads/"
  read -r -p "按回车键关闭..."
  exit 1
fi

"$PY" ./video_splitter.py "$@"
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

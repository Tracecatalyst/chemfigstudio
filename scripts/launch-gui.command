#!/bin/bash
# 双击启动 chemfigstudio 图形界面（macOS）。
#
# 输出目录优先级：命令行参数 > ~/Desktop/chemfig-out
# 想固定到别处：把 OUTDIR 环境变量改掉，或直接跑
#   make gui OUT=/your/path
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$HERE/src"

DEFAULT_ASSIGNMENT_DIR="$HOME/Desktop/chemfig-out"
if [ "${1:-}" != "" ]; then
  OUTDIR="$1"
elif [ -d "$DEFAULT_ASSIGNMENT_DIR" ]; then
  OUTDIR="$DEFAULT_ASSIGNMENT_DIR"
else
  OUTDIR="$HOME/Desktop/chemfig-out"
fi

echo "chemfigstudio —— 输出目录：$OUTDIR"
echo "（关闭这个终端窗口即可停止服务）"
exec python3 -m chemfigstudio.gui.server --outdir "$OUTDIR"

#!/bin/bash
# 双击启动 chemfigstudio 原生应用（macOS）。
# 第一次会先编译（约 5 秒，需要 Xcode 命令行工具里的 swiftc），之后直接打开。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$HERE/dist/ChemFigStudio.app"

if [ ! -d "$APP" ]; then
  echo "首次运行：正在编译原生应用（约 5 秒）…"
  "$HERE/scripts/build-macos-app.sh"
  echo
fi

echo "正在打开 $APP"
open "$APP"

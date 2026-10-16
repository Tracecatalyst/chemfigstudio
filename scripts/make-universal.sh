#!/usr/bin/env bash
# 把两个架构的 .app 合并成一个通用二进制（Apple Silicon + Intel）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARM="$ROOT/dist/universal-arm64/ChemFigStudio.app"
INTEL="$ROOT/dist/universal-x86_64/ChemFigStudio.app"
OUT="$ROOT/dist/ChemFigStudio-universal.app"

for slice in "$ARM" "$INTEL"; do
  [ -d "$slice" ] || { echo "缺少切片：$slice（先跑 build-macos-app.sh）" >&2; exit 1; }
done

rm -rf "$OUT"
cp -R "$ARM" "$OUT"

# 合并可执行文件
lipo -create "$ARM/Contents/MacOS/ChemFigStudio" "$INTEL/Contents/MacOS/ChemFigStudio" \
     -output "$OUT/Contents/MacOS/ChemFigStudio"

# 资源以 arm64 那份为准；把 Intel 切片里多出来的差异文件补齐
for extra in $(cd "$INTEL" && find . -type f ! -path "./Contents/MacOS/ChemFigStudio"); do
  if [ ! -e "$OUT/$extra" ]; then
    mkdir -p "$(dirname "$OUT/$extra")"
    cp "$INTEL/$extra" "$OUT/$extra"
  fi
done

codesign --force --deep --sign - "$OUT" 2>/dev/null || \
  echo "提示：ad-hoc 签名失败（macOS 26 已移除 spctl --add，需开发者证书才能免拦截）" >&2

echo "已生成：$OUT"
lipo -info "$OUT/Contents/MacOS/ChemFigStudio"

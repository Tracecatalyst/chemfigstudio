#!/bin/bash
# 构建 macOS 原生应用 dist/ChemFigStudio.app
#
# 零第三方依赖：只用系统自带的 swiftc / Cocoa / WebKit。
# 内嵌 chemfigstudio 的 Python 源码（Resources/src），可选内嵌 Pillow（Resources/vendor），
# 这样拷给别人也能跑（对方只要有系统自带 python3）。
#
# 用法：scripts/build-macos-app.sh [--no-vendor] [--no-icon] [--arch arm64|x86_64] [--out 目录]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH=""
OUT_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --arch) ARCH="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    *) shift ;;
  esac
done

APP="${OUT_DIR:-$HERE/dist}/ChemFigStudio.app"
VERSION="$(grep -m1 '__version__' "$HERE/src/chemfigstudio/__init__.py" | cut -d'"' -f2)"
VENDOR=1
ICON=1
for arg in "$@"; do
  case "$arg" in
    --no-vendor) VENDOR=0 ;;
    --no-icon)   ICON=0 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

echo "── 1/6 目录 ──"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/src"

echo "── 2/6 编译 Swift（Cocoa + WebKit）──"
# module-cache-path：默认缓存目录在沙箱/CI 里可能不可写，显式指到临时目录更稳
SWIFT_CACHE="${TMPDIR:-/tmp}/chemfigstudio-swift-cache"
mkdir -p "$SWIFT_CACHE"
SDK_PATH="$(xcrun --show-sdk-path)"
# 注意：bash 3.2（macOS 自带）+ set -u 下空数组展开会报 unbound variable，
# 所以这里用普通字符串而不是数组。
TARGET_FLAG=""
[ -n "$ARCH" ] && TARGET_FLAG="-target ${ARCH}-apple-macos11"

swiftc -O -whole-module-optimization $TARGET_FLAG \
  -module-cache-path "$SWIFT_CACHE" -sdk "$SDK_PATH" \
  -framework Cocoa -framework WebKit \
  -o "$APP/Contents/MacOS/ChemFigStudio" \
  "$HERE/native/macos/ChemFigStudio.swift"
echo "    可执行文件：$(du -h "$APP/Contents/MacOS/ChemFigStudio" | cut -f1)"

echo "── 3/6 内嵌 Python 源码 ──"
cp -R "$HERE/src/chemfigstudio" "$APP/Contents/Resources/src/chemfigstudio"
find "$APP/Contents/Resources/src" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
echo "    src：$(du -sh "$APP/Contents/Resources/src" | cut -f1)"

PIL_SIZE="未内嵌"
if [ "$VENDOR" = "1" ]; then
  echo "── 4/6 内嵌 Pillow（让对方机器没装 Pillow 也能跑）──"
  PIL_DIR="$(python3 - <<'PY' 2>/dev/null || true
import os, PIL
print(os.path.dirname(PIL.__file__))
PY
)"
  if [ -n "$PIL_DIR" ] && [ -d "$PIL_DIR" ]; then
    mkdir -p "$APP/Contents/Resources/vendor"
    cp -R "$PIL_DIR" "$APP/Contents/Resources/vendor/PIL"
    find "$APP/Contents/Resources/vendor" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    find "$APP/Contents/Resources/vendor" -name '*.pyc' -delete 2>/dev/null || true
    PIL_SIZE="$(du -sh "$APP/Contents/Resources/vendor" | cut -f1)"
    echo "    Pillow：${PIL_SIZE}（$(python3 -c 'import PIL;print(PIL.__version__)')，仅适用于同版本 Python/架构）"
  else
    echo "    ⚠ 找不到 Pillow，跳过（目标机器需自行确保 python3 有 Pillow）"
    PIL_SIZE="未内嵌"
  fi
else
  echo "── 4/6 跳过 Pillow（--no-vendor）──"
fi

if [ "$ICON" = "1" ]; then
  echo "── 5/6 生成图标（用项目自己的画图代码）──"
  if python3 "$HERE/scripts/make-icon.py" "$APP/Contents/Resources/AppIcon.icns" 2>/dev/null; then
    echo "    图标：$(du -h "$APP/Contents/Resources/AppIcon.icns" | cut -f1)"
  else
    echo "    ⚠ 图标生成失败，用系统默认图标"
    ICON=0
  fi
else
  echo "── 5/6 跳过图标（--no-icon）──"
fi

echo "── 6/6 Info.plist ──"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>chemfigstudio</string>
    <key>CFBundleDisplayName</key><string>chemfigstudio</string>
    <key>CFBundleIdentifier</key><string>local.chemfigstudio</string>
    <key>CFBundleExecutable</key><string>ChemFigStudio</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleShortVersionString</key><string>${VERSION}</string>
    <key>CFBundleVersion</key><string>${VERSION}</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSPrincipalClass</key><string>NSApplication</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.education</string>
$([ "$ICON" = "1" ] && echo "    <key>CFBundleIconFile</key><string>AppIcon</string>")
</dict>
</plist>
PLIST
plutil -lint "$APP/Contents/Info.plist" >/dev/null

# 就地临时签名，避免 Gatekeeper 直接拒绝启动（本地开发用 ad-hoc 签名足够）
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "    （ad-hoc 签名跳过，不影响本机使用）"

echo
echo "已生成：$APP"
echo "  版本 $VERSION | 可执行 $(du -h "$APP/Contents/MacOS/ChemFigStudio" | cut -f1) | Pillow $PIL_SIZE | 整包 $(du -sh "$APP" | cut -f1)"
echo "双击即可运行；也可拖到 /Applications。"

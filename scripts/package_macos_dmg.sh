#!/bin/zsh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="${1:-$PROJECT_ROOT/dist/MicroFlow.app}"
VERSION="${2:-v1.0.0}"
ARCH="${3:-arm64}"

if [[ ! -d "$APP_PATH" ]]; then
  echo "未找到 app 包：$APP_PATH" >&2
  echo "请先执行 PyInstaller，确认 dist/MicroFlow.app 已生成。" >&2
  exit 1
fi

RELEASE_DIR="$PROJECT_ROOT/release/macos"
STAGING_DIR="$RELEASE_DIR/.dmg-staging"
APP_NAME="$(basename "$APP_PATH")"
OUTPUT_DMG="$RELEASE_DIR/MicroFlow-${VERSION}-macos-${ARCH}.dmg"

rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"

# 使用 ditto 保留 macOS app bundle 所需的资源元数据。
ditto "$APP_PATH" "$STAGING_DIR/$APP_NAME"

# Finder 打开 DMG 后会显示标准的“拖到 Applications”目标。
ln -s /Applications "$STAGING_DIR/Applications"

mkdir -p "$RELEASE_DIR"
rm -f "$OUTPUT_DMG"

hdiutil create \
  -volname "MicroFlow" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$OUTPUT_DMG"

rm -rf "$STAGING_DIR"

echo "DMG 已生成：$OUTPUT_DMG"

#!/bin/bash
# court-doc-extract · 在当前 Mac 从纯文本 AppleScript 源码编译 applet，并安装到
# 固定用户目录 ~/Library/Application Support/CourtDocExtract/。
#
# v1.4.5 机制修复要点（对照旧版）：
#   · 编译产物**安装到稳定用户目录**（而非每次同步都重编译/重签名/改路径）。
#   · 每个 applet 设置**稳定、唯一**的 CFBundleIdentifier（com.courtdocextract.<name>），
#     首次运行由 GUI applet 自己触发 Calendar/Reminders 自动化权限请求，用户点「允许」。
#   · **仅在安装/更新（本脚本）时**做 ad-hoc 签名 + 剥离 quarantine；
#     同步运行期（generate_macos_reminders.py）绝不再执行 codesign --force。
#   · 条件触发：三个 .app 全部存在于安装目录 且 指纹一致 且 非 --force 时，跳过编译。
#     指纹 = 编译方式版本 + 三个 .applescript 源码内容哈希。
#
# 用法：
#   bash setup_apps.sh            # 缺失或源码变化时才编译安装
#   bash setup_apps.sh --force    # 强制重新编译安装
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAMES=(wb-reminder wb-calendar wb-clear)
STAMP_FILE="$SCRIPT_DIR/.applet_build_stamp"

# 固定安装目录（与 AppleScript 的 getSyncDir 同根：~/Library/Application Support/CourtDocExtract/）
INSTALL_DIR="$HOME/Library/Application Support/CourtDocExtract"

# 编译方式、签名策略或依赖发生变更时，手动把此版本号 +1，即可让所有机器重新安装一次。
# v1.4.5：applet 改为无参数 on run + 固定同步目录 + RUN_ID 协议，源码与此前不兼容，必须重装。
BUILD_STAMP_VERSION="4"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force|-f)
      FORCE=1
      ;;
    --help|-h)
      echo "用法：bash setup_apps.sh [--force]"
      echo "  （无参数）仅在 applet 缺失或源码指纹变化时编译安装到 $INSTALL_DIR"
      echo "  --force   忽略指纹校验，强制重新编译安装"
      exit 0
      ;;
    *)
      echo "⚠️ 忽略未知参数：$arg"
      ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "❌ Mac 日历和提醒事项功能仅支持 macOS。"
  exit 1
fi

if ! command -v osacompile >/dev/null 2>&1; then
  echo "❌ 未找到 osacompile，无法编译 AppleScript applet。"
  exit 1
fi

# 校验全部源码存在
missing_sources=""
for name in "${APP_NAMES[@]}"; do
  [ -f "$SCRIPT_DIR/$name.applescript" ] || missing_sources="$missing_sources $name.applescript"
done
if [ -n "$missing_sources" ]; then
  echo "❌ 缺少源码：$missing_sources"
  exit 1
fi

# 计算源码指纹：编译方式版本 + 各源码内容哈希
source_fingerprint() {
  local digest="$BUILD_STAMP_VERSION"
  for name in "${APP_NAMES[@]}"; do
    if command -v shasum >/dev/null 2>&1; then
      digest="$digest:$(shasum -a 256 "$SCRIPT_DIR/$name.applescript" | awk '{print $1}')"
    else
      digest="$digest:$(cksum "$SCRIPT_DIR/$name.applescript" | awk '{print $1 $2}')"
    fi
  done
  printf '%s' "$digest"
}

# 三个 .app 是否全部存在于安装目录
apps_complete_in_install() {
  for name in "${APP_NAMES[@]}"; do
    [ -d "$INSTALL_DIR/$name.app" ] || return 1
  done
  return 0
}

FINGERPRINT="$(source_fingerprint)"
LAST_STAMP=""
if [ -f "$STAMP_FILE" ]; then
  LAST_STAMP="$(cat "$STAMP_FILE" 2>/dev/null || printf '')"
fi

if [ "$FORCE" -eq 0 ] && apps_complete_in_install && [ "$LAST_STAMP" = "$FINGERPRINT" ]; then
  echo "⏭️  Mac applet 已是最新（三个 .app 齐备且源码指纹一致），跳过编译安装。"
  echo "   需要强制重装时执行：bash \"$0\" --force"
  exit 0
fi

# 确保安装目录存在（权限 0755，用户可读写执行）
mkdir -p "$INSTALL_DIR"

echo "==> 从源码编译并安装 court-doc-extract applet 到 $INSTALL_DIR"
for name in "${APP_NAMES[@]}"; do
  source_file="$SCRIPT_DIR/$name.applescript"
  app_path="$INSTALL_DIR/$name.app"
  # 安装前清掉旧版本（若存在），避免残留
  rm -rf "$app_path"
  osacompile -o "$app_path" "$source_file"
  # 设置稳定、唯一的 CFBundleIdentifier（覆盖 osacompile 默认生成的随机/ScriptEditor id）
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.courtdocextract.$name" \
    "$app_path/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.courtdocextract.$name" \
    "$app_path/Contents/Info.plist"
  # 仅在安装/更新时签名 + 剥离隔离属性（同步运行期不再做 codesign --force）
  codesign --force --deep --sign - "$app_path" >/dev/null 2>&1 || true
  xattr -dr com.apple.quarantine "$app_path" >/dev/null 2>&1 || true
  echo "  ✔ $name.app（CFBundleIdentifier=com.courtdocextract.$name）"
done

printf '%s' "$FINGERPRINT" > "$STAMP_FILE"

# 清理 skills 内遗留的旧编译产物（避免与安装目录的同一 .app 混淆；打包器也会排除 *.app）
for name in "${APP_NAMES[@]}"; do
  rm -rf "$SCRIPT_DIR/$name.app"
done

echo "==> 安装完成，已写入指纹 ${STAMP_FILE}（下次运行将自动跳过）。"
echo "    首次同步时 GUI applet 会自行请求 Calendar/Reminders 自动化权限，请点「允许」。"
echo "    注意：技能包不含本机 .app，每台机器首次使用时由本脚本现场编译安装。"

#!/bin/bash
# 更新并打开克定中原 — double-clickable macOS launcher
# Pulls latest main, regenerates Godot import/cache, then launches play mode.
set -euo pipefail

REPO="${HOME}/keding-zhongyuan"
GODOT_APP="${HOME}/.local/opt/Godot.app"
GODOT_BIN="${GODOT_APP}/Contents/MacOS/Godot"

cd() { builtin cd "$@"; }

echo "=== 克定中原：更新并打开 ==="
echo "Repo: $REPO"

if [[ ! -d "$REPO/.git" ]]; then
  echo "ERROR: repo not found at $REPO" >&2
  read -r -p "按回车关闭…"
  exit 1
fi

if [[ ! -x "$GODOT_BIN" ]]; then
  echo "ERROR: Godot not found at $GODOT_BIN" >&2
  read -r -p "按回车关闭…"
  exit 1
fi

cd "$REPO"

echo "→ git fetch / checkout main / pull"
git fetch origin
git checkout main
git pull --ff-only origin main
echo "→ HEAD: $(git log -1 --oneline)"

echo "→ 关闭旧的 Godot（本项目）"
# Kill any Godot instance whose args include this project path
pkill -f "${REPO}" 2>/dev/null || true
# Also match dual_scale leftovers if any
pkill -f 'dual_scale' 2>/dev/null || true
sleep 1

echo "→ Godot --import（重建 .godot 脚本缓存；否则会黑屏）"
"$GODOT_BIN" --headless --path "$REPO" --import
if [[ ! -f "$REPO/.godot/global_script_class_cache.cfg" ]]; then
  echo "ERROR: import finished but global_script_class_cache.cfg missing" >&2
  read -r -p "按回车关闭…"
  exit 1
fi
echo "→ 脚本缓存 OK"

echo "→ 启动 play mode"
open -na "$GODOT_APP" --args --path "$REPO"

echo "完成。可关闭此窗口。"
sleep 2

#!/usr/bin/env bash
# MC Panel 启动脚本（Linux / macOS）
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 没有找到 python3，请先安装 Python 3.8+"
  exit 1
fi

exec python3 panel.py "$@"

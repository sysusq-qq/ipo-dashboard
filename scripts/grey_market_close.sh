#!/bin/bash
# grey_market_close.sh — launchd 调用的包装脚本
# 每日 18:40 HKT 由 launchd 自动触发

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$REPO_DIR/logs/grey_market_close.log"

mkdir -p "$REPO_DIR/logs"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] launchd 触发 grey_market_close" >> "$LOG_FILE"

cd "$REPO_DIR"
python3 scripts/grey_market_close.py >> "$LOG_FILE" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 脚本退出，状态码: $?" >> "$LOG_FILE"

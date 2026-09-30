#!/usr/bin/env bash
# 一条本地启动命令：准备缺码 / 调岗 / 提前换领样例并启动系统。
# 用法：./run.sh [--reseed] [端口]
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
PORT="${PORT:-8000}"
for arg in "$@"; do
  case "$arg" in
    --help|-h)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) [[ "$arg" =~ ^[0-9]+$ ]] && PORT="$arg" ;;
  esac
done

mkdir -p data/photos
echo "▶ 准备演示数据（缺码候补 E1004 / 调岗 E1006 / 提前换领 E1005）…"
$PYTHON -m shoe_system.seed --db data/shoes.db --photos data/photos

echo "▶ 自检（唯一有效鞋 / 并发竞争 / 期限 / 豁免审批 / 岗位权限）…"
$PYTHON -m unittest tests.test_services 2>&1 | tail -3

echo "▶ 启动服务：http://127.0.0.1:${PORT}"
exec $PYTHON -m shoe_system --host 0.0.0.0 --port "${PORT}" --db data/shoes.db --photos data/photos

#!/usr/bin/env bash
# 一条本地启动命令：重置演示数据（缺码候补 / 跨岗位调动 / 提前磨损换领样例）并启动服务。
set -euo pipefail
cd "$(dirname "$0")"

export SAFETY_SHOES_DATA="${SAFETY_SHOES_DATA:-$(pwd)/safety_shoes/data}"
PORT="${PORT:-8000}"

echo ">> 准备演示数据（缺码候补 E1004、调岗待结算 E1005、提前磨损待审批 E1003、遗失豁免 E1006）"
python3 -m safety_shoes.seed

echo ">> 启动服务：http://127.0.0.1:${PORT}"
exec python3 -m safety_shoes --no-reseed --port "${PORT}"

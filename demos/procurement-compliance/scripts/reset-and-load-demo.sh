#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "正在重启 Explorer，以清空仅存于内存的当前图谱和本体注册表。"
echo "FalkorDB 容器及其数据卷不会被删除。"
docker compose --project-directory "$REPO_DIR" restart explorer

"$SCRIPT_DIR/load-demo.sh"

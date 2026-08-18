#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BASE_URL="${SEMANTICA_DEMO_URL:-http://127.0.0.1:8000}"
DATA_DIR="$DEMO_DIR/data"

wait_for_explorer() {
  local attempts=60
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl -fsS "$BASE_URL/api/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "错误：等待 Semantica Explorer 启动超时：$BASE_URL" >&2
  return 1
}

post_file() {
  local endpoint="$1"
  local file_path="$2"
  curl -fsS -X POST "$BASE_URL$endpoint" -F "file=@$file_path"
}

echo "[1/4] 等待 Semantica Explorer..."
wait_for_explorer

echo "[2/4] 导入采购合规业务图谱..."
post_file "/api/import" "$DATA_DIR/procurement-compliance-base.json"
echo

echo "[3/4] 导入采购合规 SKOS 术语表..."
post_file "/api/vocabulary/import" "$DATA_DIR/procurement-compliance-vocabulary.ttl"
echo

echo "[4/4] 加载采购合规 OWL 本体..."
ontology_payload="$({ python3 - "$DATA_DIR/procurement-compliance-ontology.ttl" <<'PY'
import json
from pathlib import Path
import sys

content = Path(sys.argv[1]).read_text(encoding="utf-8")
print(json.dumps({
    "content": content,
    "format": "turtle",
    "name": "采购合规审查本体",
    "description": "采购申请、供应商、投标、证据、风险与审查决策的语义模型",
    "tags": ["采购", "合规", "演示"]
}, ensure_ascii=False))
PY
} 2>/dev/null)"
curl -fsS -X POST "$BASE_URL/api/ontology/load" \
  -H "Content-Type: application/json" \
  --data-binary "$ontology_payload"
echo

python3 "$SCRIPT_DIR/verify-demo.py" "$BASE_URL"
echo
echo "演示环境已就绪：$BASE_URL"
echo "现场增量文件（请勿预先导入）：$DATA_DIR/live-blacklist-update.json"

#!/usr/bin/env bash
set -euo pipefail

server_dir=/opt/idea-server/server
set -a
. "$server_dir/.env.runtime"
set +a

health=$(curl -fsS --max-time 10 http://127.0.0.1:8900/health)
curl -fsS --max-time 180 \
    -H "Authorization: Bearer $IDEA_AUTH_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"agent_id":"idea","message":"只回复 OK。","conversation_id":"model-smoke-check"}' \
    http://127.0.0.1:8900/api/assistant/chat > /tmp/idea-model-smoke.json
curl -ksS --max-time 10 --resolve shiroha-rin.world:443:127.0.0.1 https://shiroha-rin.world/health > /tmp/idea-https-smoke.json

python3 - "$health" /tmp/idea-model-smoke.json /tmp/idea-https-smoke.json <<'PY'
import json
import sys
from pathlib import Path

health = json.loads(sys.argv[1])
chat = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
https_health = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
assert health["status"] == "healthy"
assert health["llm_available"] is True
assert isinstance(chat.get("reply"), str) and chat["reply"].strip()
assert chat.get("conversation_id") == "model-smoke-check"
assert https_health["status"] == "healthy"
print("model-and-https-smoke-ok")
PY

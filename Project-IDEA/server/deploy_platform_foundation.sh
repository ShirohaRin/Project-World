#!/usr/bin/env bash
set -euo pipefail

server_dir=/opt/idea-server/server
old_pid="$1"

"/opt/idea-server/venv/bin/python" - "$old_pid" "$server_dir/.env.runtime" <<'PY'
import os
import sys
from pathlib import Path

source = Path(f"/proc/{sys.argv[1]}/environ")
target = Path(sys.argv[2])
values = {}
for item in source.read_bytes().split(bytes([0])):
    if b"=" in item:
        key, value = item.split(b"=", 1)
        values[key.decode()] = value.decode()
keys = (
    "IDEA_AUTH_TOKEN",
    "DOUBAO_API_KEY",
    "GLM_API_KEY",
    "OPENAI_API_KEY",
    "CUSTOM_API_KEY",
    "CUSTOM_API_BASE_URL",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "IDEA_SMTP_HOST",
    "IDEA_SMTP_PORT",
    "IDEA_SMTP_USERNAME",
    "IDEA_SMTP_PASSWORD",
    "IDEA_SMTP_FROM",
    "IDEA_SMTP_USE_SSL",
    "IDEA_SMTP_USE_STARTTLS",
    "IDEA_AUTH_DEVELOPMENT_MODE",
)
target.write_text("\n".join(f"{key}={values[key]}" for key in keys if values.get(key)) + "\n", encoding="utf-8")
os.chmod(target, 0o600)
PY

kill -TERM "$old_pid"
for _ in $(seq 1 10); do
    if ! kill -0 "$old_pid" 2>/dev/null; then
        break
    fi
    sleep 1
done

if kill -0 "$old_pid" 2>/dev/null; then
    echo "old process did not stop" >&2
    exit 1
fi

cd "$server_dir"
nohup bash -c 'set -a; . /opt/idea-server/server/.env.runtime; set +a; exec /opt/idea-server/venv/bin/python /opt/idea-server/server/main.py' >> logs/server.log 2>> logs/error.log < /dev/null &
echo "$!" > idea-server.pid
sleep 3
kill -0 "$(cat idea-server.pid)"
curl -fsS --max-time 5 http://127.0.0.1:8900/health >/tmp/idea-health.json
"/opt/idea-server/venv/bin/python" -c 'import json; assert json.load(open("/tmp/idea-health.json"))["status"] == "healthy"'
echo "deployment-ok"

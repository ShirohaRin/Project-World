#!/usr/bin/env bash
set -euo pipefail

server_dir=/opt/idea-server/server
pid_file="$server_dir/idea-server.pid"
llm_secrets_file=/opt/idea-server/secrets/llm.env

test -s "$server_dir/.env.runtime"
test -s "$llm_secrets_file"

if [ -f "$pid_file" ]; then
    old_pid=$(cat "$pid_file")
    if kill -0 "$old_pid" 2>/dev/null; then
        kill -TERM "$old_pid"
        for _ in $(seq 1 10); do
            if ! kill -0 "$old_pid" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        if kill -0 "$old_pid"; then
            if [ -d "/proc/$old_pid" ]; then
                echo "old process did not stop" >&2
                exit 1
            fi
        fi
    fi
fi

cd "$server_dir"
nohup bash -c 'set -a; . /opt/idea-server/server/.env.runtime; . /opt/idea-server/secrets/llm.env; set +a; exec /opt/idea-server/venv/bin/python /opt/idea-server/server/main.py' >> logs/server.log 2>> logs/error.log < /dev/null &
echo "$!" > "$pid_file"
for _ in $(seq 1 10); do
    if curl -fsS --max-time 2 http://127.0.0.1:8900/health >/tmp/idea-health.json; then
        python3 -c 'import json; assert json.load(open("/tmp/idea-health.json"))["status"] == "healthy"'
        echo "restart-ok"
        exit 0
    fi
    sleep 1
done
echo "server health check failed" >&2
exit 1

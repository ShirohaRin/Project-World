#!/usr/bin/env bash
set -euo pipefail

server_dir=/opt/idea-server/server
pid_file="$server_dir/idea-server.pid"

test -s "$server_dir/.env.runtime"

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
        if kill -0 "$old_pid" 2>/dev/null; then
            echo "old process did not stop" >&2
            exit 1
        fi
    fi
fi

cd "$server_dir"
nohup bash -c 'set -a; . /opt/idea-server/server/.env.runtime; set +a; exec /opt/idea-server/venv/bin/python /opt/idea-server/server/main.py' >> logs/server.log 2>> logs/error.log < /dev/null &
echo "$!" > "$pid_file"
sleep 3
kill -0 "$(cat "$pid_file")"
curl -fsS --max-time 5 http://127.0.0.1:8900/health >/tmp/idea-health.json
python3 -c 'import json; assert json.load(open("/tmp/idea-health.json"))["status"] == "healthy"'
echo "restart-ok"

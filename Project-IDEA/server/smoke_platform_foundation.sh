#!/usr/bin/env bash
set -euo pipefail

server_dir=/opt/idea-server/server
set -a
. "$server_dir/.env.runtime"
set +a

unauthorized_code=$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8900/api/platform/me)
authenticated_body=$(curl -fsS -H "Authorization: Bearer $IDEA_AUTH_TOKEN" http://127.0.0.1:8900/api/platform/me)
spaces_body=$(curl -fsS -H "Authorization: Bearer $IDEA_AUTH_TOKEN" http://127.0.0.1:8900/api/platform/spaces)
audit_body=$(curl -fsS -H "Authorization: Bearer $IDEA_AUTH_TOKEN" "http://127.0.0.1:8900/api/platform/audit?limit=10")

python3 - "$unauthorized_code" "$authenticated_body" "$spaces_body" "$audit_body" <<'PY'
import json
import sys

unauthorized_code, authenticated_body, spaces_body, audit_body = sys.argv[1:]
assert unauthorized_code == "401"
assert json.loads(authenticated_body)["role"] == "owner"
assert json.loads(spaces_body)["spaces"]
assert json.loads(audit_body)["events"]
print("platform-smoke-ok")
PY

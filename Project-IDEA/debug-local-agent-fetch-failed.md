# Debug Session: local-agent-fetch-failed

Status: [OPEN]

## Symptom

Electron local Agent reports `fetch failed` when invoking `local:chat-stream`.

## Safety boundary

Do not record API keys, authorization headers, request bodies, response bodies, or credential values. Record only model ID, sanitized URL origin/path, reachability, status code, and error class.

## Hypotheses

1. The selected model endpoint is unreachable from the Electron main process.
2. The client is still attempting direct local model access although official models now use the server gateway.
3. The selected model has no stored credential; only presence metadata may be checked.
4. TLS, proxy, DNS, or local network policy blocks the endpoint.
5. Electron runtime request handling differs from the development environment.

## Evidence

Pending runtime reproduction.

## Fix

Pending evidence.

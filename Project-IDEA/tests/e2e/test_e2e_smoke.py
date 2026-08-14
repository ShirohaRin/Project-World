"""端到端冒烟测试：真实启动 server 进程，验证基础链路（服务可启动 + 鉴权闸门）。

默认跳过，避免干扰日常运行。启用方式：
    $env:IDEA_RUN_E2E = "1"; python -m pytest tests/e2e -v
或  pytest -m e2e tests/e2e（需先设置 IDEA_RUN_E2E=1，skipif 仍生效）。

验证内容：
    1. server 能在隔离端口/数据库上启动
    2. 未带 Bearer Token 访问受保护 API → 401
    3. 带正确 Token → 200
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

RUN_E2E = os.environ.get("IDEA_RUN_E2E", "") == "1"
SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
BASE_PORT = int(os.environ.get("IDEA_E2E_PORT", "8900"))


def _wait_for_port(port: int, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def _http_status(url: str, token: str | None) -> int:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


@pytest.mark.skipif(not RUN_E2E, reason="设置 IDEA_RUN_E2E=1 启用真实进程冒烟测试")
class TestE2eSmoke:
    @pytest.fixture()
    def server_env(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {
                **os.environ,
                "IDEA_AUTH_TOKEN": "e2e-smoke-token",
                "IDEA_PLATFORM_DB_PATH": str(Path(temp) / "platform.db"),
                "IDEA_AUTH_DEVELOPMENT_MODE": "true",
                "IDEA_SERVER_PORT": str(BASE_PORT),
            }
            yield env

    def test_server_boot_auth_gate_and_token_access(self, server_env):
        process = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=str(SERVER_DIR),
            env=server_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert _wait_for_port(BASE_PORT), "server 未在超时时间内启动"
            # 未带 token → 401（鉴权闸门生效）
            assert _http_status(f"http://127.0.0.1:{BASE_PORT}/api/platform/me", None) == 401
            # 带 token → 200（鉴权通过）
            assert _http_status(f"http://127.0.0.1:{BASE_PORT}/api/platform/me", "e2e-smoke-token") == 200
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

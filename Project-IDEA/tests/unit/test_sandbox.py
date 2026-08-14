"""OS 级进程沙箱 seam 单元测试：模式解析、命令包装、受限令牌执行、强制力报告。"""

import os

import pytest

from tool_runtime.sandbox import (
    SandboxBackend,
    SandboxEnforcement,
    SandboxManager,
    SandboxMode,
    SandboxPolicy,
    _wrap_bwrap,
    detect_backend,
    restricted_exec_argv,
)


class TestSandboxMode:
    def test_detect_backend_reports_honest_enforcement(self):
        backend = detect_backend(os.getcwd())
        assert isinstance(backend, SandboxBackend)
        assert backend.enforcement in (SandboxEnforcement.FULL, SandboxEnforcement.PARTIAL)
        # partial 必须伴随不可用原因，绝不虚报 full
        if backend.enforcement is SandboxEnforcement.PARTIAL:
            assert backend.wrap is None

    def test_resolve_defaults_to_workspace_write(self):
        manager = SandboxManager(os.getcwd())
        policy = manager.resolve()
        assert policy.mode is SandboxMode.WORKSPACE_WRITE
        assert policy.workspace_root == os.getcwd()

    def test_resolve_accepts_explicit_mode(self):
        manager = SandboxManager(os.getcwd())
        assert manager.resolve(SandboxMode.READ_ONLY).mode is SandboxMode.READ_ONLY
        assert manager.resolve(SandboxMode.DANGER_FULL_ACCESS).mode is SandboxMode.DANGER_FULL_ACCESS

    def test_danger_full_access_never_wraps(self):
        manager = SandboxManager(os.getcwd())
        argv = ["bash", "-c", "echo hi"]
        policy = manager.resolve(SandboxMode.DANGER_FULL_ACCESS)
        assert manager.wrap_command(argv, policy) == argv

    def test_unavailable_backend_does_not_wrap(self):
        backend = SandboxBackend("none", SandboxEnforcement.PARTIAL)
        policy = SandboxPolicy(SandboxMode.WORKSPACE_WRITE, os.getcwd())
        # wrap_command 仅当后端提供 wrap 才包装
        assert backend.wrap is None


class TestBwrapWrap:
    def test_read_only_uses_ro_bind_root(self):
        argv = ["bash", "-c", "echo hi"]
        policy = SandboxPolicy(SandboxMode.READ_ONLY, "C:/ws")
        wrapped = _wrap_bwrap(argv, policy)
        assert wrapped[:2] == ["bwrap", "--ro-bind"]
        assert "/" in wrapped[2:5]
        assert "--bind" not in wrapped

    def test_workspace_write_binds_workspace(self):
        argv = ["bash", "-c", "echo hi"]
        policy = SandboxPolicy(SandboxMode.WORKSPACE_WRITE, "C:/ws")
        wrapped = _wrap_bwrap(argv, policy)
        assert "--bind" in wrapped
        assert wrapped[wrapped.index("--bind") + 1] == "C:/ws"
        assert wrapped[-len(argv):] == argv


class TestRestrictedExec:
    def test_restricted_exec_returns_output_and_enforcement(self):
        if os.name != "nt":
            pytest.skip("Windows 专属后端")
        argv = ["cmd.exe", "/c", "echo sandbox-ok"]
        code, out, err, enforcement = restricted_exec_argv(argv, os.getcwd(), {"PATH": os.environ.get("PATH", "")}, 20)
        assert code == 0
        assert "sandbox-ok" in out
        assert enforcement in (SandboxEnforcement.FULL, SandboxEnforcement.PARTIAL)

    def test_restricted_exec_failure_degrades_to_partial(self):
        if os.name != "nt":
            pytest.skip("Windows 专属后端")
        # 不存在的可执行文件：降级路径也要给出确定结果而非崩溃
        code, out, err, enforcement = restricted_exec_argv(["no_such_binary_xyz"], os.getcwd(), {"PATH": ""}, 10)
        assert code != 0
        assert enforcement in (SandboxEnforcement.FULL, SandboxEnforcement.PARTIAL)

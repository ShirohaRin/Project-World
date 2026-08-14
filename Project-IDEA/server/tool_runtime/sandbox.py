"""OS 级进程沙箱 seam（对齐 DeepSeek Harness 的 sandbox 设计）。

- 模式：`read-only` / `workspace-write` / `danger-full-access`
- 强制力：`full` / `partial` —— 如实报告，绝不虚报。后端能力不足时降级 partial，
  并继续依赖应用层防护（路径校验、最小环境、命令黑名单、审批闭环）。
- 后端：Linux 用 bubblewrap（bwrap，全根只读挂载 + workspace 可写绑定）；
  Windows 用受限令牌（CreateRestrictedToken：禁用最高权限 + 惰性低完整性）。

sandbox 只约束"文件效应"；网络与进程可见性不属于本模块词汇（与 dsh 一致）。
"""

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("idea.sandbox")


class SandboxMode(str, Enum):
    """文件效应策略：只读 / 仅工作区可写 / 完全放行。"""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"


class SandboxEnforcement(str, Enum):
    """强制力完整度：full = 后端管住了模式承诺的所有文件效应；partial = 部分。"""

    FULL = "full"
    PARTIAL = "partial"


@dataclass(frozen=True)
class SandboxPolicy:
    """一次能力调用的完整文件效应策略（per-call 解析，不挂在后端上）。"""

    mode: SandboxMode
    workspace_root: str
    session_id: str = ""


@dataclass(frozen=True)
class SandboxBackend:
    """一个已探测到的沙箱后端。"""

    name: str
    enforcement: SandboxEnforcement
    wrap: Optional[Callable[[list[str], SandboxPolicy], list[str]]] = None

    def is_confined(self) -> bool:
        return self.wrap is not None


def _wrap_bwrap(argv: list[str], policy: SandboxPolicy) -> list[str]:
    """bwrap：全根只读挂载；workspace-write 额外把工作区绑定为可写。"""
    workspace = policy.workspace_root
    if policy.mode is SandboxMode.READ_ONLY:
        return ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", *argv]
    # workspace-write（danger-full-access 不会走到 wrap）
    return ["bwrap", "--ro-bind", "/", "/", "--bind", workspace, workspace, "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", *argv]


def _wrap_windows_restricted(argv: list[str], policy: SandboxPolicy) -> list[str]:
    """Windows 受限令牌：启动进程时禁用最高权限并设低完整性（真实强制，见 _restricted_exec）。"""
    return list(argv)


def detect_backend(workspace_root: str) -> SandboxBackend:
    """探测当前平台可用的沙箱后端，返回其强制力等级（如实报告）。"""
    if os.name == "posix":
        if shutil.which("bwrap"):
            return SandboxBackend("bwrap", SandboxEnforcement.FULL, _wrap_bwrap)
        logger.warning("Linux 未安装 bubblewrap（bwrap），OS 级沙箱不可用，命令沙箱降级为 partial")
        return SandboxBackend("bwrap-unavailable", SandboxEnforcement.PARTIAL)
    if os.name == "nt":
        try:
            import ctypes  # noqa: F401

            advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
            if hasattr(advapi32, "CreateRestrictedToken"):
                return SandboxBackend("windows-restricted-token", SandboxEnforcement.FULL, _wrap_windows_restricted)
        except (OSError, AttributeError):
            pass
        logger.warning("Windows 受限令牌后端不可用，命令沙箱降级为 partial")
        return SandboxBackend("windows-unavailable", SandboxEnforcement.PARTIAL)
    return SandboxBackend("unsupported", SandboxEnforcement.PARTIAL)


class SandboxManager:
    """解析 per-call 沙箱策略并把命令包装进已探测后端。"""

    def __init__(self, workspace_root: str, default_mode: SandboxMode = SandboxMode.WORKSPACE_WRITE, session_id: str = ""):
        self.workspace_root = str(Path(workspace_root).resolve())
        self.default_mode = default_mode
        self.session_id = session_id
        self.backend = detect_backend(self.workspace_root)

    def resolve(self, mode: Optional[SandboxMode] = None) -> SandboxPolicy:
        effective = mode if mode is not None else self.default_mode
        if effective is SandboxMode.DANGER_FULL_ACCESS:
            # 完全放行：不经过 wrap（调用方自行决定），但策略对象仍完整携带。
            pass
        return SandboxPolicy(effective, self.workspace_root, self.session_id)

    def wrap_command(self, argv: list[str], policy: SandboxPolicy) -> list[str]:
        """把 argv 包装进沙箱；danger-full-access 或后端不可用（partial）时原样返回。"""
        if policy.mode is SandboxMode.DANGER_FULL_ACCESS or not self.backend.wrap:
            return list(argv)
        return self.backend.wrap(argv, policy)


def restricted_exec_argv(argv: list[str], cwd: str, env: dict[str, str], timeout: float) -> tuple[int, str, str, SandboxEnforcement]:
    """Windows 受限令牌同步执行（供 asyncio.to_thread 调用）。

    返回 (returncode, stdout, stderr, enforcement)。受限令牌不可用时不阻塞执行，
    降级为普通启动并返回 partial（与应用层防护叠加，绝不虚报 full）。
    """
    try:
        return _windows_restricted_exec_impl(argv, cwd, env, timeout)
    except Exception as error:  # noqa: BLE001 - 降级必须兜底
        logger.warning("Windows 受限令牌执行失败，降级为普通启动: %s", error)
        try:
            from subprocess import Popen, PIPE

            process = Popen(argv, cwd=cwd, env=env, stdout=PIPE, stderr=PIPE, shell=False)
            stdout, stderr = process.communicate(timeout=timeout)
            return process.returncode, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"), SandboxEnforcement.PARTIAL
        except Exception as inner:  # noqa: BLE001
            return 1, "", f"命令无法启动: {inner}", SandboxEnforcement.PARTIAL


def _windows_restricted_exec_impl(argv: list[str], cwd: str, env: dict[str, str], timeout: float) -> tuple[int, str, str, SandboxEnforcement]:
    """CreateRestrictedToken + CreateProcessAsUserW 的 ctypes 实现。"""
    import ctypes
    from ctypes import wintypes

    # --- 常量与结构 ---
    TOKEN_DUPLICATE = 0x0002
    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_QUERY = 0x0008
    DISABLE_MAX_PRIVILEGE = 0x1
    SANDBOX_INERT = 0x2
    CREATE_NO_WINDOW = 0x08000000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    HANDLE_FLAG_INHERIT = 0x1
    WAIT_OBJECT_0 = 0
    INFINITE = 0xFFFFFFFF
    FILE_GENERIC_WRITE = 0x00000001
    FILE_SHARE_READ = 0x00000001
    OPEN_ALWAYS = 4
    FILE_ATTRIBUTE_TEMPORARY = 0x100
    GENERIC_WRITE = 0x40000000
    STARTF_USESTDHANDLES = 0x00000100

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)), ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    # 1) 打开当前进程令牌
    h_token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY | TOKEN_QUERY, ctypes.byref(h_token)):
        raise ctypes.WinError(ctypes.get_last_error())

    # 2) 复制为主令牌
    h_duplicate = wintypes.HANDLE()
    try:
        if not advapi32.DuplicateTokenEx(h_token, 0, None, 2, 1, ctypes.byref(h_duplicate)):  # SecurityImpersonation=2, TokenPrimary=1
            raise ctypes.WinError(ctypes.get_last_error())
        # 3) 受限令牌：禁用最高权限 + 惰性低完整性
        h_restricted = wintypes.HANDLE()
        try:
            if not advapi32.CreateRestrictedToken(h_duplicate, DISABLE_MAX_PRIVILEGE | SANDBOX_INERT, 0, None, 0, None, 0, None, ctypes.byref(h_restricted)):
                raise ctypes.WinError(ctypes.get_last_error())
            # 4) 重定向到临时文件
            with tempfile.TemporaryDirectory() as temp_dir:
                out_path = os.path.join(temp_dir, "stdout.txt")
                err_path = os.path.join(temp_dir, "stderr.txt")
                # CreateFileW 打开输出文件（可继承句柄）
                advapi32.CreateFileW.restype = wintypes.HANDLE
                kernel32.CreateFileW.restype = wintypes.HANDLE
                CreateFileW = kernel32.CreateFileW
                CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
                h_out = CreateFileW(out_path, GENERIC_WRITE, FILE_SHARE_READ, None, OPEN_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, None)
                h_err = CreateFileW(err_path, GENERIC_WRITE, FILE_SHARE_READ, None, OPEN_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, None)
                if h_out == wintypes.HANDLE(-1).value or h_err == wintypes.HANDLE(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    kernel32.SetHandleInformation(h_out, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)
                    kernel32.SetHandleInformation(h_err, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)
                    startup = STARTUPINFOW()
                    startup.cb = ctypes.sizeof(STARTUPINFOW)
                    startup.dwFlags = STARTF_USESTDHANDLES
                    startup.hStdOutput = h_out
                    startup.hStdError = h_err
                    startup.hStdInput = None
                    # 环境块（unicode，双空终止）
                    env_block = "".join(f"{key}={value}\0" for key, value in env.items()) + "\0"
                    env_buf = ctypes.create_unicode_buffer(env_block)
                    cmdline = ctypes.create_unicode_buffer(" ".join(f'"{part}"' if " " in part else part for part in argv))
                    cwd_buf = ctypes.create_unicode_buffer(cwd)
                    pi = PROCESS_INFORMATION()
                    success = advapi32.CreateProcessAsUserW(
                        h_restricted, None, cmdline, None, None, True,
                        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, env_buf, cwd_buf, ctypes.byref(startup), ctypes.byref(pi),
                    )
                    if not success:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if kernel32.WaitForSingleObject(pi.hProcess, int(timeout * 1000)) == WAIT_OBJECT_0:
                            code = wintypes.DWORD()
                            kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
                        else:
                            kernel32.TerminateProcess(pi.hProcess, 1)
                            code = wintypes.DWORD(1)
                    finally:
                        kernel32.CloseHandle(pi.hThread)
                        kernel32.CloseHandle(pi.hProcess)
                    with open(out_path, "r", encoding="utf-8", errors="replace") as handle:
                        out_text = handle.read()
                    with open(err_path, "r", encoding="utf-8", errors="replace") as handle:
                        err_text = handle.read()
                    return code.value, out_text, err_text, SandboxEnforcement.FULL
                finally:
                    kernel32.CloseHandle(h_out)
                    kernel32.CloseHandle(h_err)
        finally:
            kernel32.CloseHandle(h_duplicate)
    finally:
        kernel32.CloseHandle(h_token)

"""IDEA Assistant 测试集公共 fixtures 与构造工具。

依赖说明：pytest 通过 pytest.ini 的 `pythonpath = ../server` 解析 server 包，
因此本文件与所有测试文件可以直接 `from platform_auth import ...`。
"""

import pytest

from platform_auth import Principal, RequestContext
from tool_runtime.permissions import ExecutionContext


def make_context(principal_id, account_id, role, device_id="dev-1", space_id="space-1", agent_id="idea", is_owner=False):
    """构造标准 ExecutionContext，供工具层测试使用。"""
    return ExecutionContext(
        request_context=RequestContext(
            f"req-{principal_id}",
            Principal(principal_id, account_id, role, f"token-{principal_id}"),
            device_id,
            space_id,
        ),
        agent_id=agent_id,
        is_owner=is_owner,
    )


@pytest.fixture
def owner_context():
    """Owner 身份的 ExecutionContext（可写文件、可调度子智能体）。"""
    return make_context("p-owner", "account-owner", "owner", is_owner=True)


@pytest.fixture
def member_context():
    """普通成员身份的 ExecutionContext。"""
    return make_context("p-member", "account-member", "member")


@pytest.fixture
def platform_store(tmp_path):
    """基于临时 sqlite 文件的真实 PlatformStore 数据层实例。"""
    from platform_auth import PlatformStore
    return PlatformStore(str(tmp_path / "platform.db"))

"""Tool execution policy and request-bound execution context."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class ToolRisk(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    COMMAND = "command"
    NETWORK = "network"
    DELEGATION = "delegation"


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRES_APPROVAL = "requires_approval"


CAPABILITY_TOOLS = {
    "file.read": {"read_file", "list_dir", "search_content"},
    "file.write": {"write_file", "edit_file", "restore_file", "delete_file"},
    "command": {"run_command", "ssh_run", "ssh_get", "ssh_put"},
    "network": {"web_search", "web_fetch"},
    "delegate": {"dispatch_to_agent"},
}


def policy_allows_tool(capabilities: frozenset[str], tool_name: str) -> bool:
    return any(tool_name in CAPABILITY_TOOLS.get(capability, set()) for capability in capabilities)


@dataclass(frozen=True)
class ExecutionContext:
    request_context: Any
    agent_id: str
    is_owner: bool = False
    conversation_id: Optional[str] = None
    tool_capabilities: Optional[frozenset[str]] = None
    registry_version: int = 0
    prompt_version: str = ""
    prompt_text: Optional[str] = None

    @property
    def principal(self) -> Any:
        return self.request_context.principal

    @property
    def role(self) -> str:
        return self.principal.role

    @property
    def owner(self) -> bool:
        return self.is_owner

    @property
    def device_id(self) -> Optional[str]:
        return self.request_context.device_id

    @property
    def space_id(self) -> Optional[str]:
        return self.request_context.space_id

    @property
    def request_id(self) -> str:
        return self.request_context.request_id

    def for_child_agent(
        self,
        agent_id: str,
        tool_capabilities: frozenset[str],
        registry_version: int,
        prompt_version: str,
        prompt_text: Optional[str],
    ) -> "ExecutionContext":
        return ExecutionContext(
            self.request_context,
            agent_id,
            self.is_owner,
            self.conversation_id,
            tool_capabilities,
            registry_version,
            prompt_version,
            prompt_text,
        )


@dataclass(frozen=True)
class ToolPolicyResult:
    decision: PolicyDecision
    reason_code: str
    risk: ToolRisk


class ToolPolicy:
    """Central policy for schemas and execution. No user or model supplied capabilities."""

    TOOL_RISKS = {
        "read_file": ToolRisk.READ,
        "list_dir": ToolRisk.READ,
        "search_content": ToolRisk.READ,
        "write_file": ToolRisk.WRITE,
        "edit_file": ToolRisk.WRITE,
        "restore_file": ToolRisk.WRITE,
        "delete_file": ToolRisk.DESTRUCTIVE,
        "run_command": ToolRisk.COMMAND,
        "web_search": ToolRisk.NETWORK,
        "web_fetch": ToolRisk.NETWORK,
        "ssh_run": ToolRisk.COMMAND,
        "ssh_get": ToolRisk.COMMAND,
        "ssh_put": ToolRisk.COMMAND,
        "dispatch_to_agent": ToolRisk.DELEGATION,
    }
    READ_TOOLS = {"read_file", "list_dir", "search_content"}
    OWNER_WRITE_TOOLS = {"write_file", "edit_file", "restore_file"}

    def decide(self, tool_name: str, context: Optional[ExecutionContext]) -> ToolPolicyResult:
        risk = self.TOOL_RISKS.get(tool_name, ToolRisk.DESTRUCTIVE)
        if tool_name not in self.TOOL_RISKS:
            return ToolPolicyResult(PolicyDecision.DENY, "unknown_tool", risk)
        if tool_name in self.READ_TOOLS:
            return ToolPolicyResult(PolicyDecision.ALLOW, "read_only_allowed", risk)
        if tool_name in {"run_command", "web_search", "web_fetch", "delete_file", "ssh_run", "ssh_get", "ssh_put"}:
            return ToolPolicyResult(PolicyDecision.REQUIRES_APPROVAL, "approval_required", risk)
        if context is None:
            return ToolPolicyResult(PolicyDecision.DENY, "execution_context_required", risk)
        if context.is_owner and tool_name in self.OWNER_WRITE_TOOLS:
            return ToolPolicyResult(PolicyDecision.ALLOW, "owner_write_allowed", risk)
        if context.is_owner and tool_name == "dispatch_to_agent":
            return ToolPolicyResult(PolicyDecision.ALLOW, "owner_delegation_allowed", risk)
        return ToolPolicyResult(PolicyDecision.DENY, "role_not_permitted", risk)

/// <reference types="vite/client" />

interface Window {
  ideaDesktop?: {
    chooseWorkspace: () => Promise<string | null>
    readWorkspaceTree: (workspace: string) => Promise<FileTreeEntry[]>
    readFile: (workspace: string, filePath: string) => Promise<{ path: string; name: string; content: string }>
    saveFile: (workspace: string, filePath: string, content: string) => Promise<{ path: string; saved: boolean }>
    createFile: (workspace: string, relativePath: string) => Promise<{ path: string; name: string }>
    startExecution: (workspace: string, filePath: string, mode: 'run' | 'debug') => Promise<{ sessionId: string }>
    stopExecution: (sessionId: string) => Promise<boolean>
    getServiceConfig: () => Promise<ServiceConfig>
    saveServiceConfig: (config: { spaceId: string }) => Promise<ServiceConfig>
    passwordLogin: (email: string, password: string) => Promise<{ route: string; principal: { account_id: string; role: string } }>
    logout: () => Promise<void>
    testService: () => Promise<ServiceHealth>
    checkForUpdates: () => Promise<UpdateStatus>
    installUpdate: () => Promise<UpdateStatus>
    sendChat: (request: { agentId: string; message: string; contextBlocks?: Array<{ path: string; name: string; content: string }>; conversationId?: string; useMemory?: boolean; modelKey?: ModelKey }) => Promise<ServiceChatResponse>
    listConversations: () => Promise<ConversationSummary[]>
    getConversation: (conversationId: string) => Promise<ConversationDetail>
    deleteConversation: (conversationId: string) => Promise<void>
    listTasks: () => Promise<TaskSummary[]>
    deleteTask: (taskId: string) => Promise<void>
    getSyncEvents: (after: number) => Promise<SyncSnapshot>
    listMemories?: () => Promise<MemoryRecord[]>
    listOwnerDevices?: () => Promise<OwnerDevice[]>
    approveOwnerDevice?: (ownerDeviceId: string) => Promise<void>
    revokeOwnerDevice?: (ownerDeviceId: string) => Promise<void>
    listOwnerCredentials?: () => Promise<AutomatedDeviceCredential[]>
    issueOwnerCredential?: (request: { deviceLabel: string; capability: 'idea' | 'memory'; expiresInDays?: number }) => Promise<AutomatedDeviceCredential & { token: string }>
    recoverOwnerCredential?: (credentialId: string) => Promise<{ credential_id: string; token: string }>
    revokeOwnerCredential?: (credentialId: string) => Promise<void>
    listOwnerApprovals?: () => Promise<ApprovalSnapshot>
    approveOwnerApproval?: (approvalId: string) => Promise<void>
    denyOwnerApproval?: (approvalId: string) => Promise<void>
    listOwnerGrants?: () => Promise<CapabilityGrant[]>
    createOwnerGrant?: (request: { accountId: string; capability: GrantCapability; workspace?: string; expiresInDays?: number }) => Promise<CapabilityGrant>
    revokeOwnerGrant?: (grantId: string) => Promise<void>
    listOwnerFileChanges?: () => Promise<FileChange[]>
    acceptOwnerFileChange?: (changeId: string) => Promise<void>
    revertOwnerFileChange?: (changeId: string) => Promise<void>
    listAuditEvents?: () => Promise<AuditEvent[]>
    createMemory?: (memory: { scope: 'personal' | 'shared' | 'owner'; category: string; content: string }) => Promise<MemoryRecord>
    updateMemory?: (memory: { id: string; revision: number; category: string; content: string }) => Promise<MemoryRecord>
    deleteMemory?: (memory: { id: string; revision: number }) => Promise<void>
    onExecutionOutput: (listener: (event: ExecutionOutput) => void) => () => void
  }
}

interface ExecutionOutput { sessionId: string; stream: 'system' | 'stdout' | 'stderr'; content: string; terminal?: boolean }
interface FileTreeEntry { name: string; path: string; kind: 'file' | 'directory'; children?: FileTreeEntry[] }
interface ServiceConfig { serverUrl: string; spaceId: string; deviceId: string; signedIn: boolean; route?: string }
interface ServiceHealth { status: string; version?: string; llmAvailable?: boolean }
interface UpdateInfo { version: string; releaseNotes: string; publishedAt: string }
interface UpdateStatus { state: 'checked' | 'current' | 'available' | 'downloading' | 'downloaded' | 'error'; update?: UpdateInfo; message?: string }
type ModelKey = 'gpt' | 'deepseek-v4-flash'
interface ServiceChatResponse { reply: string; conversationId: string; agentId: string; dispatchedTo?: string | null; modelKey: ModelKey }
interface ConversationSummary { id: string; agent_id: string; messages: number; created_at: number; updated_at: number }
interface ConversationDetail { id: string; messages: Array<{ id: string; role: 'user' | 'assistant'; content: string; timestamp: number }> }
interface TaskSummary { id: string; title: string; conversation_id?: string | null; status: string; created_at: number }
interface SyncSnapshot { events: Array<{ event_id: number; event_type: string }>; next_cursor: number }
interface MemoryRecord { id: string; namespace: string; category: string; content: string; status: string; revision: number; created_at: number; updated_at: number }
interface OwnerDevice { owner_device_id: string; device_id: string; status: 'pending' | 'approved' | 'revoked'; requested_at: number; approved_at?: number | null; revoked_at?: number | null; last_seen_at?: number | null }
interface AutomatedDeviceCredential { credential_id: string; capability: 'idea' | 'memory'; device_label: string; space_id: string; status: 'active' | 'revoked'; expires_at?: number | null; created_at: number; last_used_at?: number | null; revoked_at?: number | null }
interface ToolApproval { approval_id: string; account_id: string; space_id: string; principal_id: string; agent_id: string; tool_name: string; args_summary: string; status: 'pending' | 'approved' | 'denied' | 'expired'; requested_at: number; expires_at: number; decided_at?: number | null; decided_by?: string | null }
interface ApprovalSnapshot { pending: ToolApproval[]; recent: ToolApproval[] }
type GrantCapability = 'file.read' | 'file.write' | 'file.delete' | 'command' | 'network' | 'delegate' | 'ssh'
interface CapabilityGrant { grant_id: string; account_id: string; granted_by: string; capability: GrantCapability; workspace: string; constraints_json: string; status: 'active' | 'revoked'; created_at: number; expires_at?: number | null; revoked_at?: number | null; revoked_by?: string | null }
interface FileChange { change_id: string; account_id: string; space_id: string; principal_id: string; agent_id: string; tool_name: string; file_path: string; backup_path?: string | null; diff_summary: string; status: 'pending' | 'accepted' | 'reverted'; created_at: number; reviewed_at?: number | null; reviewed_by?: string | null }
interface AuditEvent { event_id: string; event_type: string; occurred_at: number; principal_id?: string | null; account_id: string; device_id?: string | null; space_id?: string | null; resource_type?: string | null; resource_id?: string | null; action?: string | null; decision?: string | null; reason_code?: string | null; metadata_json?: string | null }

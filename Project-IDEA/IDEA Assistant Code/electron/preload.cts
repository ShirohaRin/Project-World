import { contextBridge, ipcRenderer } from 'electron'

type ExecutionEvent = { sessionId: string; stream: 'system' | 'stdout' | 'stderr'; content: string; terminal?: boolean }
type UpdateStatus = { state: 'checked' | 'current' | 'available' | 'downloading' | 'downloaded' | 'error'; update?: { version: string; releaseNotes: string; publishedAt: string }; message?: string }
type ChatStreamEvent = { type: 'run.started' | 'model.text.delta' | 'tool.started' | 'tool.completed' | 'run.completed' | 'run.failed'; payload: Record<string, unknown> }
// sandbox 模式下 preload 无 process.env 访问权，以 additionalArguments 传入的 argv 为准。
const isOwnerClient = typeof process.env?.IDEA_CLIENT_FLAVOR === 'string' ? process.env.IDEA_CLIENT_FLAVOR === 'owner' : process.argv.includes('--idea-owner-client')

contextBridge.exposeInMainWorld('ideaDesktop', {
  chooseWorkspace: (): Promise<string | null> => ipcRenderer.invoke('workspace:choose'),
  readWorkspaceTree: (workspace: string) => ipcRenderer.invoke('workspace:tree', workspace),
  readFile: (workspace: string, filePath: string) => ipcRenderer.invoke('file:read', workspace, filePath),
  saveFile: (workspace: string, filePath: string, content: string) => ipcRenderer.invoke('file:save', workspace, filePath, content),
  createFile: (workspace: string, relativePath: string) => ipcRenderer.invoke('file:create', workspace, relativePath),
  startExecution: (workspace: string, filePath: string, mode: 'run' | 'debug') => ipcRenderer.invoke('execution:start', workspace, filePath, mode),
  stopExecution: (sessionId: string) => ipcRenderer.invoke('execution:stop', sessionId),
  getServiceConfig: () => ipcRenderer.invoke('service:config'),
  saveServiceConfig: (config: { spaceId: string }) => ipcRenderer.invoke('service:save-config', config),
  passwordLogin: (email: string, password: string): Promise<{ route: string; principal: { account_id: string; role: string } }> => ipcRenderer.invoke('service:password-login', email, password),
  logout: (): Promise<void> => ipcRenderer.invoke('service:logout'),
  testService: () => ipcRenderer.invoke('service:health'),
  getNekoRuntime: () => ipcRenderer.invoke('neko:runtime'),
  getRagRuntime: () => ipcRenderer.invoke('rag:runtime'),
  getRagStats: () => ipcRenderer.invoke('rag:stats'),
  getRagDocuments: () => ipcRenderer.invoke('rag:documents'),
  ragSearch: (collection: string, query: string, topK?: number) => ipcRenderer.invoke('rag:search', collection, query, topK),
  ragIngest: (collection: string) => ipcRenderer.invoke('rag:ingest', collection),
  ragRebuild: (collection?: string) => ipcRenderer.invoke('rag:rebuild', collection ?? 'all'),
  checkForUpdates: (): Promise<UpdateStatus> => ipcRenderer.invoke('updates:check'),
  installUpdate: (): Promise<UpdateStatus> => ipcRenderer.invoke('updates:install'),
  sendChat: (request: { agentId: string; message: string; contextBlocks?: Array<{ path: string; name: string; content: string }>; conversationId?: string; useMemory?: boolean; modelKey?: 'gpt' | 'deepseek-v4-flash' }) => ipcRenderer.invoke('service:chat', request),
  sendChatStream: (request: { agentId: string; message: string; contextBlocks?: Array<{ path: string; name: string; content: string }>; conversationId?: string; useMemory?: boolean; modelKey?: 'gpt' | 'deepseek-v4-flash' }) => ipcRenderer.invoke('service:chat-stream', request),
  listConversations: () => ipcRenderer.invoke('service:conversations'),
  getConversation: (conversationId: string) => ipcRenderer.invoke('service:conversation', conversationId),
  deleteConversation: (conversationId: string): Promise<void> => ipcRenderer.invoke('service:delete-conversation', conversationId),
  listTasks: () => ipcRenderer.invoke('service:tasks'),
  deleteTask: (taskId: string): Promise<void> => ipcRenderer.invoke('service:delete-task', taskId),
  getSyncEvents: (after: number) => ipcRenderer.invoke('service:sync-events', after),
  getRuntimeSnapshot: () => ipcRenderer.invoke('service:runtime-snapshot'),
  registerRuntime: () => ipcRenderer.invoke('service:register-runtime'),
  heartbeatRuntime: () => ipcRenderer.invoke('service:heartbeat-runtime'),
  listPendingHandoffs: () => ipcRenderer.invoke('service:pending-handoffs'),
  executeHandoff: (handoffId: string, workspace: string) => ipcRenderer.invoke('service:execute-handoff', handoffId, workspace),
  listRuns: () => ipcRenderer.invoke('service:runs'),
  getRunDetail: (runId: string) => ipcRenderer.invoke('service:run-detail', runId),
  ...(isOwnerClient ? {
    listOwnerDevices: () => ipcRenderer.invoke('owner:devices'),
    approveOwnerDevice: (ownerDeviceId: string) => ipcRenderer.invoke('owner:approve-device', ownerDeviceId),
    revokeOwnerDevice: (ownerDeviceId: string) => ipcRenderer.invoke('owner:revoke-device', ownerDeviceId),
    listOwnerCredentials: () => ipcRenderer.invoke('owner:credentials'),
    issueOwnerCredential: (request: { deviceLabel: string; capability: 'idea' | 'memory'; expiresInDays?: number }) => ipcRenderer.invoke('owner:issue-credential', request),
    recoverOwnerCredential: (credentialId: string) => ipcRenderer.invoke('owner:recover-credential', credentialId),
    revokeOwnerCredential: (credentialId: string) => ipcRenderer.invoke('owner:revoke-credential', credentialId),
    listOwnerApprovals: () => ipcRenderer.invoke('owner:approvals'),
    approveOwnerApproval: (approvalId: string) => ipcRenderer.invoke('owner:approve-approval', approvalId),
    denyOwnerApproval: (approvalId: string) => ipcRenderer.invoke('owner:deny-approval', approvalId),
    listOwnerGrants: () => ipcRenderer.invoke('owner:grants'),
    createOwnerGrant: (request: { accountId: string; capability: 'file.read' | 'file.write' | 'file.delete' | 'command' | 'network' | 'delegate' | 'ssh'; workspace?: string; expiresInDays?: number }) => ipcRenderer.invoke('owner:create-grant', request),
    revokeOwnerGrant: (grantId: string) => ipcRenderer.invoke('owner:revoke-grant', grantId),
    listOwnerFileChanges: () => ipcRenderer.invoke('owner:file-changes'),
    acceptOwnerFileChange: (changeId: string) => ipcRenderer.invoke('owner:accept-change', changeId),
    revertOwnerFileChange: (changeId: string) => ipcRenderer.invoke('owner:revert-change', changeId),
    listAuditEvents: () => ipcRenderer.invoke('owner:audit'),
    listMemories: () => ipcRenderer.invoke('service:memories'),
    createMemory: (memory: { scope: 'personal' | 'shared' | 'owner'; category: string; content: string }) => ipcRenderer.invoke('service:create-memory', memory),
    updateMemory: (memory: { id: string; revision: number; category: string; content: string }) => ipcRenderer.invoke('service:update-memory', memory),
    deleteMemory: (memory: { id: string; revision: number }) => ipcRenderer.invoke('service:delete-memory', memory),
  } : {}),
  onChatStreamEvent: (listener: (event: ChatStreamEvent) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, streamEvent: ChatStreamEvent) => listener(streamEvent)
    ipcRenderer.on('service:chat-event', handler)
    return () => ipcRenderer.removeListener('service:chat-event', handler)
  },
  onExecutionOutput: (listener: (event: ExecutionEvent) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, output: ExecutionEvent) => listener(output)
    ipcRenderer.on('execution:output', handler)
    return () => ipcRenderer.removeListener('execution:output', handler)
  },
})

import { contextBridge, ipcRenderer } from 'electron'

type ExecutionEvent = { sessionId: string; stream: 'system' | 'stdout' | 'stderr'; content: string; terminal?: boolean }
type UpdateStatus = { state: 'checked' | 'current' | 'available' | 'downloading' | 'downloaded' | 'error'; update?: { version: string; releaseNotes: string; publishedAt: string }; message?: string }
const isOwnerClient = process.env.IDEA_CLIENT_FLAVOR === 'owner' || process.argv.includes('--idea-owner-client')

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
  checkForUpdates: (): Promise<UpdateStatus> => ipcRenderer.invoke('updates:check'),
  installUpdate: (): Promise<UpdateStatus> => ipcRenderer.invoke('updates:install'),
  sendChat: (request: { agentId: string; message: string; conversationId?: string; useMemory?: boolean; modelKey?: 'gpt' | 'deepseek-v4-flash' }) => ipcRenderer.invoke('service:chat', request),
  listConversations: () => ipcRenderer.invoke('service:conversations'),
  getConversation: (conversationId: string) => ipcRenderer.invoke('service:conversation', conversationId),
  listTasks: () => ipcRenderer.invoke('service:tasks'),
  getSyncEvents: (after: number) => ipcRenderer.invoke('service:sync-events', after),
  ...(isOwnerClient ? {
    listOwnerDevices: () => ipcRenderer.invoke('owner:devices'),
    approveOwnerDevice: (ownerDeviceId: string) => ipcRenderer.invoke('owner:approve-device', ownerDeviceId),
    revokeOwnerDevice: (ownerDeviceId: string) => ipcRenderer.invoke('owner:revoke-device', ownerDeviceId),
    listOwnerCredentials: () => ipcRenderer.invoke('owner:credentials'),
    issueOwnerCredential: (request: { deviceLabel: string; capability: 'idea' | 'memory'; expiresInDays?: number }) => ipcRenderer.invoke('owner:issue-credential', request),
    revokeOwnerCredential: (credentialId: string) => ipcRenderer.invoke('owner:revoke-credential', credentialId),
    listMemories: () => ipcRenderer.invoke('service:memories'),
    createMemory: (memory: { scope: 'personal' | 'shared' | 'owner'; category: string; content: string }) => ipcRenderer.invoke('service:create-memory', memory),
    deleteMemory: (memory: { id: string; revision: number }) => ipcRenderer.invoke('service:delete-memory', memory),
  } : {}),
  onExecutionOutput: (listener: (event: ExecutionEvent) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, output: ExecutionEvent) => listener(output)
    ipcRenderer.on('execution:output', handler)
    return () => ipcRenderer.removeListener('execution:output', handler)
  },
})

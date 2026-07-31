import { contextBridge, ipcRenderer } from 'electron'

type ExecutionEvent = { sessionId: string; stream: 'system' | 'stdout' | 'stderr'; content: string }

contextBridge.exposeInMainWorld('ideaDesktop', {
  chooseWorkspace: (): Promise<string | null> => ipcRenderer.invoke('workspace:choose'),
  readWorkspaceTree: (workspace: string) => ipcRenderer.invoke('workspace:tree', workspace),
  readFile: (workspace: string, filePath: string) => ipcRenderer.invoke('file:read', workspace, filePath),
  saveFile: (workspace: string, filePath: string, content: string) => ipcRenderer.invoke('file:save', workspace, filePath, content),
  startExecution: (workspace: string, filePath: string, mode: 'run' | 'debug') => ipcRenderer.invoke('execution:start', workspace, filePath, mode),
  stopExecution: (sessionId: string) => ipcRenderer.invoke('execution:stop', sessionId),
  getServiceConfig: () => ipcRenderer.invoke('service:config'),
  saveServiceConfig: (config: { serverUrl: string; spaceId: string }) => ipcRenderer.invoke('service:save-config', config),
  sendEmailCode: (email: string): Promise<void> => ipcRenderer.invoke('service:send-email-code', email),
  verifyEmailCode: (email: string, code: string): Promise<{ route: string; principal: { account_id: string; role: string } }> => ipcRenderer.invoke('service:verify-email-code', email, code),
  logout: (): Promise<void> => ipcRenderer.invoke('service:logout'),
  testService: () => ipcRenderer.invoke('service:health'),
  sendChat: (request: { agentId: string; message: string; conversationId?: string }) => ipcRenderer.invoke('service:chat', request),
  onExecutionOutput: (listener: (event: ExecutionEvent) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, output: ExecutionEvent) => listener(output)
    ipcRenderer.on('execution:output', handler)
    return () => ipcRenderer.removeListener('execution:output', handler)
  },
})

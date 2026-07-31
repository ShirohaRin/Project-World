/// <reference types="vite/client" />

interface Window {
  ideaDesktop?: {
    chooseWorkspace: () => Promise<string | null>
    readWorkspaceTree: (workspace: string) => Promise<FileTreeEntry[]>
    readFile: (workspace: string, filePath: string) => Promise<{ path: string; name: string; content: string }>
    saveFile: (workspace: string, filePath: string, content: string) => Promise<{ path: string; saved: boolean }>
    startExecution: (workspace: string, filePath: string, mode: 'run' | 'debug') => Promise<{ sessionId: string }>
    stopExecution: (sessionId: string) => Promise<boolean>
    getServiceConfig: () => Promise<ServiceConfig>
    saveServiceConfig: (config: { serverUrl: string; spaceId: string }) => Promise<ServiceConfig>
    sendEmailCode: (email: string) => Promise<void>
    verifyEmailCode: (email: string, code: string) => Promise<{ route: string; principal: { account_id: string; role: string } }>
    logout: () => Promise<void>
    testService: () => Promise<ServiceHealth>
    sendChat: (request: { agentId: string; message: string; conversationId?: string }) => Promise<ServiceChatResponse>
    onExecutionOutput: (listener: (event: ExecutionOutput) => void) => () => void
  }
}

interface ExecutionOutput {
  sessionId: string
  stream: 'system' | 'stdout' | 'stderr'
  content: string
}

interface FileTreeEntry {
  name: string
  path: string
  kind: 'file' | 'directory'
  children?: FileTreeEntry[]
}

interface ServiceConfig {
  serverUrl: string
  spaceId: string
  deviceId: string
  signedIn: boolean
  route?: string
}

interface ServiceHealth {
  status: string
  version?: string
  llmAvailable?: boolean
}

interface ServiceChatResponse {
  reply: string
  conversationId: string
  agentId: string
  dispatchedTo?: string | null
}

import { app, BrowserWindow, dialog, ipcMain, safeStorage } from 'electron'
import { mkdir, readdir, readFile, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { basename, dirname, extname, join, parse, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const currentDir = fileURLToPath(new URL('.', import.meta.url))
const isDevelopment = !app.isPackaged

type FileEntry = {
  name: string
  path: string
  kind: 'file' | 'directory'
  children?: FileEntry[]
}

const TEXT_EXTENSIONS = new Set(['.md', '.mdx', '.txt', '.json', '.yaml', '.yml', '.ts', '.tsx', '.js', '.jsx', '.css', '.html', '.py', '.c', '.cc', '.cpp', '.cxx', '.go', '.java', '.rs'])
const SKIPPED_DIRECTORIES = new Set(['.git', 'node_modules', 'dist', 'dist-electron', 'release', '__pycache__'])
const RUN_TIMEOUT_MS = 60_000
const MAX_OUTPUT_LENGTH = 200_000
const activeRuns = new Map<string, ChildProcessWithoutNullStreams>()

type RunMode = 'run' | 'debug'
type RunEvent = { sessionId: string; stream: 'system' | 'stdout' | 'stderr'; content: string }
type ServiceConfig = { serverUrl: string; spaceId: string; deviceId: string; signedIn: boolean; route?: string }
type StoredServiceConfig = ServiceConfig & { encryptedAccessToken?: string; encryptedRefreshToken?: string }
type ServiceHealth = { status: string; version?: string; llmAvailable?: boolean }
type ChatResponse = { reply: string; conversationId: string; agentId: string; dispatchedTo?: string | null }
type LoginResponse = { route: string; principal: { account_id: string; role: string } }
type ConversationSummary = { id: string; agent_id: string; messages: number; created_at: number; updated_at: number }
type ConversationDetail = { id: string; messages: Array<{ id: string; role: 'user' | 'assistant'; content: string; timestamp: number }> }
type TaskSummary = { id: string; title: string; conversation_id?: string | null; status: string; created_at: number }
type SyncSnapshot = { events: Array<{ event_id: number; event_type: string }>; next_cursor: number }
type MemoryScope = 'personal' | 'space' | 'owner'
type MemoryRecord = { id: string; namespace: string; category: string; content: string; status: string; created_at: number; updated_at: number }

function serviceConfigPath(): string {
  return join(app.getPath('userData'), 'service-config.json')
}

function createDeviceId(): string {
  return `desktop-${crypto.randomUUID()}`
}

function normalizeServiceUrl(value: string): string {
  const url = new URL(value.trim())
  const isLoopback = url.hostname === 'localhost' || url.hostname === '127.0.0.1' || url.hostname === '[::1]'
  if (url.protocol !== 'https:' && !(isDevelopment && isLoopback && url.protocol === 'http:')) throw new Error('生产环境服务地址必须使用 HTTPS')
  if (!isDevelopment && url.hostname !== 'shiroha-rin.world') throw new Error('生产环境仅允许连接受信 IDEA 服务地址')
  return url.toString().replace(/\/$/, '')
}

async function loadServiceConfig(): Promise<StoredServiceConfig> {
  try {
    const raw = await readFile(serviceConfigPath(), 'utf8')
    const stored = JSON.parse(raw) as Partial<StoredServiceConfig>
    return {
      serverUrl: typeof stored.serverUrl === 'string' ? stored.serverUrl : '',
      spaceId: typeof stored.spaceId === 'string' ? stored.spaceId : '',
      deviceId: typeof stored.deviceId === 'string' && stored.deviceId ? stored.deviceId : createDeviceId(),
      signedIn: Boolean(stored.encryptedRefreshToken),
      route: typeof stored.route === 'string' ? stored.route : undefined,
      encryptedAccessToken: typeof stored.encryptedAccessToken === 'string' ? stored.encryptedAccessToken : undefined,
      encryptedRefreshToken: typeof stored.encryptedRefreshToken === 'string' ? stored.encryptedRefreshToken : undefined,
    }
  } catch {
    return { serverUrl: '', spaceId: '', deviceId: createDeviceId(), signedIn: false }
  }
}

function publicServiceConfig(config: StoredServiceConfig): ServiceConfig {
  return { serverUrl: config.serverUrl, spaceId: config.spaceId, deviceId: config.deviceId, signedIn: Boolean(config.encryptedRefreshToken), route: config.route }
}

async function saveServiceConfig(config: { serverUrl: string; spaceId: string }): Promise<ServiceConfig> {
  const current = await loadServiceConfig()
  const serverUrl = config.serverUrl.trim() ? normalizeServiceUrl(config.serverUrl) : ''
  const stored: StoredServiceConfig = {
    serverUrl,
    spaceId: config.spaceId.trim(),
    deviceId: current.deviceId,
    signedIn: Boolean(current.encryptedRefreshToken),
    route: current.route,
    encryptedAccessToken: current.encryptedAccessToken,
    encryptedRefreshToken: current.encryptedRefreshToken,
  }
  await writeFile(serviceConfigPath(), JSON.stringify(stored), 'utf8')
  return publicServiceConfig(stored)
}

function decryptSecret(value: string | undefined): string {
  if (!value || !safeStorage.isEncryptionAvailable()) throw new Error('请先完成账号登录')
  return safeStorage.decryptString(Buffer.from(value, 'base64'))
}

async function saveLoginSession(config: StoredServiceConfig, payload: { access_token: string; refresh_token: string; route: string }): Promise<ServiceConfig> {
  if (!safeStorage.isEncryptionAvailable()) throw new Error('当前系统无法安全保存登录会话')
  const stored: StoredServiceConfig = {
    ...config,
    signedIn: true,
    route: payload.route,
    encryptedAccessToken: safeStorage.encryptString(payload.access_token).toString('base64'),
    encryptedRefreshToken: safeStorage.encryptString(payload.refresh_token).toString('base64'),
  }
  await writeFile(serviceConfigPath(), JSON.stringify(stored), 'utf8')
  return publicServiceConfig(stored)
}

async function refreshAccessToken(config: StoredServiceConfig): Promise<StoredServiceConfig> {
  const refreshToken = decryptSecret(config.encryptedRefreshToken)
  const response = await fetch(`${config.serverUrl}/api/auth/refresh`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Device-ID': config.deviceId }, body: JSON.stringify({ refresh_token: refreshToken }) })
  const body = await response.json().catch(() => ({})) as { detail?: string; access_token?: string; refresh_token?: string; route?: string }
  if (!response.ok || !body.access_token || !body.refresh_token || !body.route) throw new Error(body.detail || '登录会话已失效，请重新登录')
  await saveLoginSession(config, { access_token: body.access_token, refresh_token: body.refresh_token, route: body.route })
  return loadServiceConfig()
}

async function serviceRequest(path: string, init: RequestInit = {}, requireAuthentication = false, retried = false): Promise<Response> {
  const config = await loadServiceConfig()
  if (!config.serverUrl) throw new Error('请先配置 IDEA 服务地址')
  const headers = new Headers(init.headers)
  headers.set('X-Device-ID', config.deviceId)
  if (config.spaceId) headers.set('X-Space-ID', config.spaceId)
  if (requireAuthentication) headers.set('Authorization', `Bearer ${decryptSecret(config.encryptedAccessToken)}`)
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 15_000)
  try {
    const response = await fetch(`${config.serverUrl}${path}`, { ...init, headers, signal: controller.signal })
    if (requireAuthentication && response.status === 401 && !retried) {
      await refreshAccessToken(config)
      return serviceRequest(path, init, requireAuthentication, true)
    }
    return response
  } catch {
    throw new Error('无法连接 IDEA 服务')
  } finally {
    clearTimeout(timeout)
  }
}

async function sendEmailCode(email: string): Promise<void> {
  const response = await serviceRequest('/api/auth/email/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }) })
  const body = await response.json().catch(() => ({})) as { detail?: string }
  if (!response.ok) throw new Error(body.detail || '验证码发送失败')
}

async function verifyEmailCode(email: string, code: string): Promise<LoginResponse> {
  const config = await loadServiceConfig()
  const response = await serviceRequest('/api/auth/email/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, code }) })
  const body = await response.json().catch(() => ({})) as { detail?: string; access_token?: string; refresh_token?: string; route?: string; principal?: { account_id: string; role: string } }
  if (!response.ok || !body.access_token || !body.refresh_token || !body.route || !body.principal) throw new Error(body.detail || '登录失败')
  await saveLoginSession(config, { access_token: body.access_token, refresh_token: body.refresh_token, route: body.route })
  return { route: body.route, principal: body.principal }
}

async function logout(): Promise<void> {
  const config = await loadServiceConfig()
  if (config.serverUrl && config.encryptedRefreshToken) {
    await serviceRequest('/api/auth/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: decryptSecret(config.encryptedRefreshToken) }) }, true).catch(() => undefined)
  }
  await writeFile(serviceConfigPath(), JSON.stringify({ serverUrl: config.serverUrl, spaceId: config.spaceId, deviceId: config.deviceId, signedIn: false }), 'utf8')
}

function assertWithinWorkspace(workspace: string, target: string): string {
  const workspacePath = resolve(workspace)
  const targetPath = resolve(target)
  const pathFromWorkspace = relative(workspacePath, targetPath)
  if (pathFromWorkspace.startsWith('..') || pathFromWorkspace === '' && targetPath !== workspacePath) {
    throw new Error('文件路径不在当前工作区内')
  }
  return targetPath
}

function createRunId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function runCommand(command: string, args: string[], cwd: string, sessionId: string, send: (event: RunEvent) => void): Promise<number> {
  return new Promise((resolveRun, rejectRun) => {
    let outputLength = 0
    const child = spawn(command, args, { cwd, shell: false, windowsHide: true, env: process.env })
    activeRuns.set(sessionId, child)
    const timeout = setTimeout(() => {
      send({ sessionId, stream: 'system', content: `执行超过 ${RUN_TIMEOUT_MS / 1000} 秒，已停止。\n` })
      child.kill()
    }, RUN_TIMEOUT_MS)
    const emit = (stream: 'stdout' | 'stderr', chunk: Buffer) => {
      if (outputLength >= MAX_OUTPUT_LENGTH) return
      const content = chunk.toString().slice(0, MAX_OUTPUT_LENGTH - outputLength)
      outputLength += content.length
      send({ sessionId, stream, content })
    }
    child.stdout.on('data', (chunk: Buffer) => emit('stdout', chunk))
    child.stderr.on('data', (chunk: Buffer) => emit('stderr', chunk))
    child.once('error', (error) => {
      clearTimeout(timeout)
      activeRuns.delete(sessionId)
      rejectRun(error)
    })
    child.once('close', (code) => {
      clearTimeout(timeout)
      activeRuns.delete(sessionId)
      resolveRun(code ?? -1)
    })
  })
}

async function executeFile(workspace: string, filePath: string, mode: RunMode, sessionId: string, send: (event: RunEvent) => void): Promise<void> {
  const sourcePath = assertWithinWorkspace(workspace, filePath)
  const extension = extname(sourcePath).toLowerCase()
  const sourceDirectory = dirname(sourcePath)
  const fileName = basename(sourcePath)
  const baseName = parse(sourcePath).name
  const buildDirectory = join(workspace, '.idea-assistant', 'build')
  const buildOutput = join(buildDirectory, `${baseName}.exe`)
  await mkdir(buildDirectory, { recursive: true })
  const run = (command: string, args: string[], cwd = sourceDirectory) => runCommand(command, args, cwd, sessionId, send)
  const compileAndRun = async (command: string, compileArgs: string[], runCommandName: string, runArgs: string[]) => {
    send({ sessionId, stream: 'system', content: `$ ${command} ${compileArgs.join(' ')}\n` })
    const compileCode = await run(command, compileArgs)
    if (compileCode !== 0) throw new Error(`编译失败，退出码 ${compileCode}`)
    send({ sessionId, stream: 'system', content: `$ ${runCommandName} ${runArgs.join(' ')}\n` })
    const runCode = await run(runCommandName, runArgs)
    if (runCode !== 0) throw new Error(`运行结束，退出码 ${runCode}`)
  }

  if (mode === 'debug' && extension !== '.js' && extension !== '.mjs' && extension !== '.cjs') {
    throw new Error('当前语言的断点调试需要独立 Debug Adapter，第一版仅支持 Node.js Inspector 调试。')
  }
  if (extension === '.js' || extension === '.mjs' || extension === '.cjs') {
    const args = mode === 'debug' ? ['--inspect-brk', sourcePath] : [sourcePath]
    send({ sessionId, stream: 'system', content: `$ node ${args.join(' ')}\n` })
    const code = await run('node', args)
    if (code !== 0) throw new Error(`Node.js 结束，退出码 ${code}`)
    return
  }
  if (extension === '.py') {
    send({ sessionId, stream: 'system', content: `$ python ${sourcePath}\n` })
    const code = await run('python', [sourcePath])
    if (code !== 0) throw new Error(`Python 结束，退出码 ${code}`)
    return
  }
  if (extension === '.go') {
    send({ sessionId, stream: 'system', content: `$ go run ${sourcePath}\n` })
    const code = await run('go', ['run', sourcePath])
    if (code !== 0) throw new Error(`Go 结束，退出码 ${code}`)
    return
  }
  if (extension === '.ts') {
    await compileAndRun('tsc', [sourcePath, '--outDir', buildDirectory, '--target', 'ES2022', '--module', 'commonjs', '--skipLibCheck'], 'node', [join(buildDirectory, `${baseName}.js`)])
    return
  }
  if (extension === '.c' || extension === '.cc' || extension === '.cpp' || extension === '.cxx') {
    await compileAndRun('g++', [sourcePath, '-o', buildOutput], buildOutput, [])
    return
  }
  if (extension === '.rs') {
    await compileAndRun('rustc', [sourcePath, '-o', buildOutput], buildOutput, [])
    return
  }
  if (extension === '.java') {
    await compileAndRun('javac', ['-d', buildDirectory, sourcePath], 'java', ['-cp', buildDirectory, baseName])
    return
  }
  throw new Error(`不支持运行 ${fileName}，请启用相应语言插件。`)
}

async function buildFileTree(directory: string, depth = 0): Promise<FileEntry[]> {
  if (depth > 4) return []
  const entries = await readdir(directory, { withFileTypes: true })
  const visibleEntries = entries
    .filter((entry) => !entry.name.startsWith('.') && !(entry.isDirectory() && SKIPPED_DIRECTORIES.has(entry.name)))
    .sort((left, right) => Number(right.isDirectory()) - Number(left.isDirectory()) || left.name.localeCompare(right.name))

  return Promise.all(visibleEntries.slice(0, 250).map(async (entry) => {
    const fullPath = join(directory, entry.name)
    if (entry.isDirectory()) {
      return { name: entry.name, path: fullPath, kind: 'directory', children: await buildFileTree(fullPath, depth + 1) }
    }
    return { name: entry.name, path: fullPath, kind: 'file' }
  }))
}

async function createWindow(): Promise<void> {
  const window = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 1120,
    minHeight: 700,
    backgroundColor: '#0c111c',
    title: 'IDEA Assistant',
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(currentDir, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })

  if (isDevelopment) {
    await window.loadURL('http://127.0.0.1:5173')
  } else {
    await window.loadFile(join(currentDir, '..', 'dist', 'index.html'))
  }
}

app.whenReady().then(async () => {
  ipcMain.handle('service:config', async (): Promise<ServiceConfig> => {
    const config = await loadServiceConfig()
    return publicServiceConfig(config)
  })
  ipcMain.handle('service:save-config', async (_event, config: { serverUrl: string; spaceId: string }): Promise<ServiceConfig> => saveServiceConfig(config))
  ipcMain.handle('service:send-email-code', async (_event, email: string): Promise<void> => sendEmailCode(email))
  ipcMain.handle('service:verify-email-code', async (_event, email: string, code: string): Promise<LoginResponse> => verifyEmailCode(email, code))
  ipcMain.handle('service:logout', async (): Promise<void> => logout())
  ipcMain.handle('service:health', async (): Promise<ServiceHealth> => {
    const response = await serviceRequest('/health')
    if (!response.ok) throw new Error(`服务健康检查失败 (${response.status})`)
    const body = await response.json() as { status?: string; version?: string; llm_available?: boolean }
    const identity = await serviceRequest('/api/platform/me', {}, true)
    if (!identity.ok) {
      const error = await identity.json().catch(() => ({})) as { detail?: string }
      throw new Error(error.detail || `服务鉴权失败 (${identity.status})`)
    }
    return { status: body.status ?? 'unknown', version: body.version, llmAvailable: body.llm_available }
  })
  ipcMain.handle('service:chat', async (_event, request: { agentId: string; message: string; conversationId?: string; useMemory?: boolean }): Promise<ChatResponse> => {
    const response = await serviceRequest('/api/assistant/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: request.agentId, message: request.message, conversation_id: request.conversationId, use_memory: request.useMemory === true }),
    }, true)
    const body = await response.json().catch(() => ({})) as { detail?: string; reply?: string; conversation_id?: string; agent_id?: string; dispatched_to?: string | null }
    if (!response.ok) throw new Error(body.detail || `服务请求失败 (${response.status})`)
    if (!body.reply || !body.conversation_id || !body.agent_id) throw new Error('服务返回了无效的聊天响应')
    return { reply: body.reply, conversationId: body.conversation_id, agentId: body.agent_id, dispatchedTo: body.dispatched_to }
  })
  ipcMain.handle('service:conversations', async (): Promise<ConversationSummary[]> => {
    const response = await serviceRequest('/api/conversations', {}, true)
    const body = await response.json().catch(() => ({})) as { detail?: string; conversations?: ConversationSummary[] }
    if (!response.ok || !body.conversations) throw new Error(body.detail || `会话读取失败 (${response.status})`)
    return body.conversations
  })
  ipcMain.handle('service:conversation', async (_event, conversationId: string): Promise<ConversationDetail> => {
    const response = await serviceRequest(`/api/conversations/${encodeURIComponent(conversationId)}`, {}, true)
    const body = await response.json().catch(() => ({})) as { detail?: string; id?: string; messages?: ConversationDetail['messages'] }
    if (!response.ok || !body.id || !body.messages) throw new Error(body.detail || `会话读取失败 (${response.status})`)
    return { id: body.id, messages: body.messages }
  })
  ipcMain.handle('service:tasks', async (): Promise<TaskSummary[]> => {
    const response = await serviceRequest('/api/tasks', {}, true)
    const body = await response.json().catch(() => ({})) as { detail?: string; tasks?: TaskSummary[] }
    if (!response.ok || !body.tasks) throw new Error(body.detail || `任务读取失败 (${response.status})`)
    return body.tasks
  })
  ipcMain.handle('service:sync-events', async (_event, after: number): Promise<SyncSnapshot> => {
    const response = await serviceRequest(`/api/sync/events?after=${Math.max(0, after)}`, {}, true)
    const body = await response.json().catch(() => ({})) as { detail?: string; events?: SyncSnapshot['events']; next_cursor?: number }
    if (!response.ok || !body.events || typeof body.next_cursor !== 'number') throw new Error(body.detail || `同步读取失败 (${response.status})`)
    return { events: body.events, next_cursor: body.next_cursor }
  })
  ipcMain.handle('service:memories', async (): Promise<MemoryRecord[]> => {
    const response = await serviceRequest('/api/memories', {}, true)
    const body = await response.json().catch(() => ({})) as { detail?: string; memories?: MemoryRecord[] }
    if (!response.ok || !body.memories) throw new Error(body.detail || `记忆读取失败 (${response.status})`)
    return body.memories
  })
  ipcMain.handle('service:create-memory', async (_event, memory: { scope: MemoryScope; category: string; content: string }): Promise<MemoryRecord> => {
    const response = await serviceRequest('/api/memories', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...memory, confirmed: true }) }, true)
    const body = await response.json().catch(() => ({})) as { detail?: string; id?: string }
    if (!response.ok || !body.id) throw new Error(body.detail || `记忆保存失败 (${response.status})`)
    return body as MemoryRecord
  })
  ipcMain.handle('service:delete-memory', async (_event, memoryId: string): Promise<void> => {
    const response = await serviceRequest(`/api/memories/${encodeURIComponent(memoryId)}`, { method: 'DELETE' }, true)
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { detail?: string }
      throw new Error(body.detail || `记忆删除失败 (${response.status})`)
    }
  })
  ipcMain.handle('workspace:choose', async () => {
    const result = await dialog.showOpenDialog({ properties: ['openDirectory', 'createDirectory'] })
    return result.canceled ? null : result.filePaths[0]
  })
  ipcMain.handle('workspace:tree', async (_event, workspace: string): Promise<FileEntry[]> => {
    if (!workspace || !existsSync(workspace)) throw new Error('工作区不存在')
    return buildFileTree(resolve(workspace))
  })
  ipcMain.handle('file:read', async (_event, workspace: string, filePath: string) => {
    const resolvedPath = assertWithinWorkspace(workspace, filePath)
    if (!TEXT_EXTENSIONS.has(extname(resolvedPath).toLowerCase())) throw new Error('当前仅支持打开文本与 Markdown 文件')
    return { path: resolvedPath, name: basename(resolvedPath), content: await readFile(resolvedPath, 'utf8') }
  })
  ipcMain.handle('file:save', async (_event, workspace: string, filePath: string, content: string) => {
    const resolvedPath = assertWithinWorkspace(workspace, filePath)
    if (!TEXT_EXTENSIONS.has(extname(resolvedPath).toLowerCase())) throw new Error('当前仅支持保存文本与 Markdown 文件')
    if (typeof content !== 'string' || content.length > 2_000_000) throw new Error('文件内容无效或超过 2 MB 限制')
    await writeFile(resolvedPath, content, 'utf8')
    return { path: resolvedPath, saved: true }
  })
  ipcMain.handle('execution:start', async (event, workspace: string, filePath: string, mode: RunMode) => {
    if (mode !== 'run' && mode !== 'debug') throw new Error('无效的执行模式')
    const sessionId = createRunId()
    const send = (runEvent: RunEvent) => event.sender.send('execution:output', runEvent)
    send({ sessionId, stream: 'system', content: mode === 'debug' ? '正在以调试模式启动。\n' : '正在运行。\n' })
    void executeFile(workspace, filePath, mode, sessionId, send)
      .then(() => send({ sessionId, stream: 'system', content: '执行完成。\n' }))
      .catch((error) => send({ sessionId, stream: 'stderr', content: `${error instanceof Error ? error.message : String(error)}\n` }))
    return { sessionId }
  })
  ipcMain.handle('execution:stop', async (_event, sessionId: string) => {
    const child = activeRuns.get(sessionId)
    if (!child) return false
    child.kill()
    return true
  })

  try {
    await createWindow()
  } catch (error) {
    await dialog.showMessageBox({ type: 'error', title: 'IDEA Assistant 启动失败', message: String(error) })
    app.quit()
  }

  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) void createWindow() })
})

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })

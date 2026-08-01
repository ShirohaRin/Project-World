import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react'
import CodeEditor from './CodeEditor'
import { languagePluginForFile, PLUGINS } from './plugins/registry'
import './plugins/marketplace-theme.css'

type Mode = 'ide' | 'work'
type OpenFile = { path: string; name: string; content: string; savedContent: string }
type ChatMessage = { id: number; role: 'assistant' | 'user'; content: string }
type Theme = 'dark' | 'light'
type Background = 'default' | 'graphite' | 'midnight'
type ExecutionLine = { stream: ExecutionOutput['stream']; content: string }
type ServiceState = 'unconfigured' | 'checking' | 'online' | 'unauthorized' | 'error'

const INITIAL_MESSAGES: ChatMessage[] = [{
  id: 1,
  role: 'assistant',
  content: '我是 IDEA Assistant。本地 Work 模式已准备就绪。\n\n当前可管理工作区、查看和编辑本地文件；接入 IDEA Agents 后，这里将承载任务执行与结果回传。',
}]

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#039;', '"': '&quot;' })[character] ?? character)
}

function renderMarkdown(source: string): string {
  const lines = source.split('\n')
  const rendered: string[] = []
  let codeLines: string[] = []
  let listItems: string[] = []
  let inCodeBlock = false

  const inline = (value: string) => escapeHtml(value)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')

  const flushList = () => {
    if (listItems.length) rendered.push(`<ul>${listItems.map((item) => `<li>${inline(item)}</li>`).join('')}</ul>`)
    listItems = []
  }

  for (const line of lines) {
    if (line.startsWith('```')) {
      if (inCodeBlock) {
        rendered.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
        codeLines = []
      }
      inCodeBlock = !inCodeBlock
      continue
    }
    if (inCodeBlock) {
      codeLines.push(line)
      continue
    }
    if (/^[-*] /.test(line)) {
      listItems.push(line.slice(2))
      continue
    }
    flushList()
    if (line.startsWith('### ')) rendered.push(`<h3>${inline(line.slice(4))}</h3>`)
    else if (line.startsWith('## ')) rendered.push(`<h2>${inline(line.slice(3))}</h2>`)
    else if (line.startsWith('# ')) rendered.push(`<h1>${inline(line.slice(2))}</h1>`)
    else if (/^---+$/.test(line)) rendered.push('<hr />')
    else if (line) rendered.push(`<p>${inline(line)}</p>`)
  }
  flushList()
  if (codeLines.length) rendered.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
  return rendered.join('')
}

function splitMarkdownBlocks(source: string): string[] {
  const lines = source.split('\n')
  const blocks: string[] = []
  let current: string[] = []
  let inCodeBlock = false
  const flush = () => { if (current.length) { blocks.push(current.join('\n')); current = [] } }

  for (const line of lines) {
    if (line.startsWith('```')) {
      current.push(line)
      inCodeBlock = !inCodeBlock
      if (!inCodeBlock) flush()
      continue
    }
    if (inCodeBlock) { current.push(line); continue }
    if (!line.trim()) { flush(); continue }
    if (/^#{1,3} /.test(line) || /^---+$/.test(line)) { flush(); blocks.push(line); continue }
    if (/^[-*] /.test(line)) { current.push(line); continue }
    if (current.length && /^[-*] /.test(current[0])) flush()
    current.push(line)
  }
  flush()
  return blocks.length ? blocks : ['']
}

function MarkdownLiveEditor({ content, onChange }: { content: string; onChange: (content: string) => void }) {
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const blocks = useMemo(() => splitMarkdownBlocks(content), [content])

  function updateBlock(index: number, value: string) {
    const nextBlocks = [...blocks]
    nextBlocks[index] = value
    onChange(nextBlocks.join('\n\n'))
  }

  return <article className="markdown-live-editor">
    <div className="live-editor-notice"><span>◉</span> 隐编辑模式：点击内容块即可编辑 Markdown 源码</div>
    <div className="live-editor-document">{blocks.map((block, index) => editingIndex === index ? <textarea key={`${index}-${block}`} autoFocus className="live-source-block" value={block} onChange={(event) => updateBlock(index, event.target.value)} onBlur={() => setEditingIndex(null)} onKeyDown={(event) => { if (event.key === 'Escape') { event.currentTarget.blur() } }} /> : <button key={`${index}-${block}`} className="live-rendered-block" onClick={() => setEditingIndex(index)} dangerouslySetInnerHTML={{ __html: renderMarkdown(block) || '<p>点击开始输入</p>' }} />)}</div>
  </article>
}

function FileNode({ node, selectedPath, onOpen, depth = 0 }: { node: FileTreeEntry; selectedPath?: string; onOpen: (file: FileTreeEntry) => void; depth?: number }) {
  const [expanded, setExpanded] = useState(depth < 1)
  if (node.kind === 'directory') {
    return <div className="file-node">
      <button className="tree-folder" style={{ paddingLeft: 10 + depth * 14 }} onClick={() => setExpanded((value) => !value)}><span>{expanded ? '⌄' : '›'}</span>{node.name}</button>
      {expanded && node.children?.map((child) => <FileNode key={child.path} node={child} selectedPath={selectedPath} onOpen={onOpen} depth={depth + 1} />)}
    </div>
  }
  return <button className={`tree-file ${selectedPath === node.path ? 'active' : ''}`} style={{ paddingLeft: 28 + depth * 14 }} onClick={() => onOpen(node)}><span>{node.name.endsWith('.md') ? 'M' : '·'}</span>{node.name}</button>
}

function TaskSidebar({ workspace, workspaceName, onChooseWorkspace, onCreateTask, onOpenTask, onOpenConversation, onOpenPendingTask, onRefresh, onSwitchToIde, showWorkspace = true, newTask, serviceState, conversations, tasks, syncLabel }: { workspace: string; workspaceName: string; onChooseWorkspace: () => void; onCreateTask: () => void; onOpenTask: () => void; onOpenConversation: (conversationId: string) => void; onOpenPendingTask: (task: TaskSummary) => void; onRefresh: () => void; onSwitchToIde?: () => void; showWorkspace?: boolean; newTask: boolean; serviceState: ServiceState; conversations: ConversationSummary[]; tasks: TaskSummary[]; syncLabel: string }) {
  const serviceLabel = serviceState === 'online' ? '在线服务' : serviceState === 'checking' ? '正在连接' : serviceState === 'unauthorized' ? '鉴权失败' : serviceState === 'error' ? '服务不可用' : '未配置服务'
  const pendingTasks = tasks.filter((task) => task.status === 'pending')
  return <aside className="chat-sidebar"><button className="new-chat-button" onClick={onCreateTask}><span>＋</span>新建任务</button><div className="chat-sidebar-label">会话 <button className="sync-button" title="刷新跨设备状态" onClick={onRefresh}>↻</button></div><div className="remote-list">{conversations.length ? conversations.map((conversation) => <button key={conversation.id} className={newTask ? 'chat-history-item' : 'chat-history-item active'} onClick={() => onOpenConversation(conversation.id)}><span className="history-dot" />会话 · {conversation.messages} 条消息</button>) : <button className={newTask ? 'chat-history-item active' : 'chat-history-item'} onClick={onOpenTask}><span className="history-dot" />工作会话</button>}</div>{pendingTasks.length ? <><div className="chat-sidebar-label">待续接 · {pendingTasks.length}</div><div className="remote-list">{pendingTasks.map((task) => <button key={task.id} className="chat-history-item pending-task" onClick={() => onOpenPendingTask(task)}><span className="history-dot" />{task.title}</button>)}</div></> : null}{showWorkspace ? <><div className="chat-sidebar-label">工作区</div><button className="workspace-button" title={workspace} onClick={onChooseWorkspace}><span className="chevron">⌄</span>{workspaceName}</button></> : null}<div className="chat-sidebar-footer"><span className={`service-status ${serviceState}`}><span />{serviceLabel} · {syncLabel}</span>{onSwitchToIde ? <button title="切换至 IDE" onClick={onSwitchToIde}>⌘</button> : null}</div></aside>
}

export default function App() {
  const [mode, setMode] = useState<Mode>('work')
  const [workspace, setWorkspace] = useState('')
  const [tree, setTree] = useState<FileTreeEntry[]>([])
  const [openFile, setOpenFile] = useState<OpenFile | null>(null)
  const [editorView, setEditorView] = useState<'edit' | 'preview' | 'split' | 'live'>('edit')
  const [status, setStatus] = useState('离线模式 · 本地文件可用')
  const [serviceState, setServiceState] = useState<ServiceState>('unconfigured')
  const [serviceConfig, setServiceConfig] = useState<ServiceConfig>({ serverUrl: '', spaceId: '', deviceId: '', signedIn: false })
  const [serviceUrlInput, setServiceUrlInput] = useState('')
  const [spaceIdInput, setSpaceIdInput] = useState('')
  const [emailInput, setEmailInput] = useState('')
  const [verificationCodeInput, setVerificationCodeInput] = useState('')
  const [isSendingCode, setIsSendingCode] = useState(false)
  const [isSigningIn, setIsSigningIn] = useState(false)
  const [conversationId, setConversationId] = useState<string | undefined>()
  const [isSending, setIsSending] = useState(false)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>(INITIAL_MESSAGES)
  const [isNewTask, setIsNewTask] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [memories, setMemories] = useState<MemoryRecord[]>([])
  const [syncCursor, setSyncCursor] = useState(0)
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null)
  const [isSyncing, setIsSyncing] = useState(false)
  const [useMemory, setUseMemory] = useState(false)
  const [showMemoryPanel, setShowMemoryPanel] = useState(false)
  const [memoryContent, setMemoryContent] = useState('')
  const [memoryCategory, setMemoryCategory] = useState('general')
  const [memoryScope, setMemoryScope] = useState<'personal' | 'space' | 'owner'>('personal')
  const [isSavingMemory, setIsSavingMemory] = useState(false)
  const [selectedAgent] = useState('IDEA')
  const [showSettings, setShowSettings] = useState(false)
  const [showIdeMenu, setShowIdeMenu] = useState(false)
  const [showIdeSettings, setShowIdeSettings] = useState(false)
  const [headerMenu, setHeaderMenu] = useState<'file' | 'edit' | 'view' | 'window' | 'help' | null>(null)
  const [theme, setTheme] = useState<Theme>('dark')
  const [fontSize, setFontSize] = useState(13)
  const [background, setBackground] = useState<Background>('default')
  const [showMarketplace, setShowMarketplace] = useState(false)
  const [enabledPlugins, setEnabledPlugins] = useState<string[]>(() => PLUGINS.filter((plugin) => plugin.enabledByDefault).map((plugin) => plugin.id))
  const [executionLines, setExecutionLines] = useState<ExecutionLine[]>([])
  const [executionSessionId, setExecutionSessionId] = useState<string | null>(null)

  const isDirty = Boolean(openFile && openFile.content !== openFile.savedContent)
  const markdownPreview = useMemo(() => openFile?.name.toLowerCase().endsWith('.md') ? renderMarkdown(openFile.content) : '', [openFile])

  useEffect(() => { void refreshTree() }, [workspace])
  useEffect(() => { void loadServiceConfig() }, [])
  useEffect(() => window.ideaDesktop?.onExecutionOutput((event) => {
    setExecutionLines((lines) => [...lines, { stream: event.stream, content: event.content }].slice(-500))
  }), [])

  async function refreshTree() {
    if (!workspace) {
      setTree([])
      return
    }
    try {
      const result = await window.ideaDesktop?.readWorkspaceTree(workspace)
      setTree(result ?? [])
      setStatus('离线模式 · 文件树已更新')
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : '无法读取工作区')
    }
  }

  async function loadServiceConfig() {
    const config = await window.ideaDesktop?.getServiceConfig()
    if (!config) return
    setServiceConfig(config)
    setServiceUrlInput(config.serverUrl)
    setSpaceIdInput(config.spaceId)
    if (config.serverUrl && config.signedIn) void checkService(config)
  }

  async function checkService(config = serviceConfig) {
    if (!config.serverUrl || !config.signedIn) {
      setServiceState('unconfigured')
      return
    }
    setServiceState('checking')
    try {
      const health = await window.ideaDesktop?.testService()
      if (!health || health.status !== 'healthy') throw new Error('服务健康检查失败')
      setServiceState('online')
      setStatus(health.llmAvailable ? '在线 · IDEA 服务已连接' : '在线 · 服务已连接，模型暂不可用')
      void refreshRemoteState(config)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '服务不可用'
      setServiceState(message.includes('401') || message.includes('令牌') ? 'unauthorized' : 'error')
      setStatus(`${message} · 本地文件仍可用`)
    }
  }

  const syncStorageKey = (config = serviceConfig) => `idea-sync-cursor:${config.serverUrl}:${config.spaceId}`

  async function refreshRemoteState(config = serviceConfig) {
    if (!config.serverUrl || !config.signedIn || isSyncing) return
    setIsSyncing(true)
    try {
      const storedCursor = Number(window.localStorage.getItem(syncStorageKey(config)) ?? '0') || 0
      const snapshot = await window.ideaDesktop?.getSyncEvents(storedCursor)
      const [nextConversations, nextTasks, nextMemories] = await Promise.all([window.ideaDesktop?.listConversations(), window.ideaDesktop?.listTasks(), window.ideaDesktop?.listMemories()])
      if (!snapshot || !nextConversations || !nextTasks || !nextMemories) throw new Error('同步服务不可用')
      setConversations(nextConversations)
      setTasks(nextTasks)
      setMemories(nextMemories)
      setSyncCursor(snapshot.next_cursor)
      window.localStorage.setItem(syncStorageKey(config), String(snapshot.next_cursor))
      setLastSyncedAt(Date.now())
    } catch (reason) {
      setStatus(reason instanceof Error ? `跨设备同步失败：${reason.message}` : '跨设备同步失败')
    } finally {
      setIsSyncing(false)
    }
  }

  async function saveServiceSettings() {
    try {
      const config = await window.ideaDesktop?.saveServiceConfig({ serverUrl: serviceUrlInput, spaceId: spaceIdInput })
      if (!config) return
      setServiceConfig(config)
      setConversationId(undefined)
      if (config.signedIn) await checkService(config)
    } catch (reason) {
      setServiceState('error')
      setStatus(reason instanceof Error ? reason.message : '无法保存服务设置')
    }
  }

  async function sendVerificationCode() {
    if (!emailInput.trim() || isSendingCode) return
    try {
      await saveServiceSettings()
      setIsSendingCode(true)
      await window.ideaDesktop?.sendEmailCode(emailInput.trim())
      setStatus('验证码已发送，请查收邮箱')
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : '验证码发送失败')
    } finally {
      setIsSendingCode(false)
    }
  }

  async function signInWithEmail() {
    if (!emailInput.trim() || !verificationCodeInput.trim() || isSigningIn) return
    try {
      await saveServiceSettings()
      setIsSigningIn(true)
      const result = await window.ideaDesktop?.verifyEmailCode(emailInput.trim(), verificationCodeInput.trim())
      if (!result) throw new Error('登录服务不可用')
      const config = await window.ideaDesktop?.getServiceConfig()
      if (config) {
        setServiceConfig(config)
        await checkService(config)
      }
      setVerificationCodeInput('')
      setStatus(result.route === 'owner_idea' ? '已登录 · 伊迪亚私人服务域' : '已登录 · IDEA Assistant 服务域')
    } catch (reason) {
      setServiceState('unauthorized')
      setStatus(reason instanceof Error ? reason.message : '登录失败')
    } finally {
      setIsSigningIn(false)
    }
  }

  async function signOut() {
    await window.ideaDesktop?.logout()
    const config = await window.ideaDesktop?.getServiceConfig()
    if (config) setServiceConfig(config)
    setConversationId(undefined)
    setServiceState('unconfigured')
    setStatus('已退出在线账户 · 本地文件仍可用')
  }

  async function chooseWorkspace() {
    const selected = await window.ideaDesktop?.chooseWorkspace()
    if (selected) {
      setWorkspace(selected)
      setOpenFile(null)
    }
  }

  async function openLocalFile(file: FileTreeEntry) {
    try {
      const result = await window.ideaDesktop?.readFile(workspace, file.path)
      if (result) {
        setOpenFile({ ...result, savedContent: result.content })
        setEditorView(result.name.endsWith('.md') ? 'live' : 'edit')
        setStatus(`已打开 ${result.name}`)
      }
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : '无法打开文件')
    }
  }

  async function saveCurrentFile() {
    if (!openFile) return
    try {
      await window.ideaDesktop?.saveFile(workspace, openFile.path, openFile.content)
      setOpenFile((file) => file ? { ...file, savedContent: file.content } : null)
      setStatus(`已保存 ${openFile.name}`)
      void refreshTree()
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : '保存失败')
    }
  }

  const updateOpenFile = useCallback((content: string) => {
    setOpenFile((file) => file ? { ...file, content } : null)
  }, [])

  const saveEditorFile = useCallback(() => { void saveCurrentFile() }, [openFile])

  async function startExecution(mode: 'run' | 'debug') {
    if (!openFile) {
      setStatus('请先打开要运行的源代码文件')
      return
    }
    const plugin = languagePluginForFile(openFile.name)
    if (!plugin) {
      setStatus(`不支持运行 ${openFile.name}`)
      return
    }
    if (!enabledPlugins.includes(plugin.id)) {
      setStatus(`请先在插件市场启用 ${plugin.name} 插件`)
      return
    }
    if (isDirty) await saveCurrentFile()
    try {
      setExecutionLines([{ stream: 'system', content: `${mode === 'debug' ? '调试' : '运行'} ${openFile.name}\n` }])
      const result = await window.ideaDesktop?.startExecution(workspace, openFile.path, mode)
      if (result) {
        setExecutionSessionId(result.sessionId)
        setStatus(`${mode === 'debug' ? '调试' : '运行'} ${plugin.name}：${openFile.name}`)
      }
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '无法启动执行'
      setExecutionLines((lines) => [...lines, { stream: 'stderr', content: `${message}\n` }])
      setStatus(message)
    }
  }

  async function stopExecution() {
    if (!executionSessionId) return
    await window.ideaDesktop?.stopExecution(executionSessionId)
    setExecutionSessionId(null)
    setStatus('已请求停止执行')
  }

  function togglePlugin(pluginId: string) {
    setEnabledPlugins((items) => items.includes(pluginId) ? items.filter((item) => item !== pluginId) : [...items, pluginId])
  }

  function createChat() {
    setIsNewTask(true)
    setChatMessages([])
    setChatInput('')
    setConversationId(undefined)
  }

  function openChatHistory() {
    setIsNewTask(false)
    setChatMessages(INITIAL_MESSAGES.map((message) => ({ ...message })))
    setChatInput('')
    setConversationId(undefined)
  }

  async function openRemoteConversation(id: string) {
    try {
      const conversation = await window.ideaDesktop?.getConversation(id)
      if (!conversation) throw new Error('会话读取失败')
      setConversationId(conversation.id)
      setChatMessages(conversation.messages.map((message, index) => ({ id: Number(message.timestamp * 1000) + index, role: message.role, content: message.content })))
      setIsNewTask(false)
      setChatInput('')
      setStatus('已恢复跨设备会话')
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : '无法恢复会话')
    }
  }

  function openPendingTask(task: TaskSummary) {
    if (task.conversation_id) void openRemoteConversation(task.conversation_id)
    else setStatus(`任务“${task.title}”尚未关联可恢复会话`)
  }

  async function saveMemory() {
    if (!memoryContent.trim() || isSavingMemory) return
    if (memoryScope === 'space' && !window.confirm('空间记忆会对当前空间的其他成员可见。确认保存吗？')) return
    try {
      setIsSavingMemory(true)
      const memory = await window.ideaDesktop?.createMemory({ scope: memoryScope, category: memoryCategory.trim() || 'general', content: memoryContent.trim() })
      if (!memory) throw new Error('记忆保存服务不可用')
      setMemories((items) => [memory, ...items])
      setMemoryContent('')
      setStatus('已保存为跨设备长期记忆')
      void refreshRemoteState()
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : '记忆保存失败')
    } finally {
      setIsSavingMemory(false)
    }
  }

  async function removeMemory(memoryId: string) {
    if (!window.confirm('删除后，该记忆将不再参与跨设备读取。确认删除吗？')) return
    try {
      await window.ideaDesktop?.deleteMemory(memoryId)
      setMemories((items) => items.filter((item) => item.id !== memoryId))
      setStatus('已删除长期记忆')
      void refreshRemoteState()
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : '记忆删除失败')
    }
  }

  async function sendChatMessage() {
    const content = chatInput.trim()
    if (!content || isSending) return
    const messageId = Date.now()
    setChatMessages((messages) => [...messages, { id: messageId, role: 'user', content }])
    setChatInput('')
    setIsSending(true)
    try {
      const result = await window.ideaDesktop?.sendChat({ agentId: 'idea', message: content, conversationId, useMemory })
      if (!result) throw new Error('在线服务仅可在 IDEA Assistant 桌面端使用')
      setConversationId(result.conversationId)
      setChatMessages((messages) => [...messages, { id: messageId + 1, role: 'assistant', content: result.reply }])
      setServiceState('online')
      setStatus(serviceConfig.route === 'owner_idea' ? '在线 · 伊迪亚已回复' : '在线 · IDEA Assistant 已回复')
      void refreshRemoteState()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '无法连接 IDEA 服务'
      setServiceState(message.includes('401') || message.includes('令牌') ? 'unauthorized' : 'error')
      setChatMessages((messages) => [...messages, { id: messageId + 1, role: 'assistant', content: `在线服务暂不可用：${message}\n\n本地文件编辑与运行能力不受影响。` }])
      setStatus(`${message} · 本地文件仍可用`)
    } finally {
      setIsSending(false)
    }
  }

  const workspaceName = workspace ? workspace.split('\\').pop() ?? workspace : '未选择工作区'
  const syncLabel = isSyncing ? '同步中' : lastSyncedAt ? `已同步 ${new Date(lastSyncedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : syncCursor ? '已同步' : '未同步'
  return <div className={`offline-app theme-${theme} background-${background}`} style={{ '--ui-font-size': `${fontSize}px` } as CSSProperties}>
    <div className="app-body">
      <aside className="activity-rail"><button className={mode === 'ide' ? 'rail-button active' : 'rail-button'} title="资源管理器" onClick={() => setMode('ide')}>▤</button><button className={mode === 'work' ? 'rail-button active' : 'rail-button'} title="工作台" onClick={() => setMode('work')}>✓</button><button className={showMarketplace ? 'rail-button active' : 'rail-button'} title="插件市场" onClick={() => setShowMarketplace((visible) => !visible)}>▦</button><div className="rail-spacer" /><button className="rail-button" title="打开工作区" onClick={() => void chooseWorkspace()}>⌂</button><button className={showSettings ? 'rail-button active' : 'rail-button'} title="视觉设置" onClick={() => setShowSettings((visible) => !visible)}>⚙</button></aside>
      {mode === 'ide' ? <main className="ide-layout">
        <header className="ide-projectbar"><div className="project-picker"><span className="project-mark">I</span><strong>IDEA</strong></div><nav className="app-menu-bar" aria-label="应用菜单">{(['file', 'edit', 'view', 'window', 'help'] as const).map((item) => <button key={item} className={headerMenu === item ? 'active' : ''} onClick={() => setHeaderMenu((current) => current === item ? null : item)}>{item === 'file' ? 'File' : item === 'edit' ? 'Edit' : item === 'view' ? 'View' : item === 'window' ? 'Window' : 'Help'}</button>)}{headerMenu ? <div className="app-menu-popover">{headerMenu === 'file' ? <><button onClick={() => { void chooseWorkspace(); setHeaderMenu(null) }}>打开工作区</button><button disabled={!isDirty} onClick={() => { void saveCurrentFile(); setHeaderMenu(null) }}>保存</button></> : headerMenu === 'edit' ? <button disabled={!openFile}>在编辑器中修改当前文件</button> : headerMenu === 'view' ? <button onClick={() => { setShowSettings(true); setHeaderMenu(null) }}>视觉设置</button> : headerMenu === 'window' ? <button onClick={() => { setMode('work'); setHeaderMenu(null) }}>切换到 Work</button> : <button onClick={() => { setShowMarketplace(true); setHeaderMenu(null) }}>插件市场</button>}</div> : null}</nav><span className="brand-divider">/</span><span>编辑器</span><nav className="mode-switch" aria-label="工作模式"><button className="active">IDE</button><button onClick={() => setMode('work')}>WORK</button></nav><div className="project-branch">⌘ main <span>⌄</span></div><div className="project-actions"><span>{workspaceName}</span><button title="运行" disabled={!openFile || Boolean(executionSessionId)} onClick={() => void startExecution('run')}>▷</button><button title="调试" disabled={!openFile || Boolean(executionSessionId)} onClick={() => void startExecution('debug')}>☼</button><button title="设置" aria-expanded={showIdeMenu} onClick={() => setShowIdeMenu((visible) => !visible)}>⚙</button>{showIdeMenu ? <div className="ide-more-menu"><button onClick={() => { setShowIdeSettings((visible) => !visible); setShowIdeMenu(false) }}>IDE 设置</button></div> : null}</div></header>
        <div className="ide-workbench"><aside className="explorer-panel"><div className="panel-title"><span>项目</span><div className="panel-tools"><button title="新建文件">□</button><button title="刷新文件树" onClick={() => void refreshTree()}>↻</button><button title="折叠文件夹">⌃</button></div></div><button className="workspace-button" title={workspace} onClick={() => void chooseWorkspace()}><span className="chevron">⌄</span>{workspaceName}</button><div className="tree-list">{tree.map((node) => <FileNode key={node.path} node={node} selectedPath={openFile?.path} onOpen={(file) => void openLocalFile(file)} />)}</div><div className="explorer-footer"><button>⌕ 搜索</button><button>⑂ 源代码管理</button></div></aside>
          <section className="editor-panel">{openFile ? <><div className="editor-tabs"><div className="file-tab"><span>{openFile.name.endsWith('.md') ? 'M' : '·'}</span>{openFile.name}{isDirty ? ' •' : ''}<button title="关闭文件">×</button></div><div className="editor-actions">{openFile.name.endsWith('.md') ? <><button className={editorView === 'live' ? 'active' : ''} onClick={() => setEditorView('live')}>隐编辑</button><button className={editorView === 'edit' ? 'active' : ''} onClick={() => setEditorView('edit')}>源码</button><button className={editorView === 'split' ? 'active' : ''} onClick={() => setEditorView('split')}>同步预览</button><button className={editorView === 'preview' ? 'active' : ''} onClick={() => setEditorView('preview')}>预览</button></> : null}<button className="save-button" disabled={!isDirty} onClick={() => void saveCurrentFile()}>保存</button></div></div><div className="editor-breadcrumb"><span>{workspaceName}</span><b>›</b><span>{openFile.name}</span><span className="editor-language">{openFile.name.endsWith('.md') ? 'Markdown' : openFile.name.split('.').pop()?.toUpperCase()}</span></div><div className="editor-content">{editorView === 'live' && openFile.name.endsWith('.md') ? <MarkdownLiveEditor content={openFile.content} onChange={updateOpenFile} /> : editorView === 'split' && openFile.name.endsWith('.md') ? <div className="markdown-split"><CodeEditor value={openFile.content} onChange={updateOpenFile} fileName={openFile.name} enabled={Boolean(languagePluginForFile(openFile.name) && enabledPlugins.includes(languagePluginForFile(openFile.name)?.id ?? ''))} onSave={saveEditorFile} /><article className="markdown-preview" dangerouslySetInnerHTML={{ __html: markdownPreview }} /></div> : editorView === 'preview' && openFile.name.endsWith('.md') ? <article className="markdown-preview" dangerouslySetInnerHTML={{ __html: markdownPreview }} /> : <CodeEditor value={openFile.content} onChange={updateOpenFile} fileName={openFile.name} enabled={Boolean(languagePluginForFile(openFile.name) && enabledPlugins.includes(languagePluginForFile(openFile.name)?.id ?? ''))} onSave={saveEditorFile} />}</div>{executionLines.length ? <section className="execution-panel"><header><strong>输出</strong><div>{executionSessionId ? <button onClick={() => void stopExecution()}>停止</button> : null}<button onClick={() => setExecutionLines([])}>清除</button></div></header><pre>{executionLines.map((line, index) => <span className={line.stream} key={`${index}-${line.content}`}>{line.content}</span>)}</pre></section> : null}</> : <div className="empty-editor"><div className="empty-symbol">I</div><h1>IDEA 编辑器</h1><p>从左侧项目树打开本地文件。</p><button onClick={() => void chooseWorkspace()}>打开项目</button></div>}</section><TaskSidebar workspace={workspace} workspaceName={workspaceName} onChooseWorkspace={() => void chooseWorkspace()} onCreateTask={createChat} onOpenTask={openChatHistory} onOpenConversation={(id) => void openRemoteConversation(id)} onOpenPendingTask={openPendingTask} onRefresh={() => void refreshRemoteState()} newTask={isNewTask} serviceState={serviceState} conversations={conversations} tasks={tasks} syncLabel={syncLabel} />
          </div>{showIdeSettings ? <aside className="ide-settings-popover"><header><strong>IDE 设置</strong><button title="关闭" onClick={() => setShowIdeSettings(false)}>×</button></header><nav><button className="active">通用</button><button>智能体</button><button>MCP</button><button>上下文</button></nav><div className="assistant-settings"><h3>通用</h3><section><h4>会话语言</h4><p>设置 IDEA 的默认语言。</p><label><input type="radio" name="language" /> Auto 自动</label><label><input type="radio" name="language" defaultChecked /> 中文</label><label><input type="radio" name="language" /> English</label></section><section><h4>Tab-Cue <span className="switch on" /></h4><p>根据当前编辑内容提供上下文建议。</p></section><section><h4>编辑辅助 <span className="switch on" /></h4><p>在编辑器中显示解释与关联提示。</p><label><input type="checkbox" defaultChecked /> Show Doc</label><label><input type="checkbox" defaultChecked /> Show Explain</label></section><section><h4>添加到对话 <span className="switch on" /></h4><p>将当前文件内容作为 Work 对话的上下文。</p></section></div></aside> : null}
        <footer className="ide-statusbar"><span>⌘ {status}</span><span>分支：main</span><span>{openFile ? `${openFile.content.split('\n').length} 行` : '未打开文件'}</span><span>{isDirty ? '未保存更改' : 'UTF-8'}</span><span>CRLF</span></footer>
      </main> : <main className="work-layout"><TaskSidebar workspace={workspace} workspaceName={workspaceName} onChooseWorkspace={() => void chooseWorkspace()} onCreateTask={createChat} onOpenTask={openChatHistory} onOpenConversation={(id) => void openRemoteConversation(id)} onOpenPendingTask={openPendingTask} onRefresh={() => void refreshRemoteState()} onSwitchToIde={() => setMode('ide')} showWorkspace={false} newTask={isNewTask} serviceState={serviceState} conversations={conversations} tasks={tasks} syncLabel={syncLabel} /><section className="chat-panel"><header className="ide-projectbar work-projectbar"><div className="project-picker"><span className="project-mark">I</span><strong>IDEA</strong></div><nav className="app-menu-bar" aria-label="应用菜单">{(['file', 'edit', 'view', 'window', 'help'] as const).map((item) => <button key={item} className={headerMenu === item ? 'active' : ''} onClick={() => setHeaderMenu((current) => current === item ? null : item)}>{item === 'file' ? 'File' : item === 'edit' ? 'Edit' : item === 'view' ? 'View' : item === 'window' ? 'Window' : 'Help'}</button>)}{headerMenu ? <div className="app-menu-popover">{headerMenu === 'file' ? <><button onClick={() => { void chooseWorkspace(); setHeaderMenu(null) }}>打开工作区</button><button disabled={!isDirty} onClick={() => { void saveCurrentFile(); setHeaderMenu(null) }}>保存</button></> : headerMenu === 'edit' ? <button disabled={!openFile}>在编辑器中修改当前文件</button> : headerMenu === 'view' ? <button onClick={() => { setShowSettings(true); setHeaderMenu(null) }}>视觉设置</button> : headerMenu === 'window' ? <button onClick={() => { setMode('ide'); setHeaderMenu(null) }}>切换到 IDE</button> : <button onClick={() => { setShowMarketplace(true); setHeaderMenu(null) }}>插件市场</button>}</div> : null}</nav><span className="brand-divider">/</span><span>工作台</span><nav className="mode-switch" aria-label="工作模式"><button onClick={() => setMode('ide')}>IDE</button><button className="active">WORK</button></nav><div className="project-branch">⌘ main <span>⌄</span></div><div className="project-actions"><span>{workspaceName}</span><button title="运行">▷</button><button title="调试">☼</button><button title="设置" aria-expanded={showIdeMenu} onClick={() => setShowIdeMenu((visible) => !visible)}>⚙</button>{showIdeMenu ? <div className="ide-more-menu"><button onClick={() => { setShowIdeSettings((visible) => !visible); setShowIdeMenu(false) }}>IDE 设置</button></div> : null}</div></header>{chatMessages.length ? <><div className="chat-scroll"><div className="chat-thread">{chatMessages.map((message) => <article className={`chat-message ${message.role}`} key={message.id}>{message.role === 'assistant' ? <div className="assistant-avatar">I</div> : null}<div className="message-content">{message.content.split('\n').map((line, index) => <p key={`${message.id}-${index}`}>{line || <br />}</p>)}</div></article>)}</div></div><ChatComposer input={chatInput} setInput={setChatInput} onSend={sendChatMessage} selectedAgent={selectedAgent} serviceState={serviceState} isSending={isSending} useMemory={useMemory} /></> : <div className="new-task-page"><div className="new-task-center"><h1><span>◇</span> Work with IDEA</h1><ChatComposer input={chatInput} setInput={setChatInput} onSend={sendChatMessage} selectedAgent={selectedAgent} serviceState={serviceState} isSending={isSending} useMemory={useMemory} /><div className="task-shortcuts"><button onClick={() => setChatInput('帮我写一个 PPT 大纲')}>▣ 生成 PPT</button><button onClick={() => setChatInput('分析当前工作区中的数据')}>▤ 数据分析</button><button onClick={() => setChatInput('整理一份科研调研框架')}>⌁ 深度研究</button><button onClick={() => setChatInput('创建一个新的智能体角色卡')}>◇ 生成智能体</button></div></div></div>}</section><footer className="work-statusbar"><span>⌘ {serviceState === 'online' ? '在线服务' : serviceState === 'checking' ? '正在连接' : serviceState === 'unauthorized' ? '鉴权失败' : serviceState === 'error' ? '服务不可用' : '未配置服务'}</span><span>智能体：{selectedAgent}</span><span>{chatMessages.length ? '工作会话' : '新建任务'}</span></footer></main>}
    </div>
    {showSettings ? <aside className="settings-panel"><header><strong>设置</strong><button onClick={() => setShowSettings(false)}>×</button></header><section><label>IDEA 服务地址</label><input value={serviceUrlInput} onChange={(event) => setServiceUrlInput(event.target.value)} placeholder="https://shiroha-rin.world" /><button className="service-save-button" onClick={() => void saveServiceSettings()}>保存服务地址</button></section><section><label>空间 ID（可选）</label><input value={spaceIdInput} onChange={(event) => setSpaceIdInput(event.target.value)} placeholder="默认个人空间" /></section><section><label>邮箱登录 {serviceConfig.signedIn ? <span>已登录</span> : null}</label><input type="email" value={emailInput} onChange={(event) => setEmailInput(event.target.value)} placeholder="name@example.com" /><button className="service-save-button" disabled={!emailInput.trim() || isSendingCode} onClick={() => void sendVerificationCode()}>{isSendingCode ? '正在发送' : '发送验证码'}</button><input value={verificationCodeInput} inputMode="numeric" maxLength={6} onChange={(event) => setVerificationCodeInput(event.target.value.replace(/\D/g, ''))} placeholder="6 位验证码" /><button className="service-save-button" disabled={!emailInput.trim() || verificationCodeInput.length !== 6 || isSigningIn} onClick={() => void signInWithEmail()}>{isSigningIn ? '正在登录' : '验证并登录'}</button>{serviceConfig.signedIn ? <button className="service-save-button" onClick={() => void signOut()}>退出登录</button> : null}</section><section><label>跨设备记忆 <button className="inline-action" onClick={() => setShowMemoryPanel((value) => !value)}>{showMemoryPanel ? '收起' : `管理 ${memories.length}`}</button></label><label className="memory-option"><input type="checkbox" checked={useMemory} onChange={(event) => setUseMemory(event.target.checked)} />本次聊天引用已保存记忆</label><p className="memory-hint">默认关闭；启用后仅匹配当前授权范围的记忆。</p></section><section><label>显示模式</label><div className="setting-segment"><button className={theme === 'light' ? 'active' : ''} onClick={() => setTheme('light')}>亮色</button><button className={theme === 'dark' ? 'active' : ''} onClick={() => setTheme('dark')}>暗色</button></div></section><section><label>界面字号 <span>{fontSize}px</span></label><input type="range" min="12" max="17" value={fontSize} onChange={(event) => setFontSize(Number(event.target.value))} /></section><section><label>工作区背景</label><div className="background-options"><button className={background === 'default' ? 'active default-bg' : 'default-bg'} onClick={() => setBackground('default')}>默认</button><button className={background === 'graphite' ? 'active graphite-bg' : 'graphite-bg'} onClick={() => setBackground('graphite')}>石墨</button><button className={background === 'midnight' ? 'active midnight-bg' : 'midnight-bg'} onClick={() => setBackground('midnight')}>深夜</button></div></section></aside> : null}
    {showMemoryPanel ? <aside className="memory-panel"><header><div><strong>跨设备记忆</strong><small>仅显式保存的内容会进入服务端</small></div><button onClick={() => setShowMemoryPanel(false)}>×</button></header><section><textarea value={memoryContent} onChange={(event) => setMemoryContent(event.target.value)} placeholder="写下需要在其他设备延续的偏好、约定或项目上下文" /><div className="memory-form-row"><input value={memoryCategory} onChange={(event) => setMemoryCategory(event.target.value)} placeholder="分类" /><select value={memoryScope} onChange={(event) => setMemoryScope(event.target.value as 'personal' | 'space' | 'owner')}><option value="personal">个人</option><option value="space">当前空间共享</option>{serviceConfig.route === 'owner_idea' ? <option value="owner">Owner 私人</option> : null}</select></div><button className="service-save-button" disabled={!memoryContent.trim() || isSavingMemory} onClick={() => void saveMemory()}>{isSavingMemory ? '正在保存' : '确认保存记忆'}</button></section><div className="memory-list">{memories.map((memory) => <article key={memory.id}><div><strong>{memory.category}</strong><small>{memory.namespace.startsWith('owner/') ? 'Owner 私人' : memory.namespace.startsWith('space/') ? '空间共享' : '个人'}</small><p>{memory.content}</p></div><button title="删除记忆" onClick={() => void removeMemory(memory.id)}>×</button></article>)}</div></aside> : null}
    {showMarketplace ? <aside className="marketplace-panel"><header><div><strong>插件市场</strong><small>本地插件</small></div><button onClick={() => setShowMarketplace(false)}>×</button></header><div className="marketplace-search">⌕ <input placeholder="搜索插件" /></div><nav><button className="active">已发现</button><button>已启用</button></nav><div className="plugin-list">{PLUGINS.map((plugin) => <article className="plugin-card" key={plugin.id}><div className="plugin-icon">{plugin.category === '语言支持' ? '&lt;/&gt;' : '⌘'}</div><div><h3>{plugin.name} <small>v{plugin.version}</small></h3><p>{plugin.description}</p>{plugin.languages ? <span className="plugin-meta">{plugin.languages.length} 种语言</span> : <span className="plugin-meta">本地工具</span>}</div><button className={enabledPlugins.includes(plugin.id) ? 'plugin-toggle enabled' : 'plugin-toggle'} onClick={() => togglePlugin(plugin.id)}>{enabledPlugins.includes(plugin.id) ? '已启用' : '启用'}</button></article>)}</div></aside> : null}
  </div>
}

function ChatComposer({ input, setInput, onSend, selectedAgent, serviceState, isSending, useMemory = false }: { input: string; setInput: (value: string) => void; onSend: () => void; selectedAgent: string; serviceState: ServiceState; isSending: boolean; useMemory?: boolean }) {
  const note = serviceState === 'online' ? useMemory ? '本次聊天会在当前授权范围内匹配已保存的长期记忆。' : '在线模式会调用当前账户获授权的智能体；长期记忆默认不读取。' : serviceState === 'checking' ? '正在连接 IDEA 服务。' : serviceState === 'unauthorized' ? '登录会话无效，请在设置中重新登录。' : serviceState === 'error' ? '服务暂不可用，本地文件能力不受影响。' : '请在设置中配置 IDEA 服务地址并使用邮箱登录。'
  return <div className="composer-wrap"><div className="chat-composer"><textarea value={input} disabled={isSending} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); onSend() } }} placeholder="描述你希望完成的任务" rows={1} /><div className="composer-toolbar"><div><button title="添加附件">⌕</button><button title="引用工作区">⌘</button></div><div><span>{selectedAgent}</span><button className="send-button" disabled={!input.trim() || isSending} title="发送" onClick={onSend}>{isSending ? '…' : '↑'}</button></div></div></div><p className="composer-note">{note}</p></div>
}

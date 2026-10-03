package cn.programidea.assistant

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.lazy.rememberLazyListState

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { IdeaApp(SecureSessionStore(applicationContext)) } }
    }
}

private enum class Destination(val label: String) { Chat("对话"), Memory("记忆"), Sync("同步") }

@Composable
private fun IdeaApp(store: SecureSessionStore) {
    val api = remember { IdeaApi(store) }
    var session by remember { mutableStateOf(store.session()) }
    if (session == null) LoginScreen(api, store) { session = store.session() } else HomeScreen(api, store) { session = null }
}

@Composable
private fun LoginScreen(api: AuthRepository, store: SecureSessionStore, onLoggedIn: () -> Unit) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var serverUrl by remember { mutableStateOf(store.serverUrl) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = remember { CoroutineScope(Dispatchers.Main) }

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("IDEA Assistant", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        Text("跨设备对话与记忆", color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedTextField(serverUrl, { serverUrl = it }, label = { Text("IDEA 服务地址") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(email, { email = it }, label = { Text("账号邮箱") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(password, { password = it }, label = { Text("密码") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Button(
            onClick = {
                busy = true
                error = null
                scope.launch {
                    runCatching {
                        store.serverUrl = serverUrl
                        withContext(Dispatchers.IO) { api.login(email.trim(), password) }
                    }.onFailure { error = it.message ?: "登录失败" }
                    busy = false
                }
            },
            enabled = !busy && email.isNotBlank() && password.isNotBlank() && serverUrl.isNotBlank(),
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (busy) "正在登录" else "登录") }
        Text("此设备会生成独立 ID，并使用 Android 加密存储保存会话。Owner 设备仍需在已批准设备上确认。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HomeScreen(api: IdeaApi, store: SecureSessionStore, onLoggedOut: () -> Unit) {
    var destination by remember { mutableStateOf(Destination.Chat) }
    var showSettings by remember { mutableStateOf(false) }
    Scaffold(
        topBar = { CenterAlignedTopAppBar(title = { Text("IDEA Assistant") }, actions = { TextButton(onClick = { showSettings = true }) { Text("设置") } }, colors = TopAppBarDefaults.centerAlignedTopAppBarColors(containerColor = MaterialTheme.colorScheme.surface)) },
        bottomBar = {
            NavigationBar {
                Destination.entries.forEach { item ->
                    NavigationBarItem(selected = destination == item, onClick = { destination = item }, icon = { Text(item.label.take(1)) }, label = { Text(item.label) })
                }
            }
        },
    ) { padding ->
        when (destination) {
            Destination.Chat -> ChatScreen(api, Modifier.padding(padding))
            Destination.Memory -> MemoryScreen(api, Modifier.padding(padding))
            Destination.Sync -> SyncScreen(api, Modifier.padding(padding))
        }
    }
    if (showSettings) SettingsDialog(store, onDismiss = { showSettings = false }, onLoggedOut = onLoggedOut)
}

@Composable
private fun ChatScreen(api: ConversationRepository, modifier: Modifier) {
    val scope = remember { CoroutineScope(Dispatchers.Main) }
    val messageListState = rememberLazyListState()
    var conversations by remember { mutableStateOf<List<ConversationSummary>>(emptyList()) }
    var messages by remember { mutableStateOf<List<ChatMessage>>(emptyList()) }
    var activeId by remember { mutableStateOf<String?>(null) }
    var input by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }

    fun loadConversations() = scope.launch {
        runCatching { withContext(Dispatchers.IO) { api.conversations() } }
            .onSuccess { conversations = it }
            .onFailure { error = it.message ?: "会话加载失败" }
    }

    fun openConversation(id: String) = scope.launch {
        runCatching { withContext(Dispatchers.IO) { api.conversation(id) } }
            .onSuccess {
                activeId = it.id
                messages = it.messages
                error = null
            }
            .onFailure { error = it.message ?: "会话加载失败" }
    }

    androidx.compose.runtime.LaunchedEffect(Unit) { loadConversations() }
    androidx.compose.runtime.LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) messageListState.animateScrollToItem(messages.lastIndex)
    }

    Column(modifier = modifier.fillMaxSize().background(MaterialTheme.colorScheme.surface)) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = activeId?.let { "对话 ${it.takeLast(8)}" } ?: "新的对话",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Text("生活助手 · 科研助手", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            OutlinedButton(onClick = { activeId = null; messages = emptyList(); error = null }) { Text("新对话") }
        }

        if (conversations.isNotEmpty()) {
            LazyRow(
                modifier = Modifier.fillMaxWidth().height(74.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp),
            ) {
                items(conversations, key = { it.id }) { conversation ->
                    Card(
                        modifier = Modifier.width(150.dp).clickable { openConversation(conversation.id) },
                        shape = RoundedCornerShape(14.dp),
                    ) {
                        Column(Modifier.padding(10.dp)) {
                            Text(
                                text = "IDEA ${conversation.id.takeLast(6)}",
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                fontWeight = FontWeight.Medium,
                            )
                            Text("${conversation.messages} 条消息", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
            Spacer(Modifier.height(6.dp))
        }

        LazyColumn(
            state = messageListState,
            modifier = Modifier.fillMaxWidth().weight(1f),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(messages, key = { it.id.ifBlank { "${it.role}-${it.timestamp}-${it.content.hashCode()}" } }) { message ->
                MessageBubble(message)
            }
            if (busy) item { TypingIndicator() }
        }

        error?.let {
            Text(it, modifier = Modifier.padding(horizontal = 16.dp), color = MaterialTheme.colorScheme.error)
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(12.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                placeholder = { Text("发消息给 IDEA") },
                modifier = Modifier.weight(1f),
                maxLines = 4,
                shape = RoundedCornerShape(20.dp),
            )
            Button(
                onClick = {
                    val text = input.trim()
                    if (text.isEmpty() || busy) return@Button
                    input = ""
                    busy = true
                    error = null
                    messages = messages + ChatMessage(role = "user", content = text)
                    scope.launch {
                        runCatching { withContext(Dispatchers.IO) { api.chat(text, activeId) } }
                            .onSuccess { reply ->
                                activeId = reply.conversationId
                                messages = messages + ChatMessage(role = "assistant", content = reply.reply)
                                loadConversations()
                            }
                            .onFailure { error = it.message ?: "发送失败" }
                            .also { busy = false }
                    }
                },
                enabled = !busy && input.isNotBlank(),
                modifier = Modifier.height(56.dp),
                shape = RoundedCornerShape(18.dp),
            ) { Text("发送") }
        }
    }
}

@Composable
private fun MessageBubble(message: ChatMessage) {
    val isUser = message.role == "user"
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
        verticalAlignment = Alignment.Bottom,
    ) {
        if (!isUser) Avatar("I", MaterialTheme.colorScheme.primaryContainer)
        if (!isUser) Spacer(Modifier.width(8.dp))
        Box(
            modifier = Modifier
                .widthIn(max = 300.dp)
                .clip(RoundedCornerShape(18.dp))
                .background(if (isUser) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant)
                .padding(horizontal = 14.dp, vertical = 10.dp),
        ) {
            Text(
                message.content,
                color = if (isUser) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (isUser) Spacer(Modifier.width(8.dp))
        if (isUser) Avatar("你", MaterialTheme.colorScheme.primary)
    }
}

@Composable
private fun Avatar(label: String, color: Color) {
    Box(
        modifier = Modifier.size(34.dp).clip(RoundedCornerShape(17.dp)).background(color),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun TypingIndicator() {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Avatar("I", MaterialTheme.colorScheme.primaryContainer)
        Spacer(Modifier.width(8.dp))
        Text("IDEA 正在思考…", color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun MemoryScreen(api: MemoryRepository, modifier: Modifier) {
    val scope = remember { CoroutineScope(Dispatchers.Main) }
    var memories by remember { mutableStateOf<List<MemoryRecord>>(emptyList()) }
    var content by remember { mutableStateOf("") }
    var category by remember { mutableStateOf("general") }
    var scopeName by remember { mutableStateOf("personal") }
    var error by remember { mutableStateOf<String?>(null) }
    var editTarget by remember { mutableStateOf<MemoryRecord?>(null) }
    fun reload() = scope.launch { runCatching { withContext(Dispatchers.IO) { api.memories() } }.onSuccess { memories = it }.onFailure { error = it.message } }
    androidx.compose.runtime.LaunchedEffect(Unit) { reload() }

    Column(modifier = modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("跨设备记忆", style = MaterialTheme.typography.titleLarge)
        OutlinedTextField(content, { content = it }, label = { Text("要记住的内容") }, modifier = Modifier.fillMaxWidth(), minLines = 3)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(category, { category = it }, label = { Text("分类") }, modifier = Modifier.weight(1f), singleLine = true)
            OutlinedTextField(scopeName, { scopeName = it }, label = { Text("范围") }, modifier = Modifier.weight(1f), singleLine = true)
        }
        Button(onClick = {
            val text = content.trim(); if (text.isEmpty()) return@Button
            scope.launch { runCatching { withContext(Dispatchers.IO) { api.createMemory(scopeName, category, text) } }.onSuccess { content = ""; reload() }.onFailure { error = it.message } }
        }, modifier = Modifier.fillMaxWidth()) { Text("确认并保存") }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(memories, key = { it.id }) { memory ->
                Card(modifier = Modifier.fillMaxWidth().clickable { editTarget = memory }) { Column(Modifier.padding(12.dp)) { Text("${memory.namespace} · ${memory.category}", style = MaterialTheme.typography.labelMedium); Text(memory.content); Text("版本 ${memory.revision}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) } }
            }
        }
    }
    editTarget?.let { memory -> MemoryDialog(memory, onDismiss = { editTarget = null }, onSave = { text, categoryValue ->
        scope.launch { runCatching { withContext(Dispatchers.IO) { api.updateMemory(memory, text, categoryValue) } }.onSuccess { editTarget = null; reload() }.onFailure { error = conflictMessage(it) } }
    }, onDelete = {
        scope.launch { runCatching { withContext(Dispatchers.IO) { api.deleteMemory(memory) } }.onSuccess { editTarget = null; reload() }.onFailure { error = conflictMessage(it) } }
    }) }
}

@Composable
private fun MemoryDialog(memory: MemoryRecord, onDismiss: () -> Unit, onSave: (String, String) -> Unit, onDelete: () -> Unit) {
    var text by remember { mutableStateOf(memory.content) }
    var category by remember { mutableStateOf(memory.category) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text("编辑记忆") }, text = { Column(verticalArrangement = Arrangement.spacedBy(8.dp)) { OutlinedTextField(text, { text = it }, label = { Text("内容") }); OutlinedTextField(category, { category = it }, label = { Text("分类") }) } }, confirmButton = { TextButton(onClick = { onSave(text, category) }) { Text("保存") } }, dismissButton = { Row { TextButton(onClick = onDelete) { Text("删除", color = MaterialTheme.colorScheme.error) }; TextButton(onClick = onDismiss) { Text("取消") } } })
}

@Composable
private fun SyncScreen(api: SyncRepository, modifier: Modifier) {
    val scope = remember { CoroutineScope(Dispatchers.Main) }
    var cursor by remember { mutableLongStateOf(0L) }
    var events by remember { mutableStateOf<List<SyncEvent>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }
    Column(modifier = modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("同步状态", style = MaterialTheme.typography.titleLarge)
        Text("游标 $cursor", color = MaterialTheme.colorScheme.onSurfaceVariant)
        Button(onClick = { scope.launch { runCatching { withContext(Dispatchers.IO) { api.sync(cursor) } }.onSuccess { (newEvents, next) -> events = newEvents + events; cursor = next }.onFailure { error = it.message } } }, modifier = Modifier.fillMaxWidth()) { Text("拉取更新") }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) { items(events, key = { it.eventId }) { event -> Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(12.dp)) { Text(event.eventType, fontWeight = FontWeight.Medium); Text("${event.aggregateType} · #${event.eventId}", color = MaterialTheme.colorScheme.onSurfaceVariant) } } } }
    }
}

@Composable
private fun SettingsDialog(store: SecureSessionStore, onDismiss: () -> Unit, onLoggedOut: () -> Unit) {
    val api = remember { IdeaApi(store) }
    var spaceId by remember { mutableStateOf(store.spaceId) }
    val scope = remember { CoroutineScope(Dispatchers.Main) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text("连接设置") }, text = { Column(verticalArrangement = Arrangement.spacedBy(8.dp)) { Text("设备：${store.deviceId}", style = MaterialTheme.typography.bodySmall); OutlinedTextField(spaceId, { spaceId = it }, label = { Text("空间 ID（可选）") }); Text("不填时由服务端选择可访问的默认空间。", style = MaterialTheme.typography.bodySmall) } }, confirmButton = { TextButton(onClick = { store.spaceId = spaceId; onDismiss() }) { Text("保存") } }, dismissButton = { Row { TextButton(onClick = { scope.launch { withContext(Dispatchers.IO) { IdeaApi(store).logout() }; onDismiss(); onLoggedOut() } }) { Text("退出登录", color = MaterialTheme.colorScheme.error) }; TextButton(onClick = onDismiss) { Text("取消") } } })
}

private fun conflictMessage(error: Throwable): String = if (error is ApiException && error.statusCode == 409) "这条记忆已经在其他设备更新到版本 ${error.memoryRevision ?: "新"}，请刷新后再编辑。" else error.message ?: "请求失败"

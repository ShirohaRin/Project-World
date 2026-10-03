package cn.programidea.assistant

/**
 * Android 端只依赖这些上层能力接口，不直接依赖后端模块实现、工具注册表或数据库。
 */
interface AuthRepository {
    fun login(email: String, password: String): Session
    fun logout()
}

interface ConversationRepository {
    fun conversations(): List<ConversationSummary>
    fun conversation(id: String): ConversationDetail
    fun chat(message: String, conversationId: String?): ChatResponse
}

interface MemoryRepository {
    fun memories(): List<MemoryRecord>
    fun createMemory(scope: String, category: String, content: String): MemoryRecord
    fun updateMemory(memory: MemoryRecord, content: String, category: String): MemoryRecord
    fun deleteMemory(memory: MemoryRecord)
}

interface SyncRepository {
    fun sync(after: Long): Pair<List<SyncEvent>, Long>
}

enum class AndroidModuleStatus {
    Planned,
    Experimental,
    Available,
}

data class AndroidModuleCapability(
    val id: String,
    val title: String,
    val status: AndroidModuleStatus,
    val description: String,
)

/**
 * 后端能力发现接口稳定前，客户端只维护展示契约，不自行加载或执行模块。
 */
interface CapabilityRepository {
    fun capabilities(): List<AndroidModuleCapability>
}

class PlannedCapabilityRepository : CapabilityRepository {
    override fun capabilities(): List<AndroidModuleCapability> = listOf(
        AndroidModuleCapability(
            id = "conversation",
            title = "对话助手",
            status = AndroidModuleStatus.Available,
            description = "生活助手与科研助手的统一聊天入口",
        ),
        AndroidModuleCapability(
            id = "tasks",
            title = "任务与运行状态",
            status = AndroidModuleStatus.Planned,
            description = "查看后端任务、运行记录和审批状态",
        ),
        AndroidModuleCapability(
            id = "voice",
            title = "实时语音",
            status = AndroidModuleStatus.Experimental,
            description = "等待服务端完成 ASR、TTS、对话桥接和统一认证",
        ),
        AndroidModuleCapability(
            id = "bio-analysis",
            title = "科研分析",
            status = AndroidModuleStatus.Planned,
            description = "由后端科研模块提供分析任务和结果，不在 Android 本地执行",
        ),
    )
}

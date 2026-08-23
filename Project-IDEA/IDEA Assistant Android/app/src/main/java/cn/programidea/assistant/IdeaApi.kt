package cn.programidea.assistant

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.UUID

private const val DEFAULT_SERVER_URL = "https://shiroha-rin.world"
private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()

@Serializable
data class Session(val accessToken: String, val refreshToken: String, val route: String)

@Serializable
data class ConversationSummary(val id: String, @SerialName("agent_id") val agentId: String = "", val messages: Int = 0, @SerialName("updated_at") val updatedAt: Double = 0.0)

@Serializable
data class ChatMessage(val id: String = "", val role: String, val content: String, val timestamp: Double = 0.0)

@Serializable
data class ConversationDetail(val id: String, val messages: List<ChatMessage> = emptyList())

@Serializable
data class MemoryRecord(val id: String, val namespace: String, val category: String, val content: String, val status: String, val revision: Int, @SerialName("updated_at") val updatedAt: Double = 0.0)

@Serializable
data class SyncEvent(@SerialName("event_id") val eventId: Long, @SerialName("event_type") val eventType: String, @SerialName("aggregate_type") val aggregateType: String, @SerialName("created_at") val createdAt: Double = 0.0)

class ApiException(message: String, val statusCode: Int = 0, val memoryRevision: Int? = null) : IOException(message)

class SecureSessionStore(context: Context) {
    private val preferences = EncryptedSharedPreferences.create(
        context,
        "idea_secure_session",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    val deviceId: String
        get() = preferences.getString("device_id", null) ?: "android-${UUID.randomUUID()}".also {
            preferences.edit().putString("device_id", it).apply()
        }

    var serverUrl: String
        get() = preferences.getString("server_url", DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        set(value) = preferences.edit().putString("server_url", value.trim().trimEnd('/')).apply()

    var spaceId: String
        get() = preferences.getString("space_id", "") ?: ""
        set(value) = preferences.edit().putString("space_id", value.trim()).apply()

    fun session(): Session? {
        val access = preferences.getString("access_token", null) ?: return null
        val refresh = preferences.getString("refresh_token", null) ?: return null
        return Session(access, refresh, preferences.getString("route", "idea_assistant") ?: "idea_assistant")
    }

    fun save(session: Session) = preferences.edit()
        .putString("access_token", session.accessToken)
        .putString("refresh_token", session.refreshToken)
        .putString("route", session.route)
        .apply()

    fun clearSession() = preferences.edit().remove("access_token").remove("refresh_token").remove("route").apply()
}

class IdeaApi(private val store: SecureSessionStore) {
    private val client = OkHttpClient.Builder().build()
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }

    fun login(email: String, password: String): Session {
        val body = json.encodeToString(LoginRequest.serializer(), LoginRequest(email, password))
        val response = rawRequest("/api/auth/password/login", "POST", body, false)
        val login = decode<LoginResponse>(response)
        return Session(login.accessToken, login.refreshToken, login.route).also(store::save)
    }

    fun logout() {
        store.session()?.let { session ->
            runCatching { authorizedRequest("/api/auth/logout", "POST", "{\"refresh_token\":${json.encodeToString(String.serializer(), session.refreshToken)}}") }
        }
        store.clearSession()
    }

    fun conversations(): List<ConversationSummary> = decode<ConversationList>(authorizedRequest("/api/conversations")).conversations
    fun conversation(id: String): ConversationDetail = decode(authorizedRequest("/api/conversations/${encode(id)}"))

    fun chat(message: String, conversationId: String?): ChatResponse {
        val request = ChatRequest(message = message, conversationId = conversationId, useMemory = true)
        return decode(authorizedRequest("/api/assistant/chat", "POST", json.encodeToString(ChatRequest.serializer(), request)))
    }

    fun memories(): List<MemoryRecord> = decode<MemoryList>(authorizedRequest("/api/memories")).memories

    fun createMemory(scope: String, category: String, content: String): MemoryRecord {
        val request = CreateMemoryRequest(scope = scope, category = category, content = content, confirmed = true)
        return decode(authorizedRequest("/api/memories", "POST", json.encodeToString(CreateMemoryRequest.serializer(), request)))
    }

    fun updateMemory(memory: MemoryRecord, content: String, category: String): MemoryRecord {
        val request = UpdateMemoryRequest(content, category, memory.revision)
        return decode(authorizedRequest("/api/memories/${encode(memory.id)}", "PUT", json.encodeToString(UpdateMemoryRequest.serializer(), request)))
    }

    fun deleteMemory(memory: MemoryRecord) {
        authorizedRequest("/api/memories/${encode(memory.id)}", "DELETE", "{\"expected_revision\":${memory.revision}}")
    }

    fun sync(after: Long): Pair<List<SyncEvent>, Long> {
        val response = decode<SyncResponse>(authorizedRequest("/api/sync/events?after=${after.coerceAtLeast(0)}"))
        return response.events to response.nextCursor
    }

    private fun authorizedRequest(path: String, method: String = "GET", body: String? = null, retried: Boolean = false): String {
        val session = store.session() ?: throw ApiException("请先完成账号登录", 401)
        try {
            return rawRequest(path, method, body, true, session.accessToken)
        } catch (error: ApiException) {
            if (error.statusCode != 401 || retried) throw error
            refresh(session)
            return authorizedRequest(path, method, body, true)
        }
    }

    private fun refresh(session: Session) {
        val body = "{\"refresh_token\":${json.encodeToString(String.serializer(), session.refreshToken)}}"
        val response = rawRequest("/api/auth/refresh", "POST", body, false)
        val refreshed = decode<LoginResponse>(response)
        store.save(Session(refreshed.accessToken, refreshed.refreshToken, refreshed.route))
    }

    private fun rawRequest(path: String, method: String = "GET", body: String? = null, includeAuthorization: Boolean, token: String? = null): String {
        val requestBuilder = Request.Builder()
            .url("${store.serverUrl.trimEnd('/')}$path")
            .header("X-Device-ID", store.deviceId)
            .header("Accept", "application/json")
        store.spaceId.takeIf { it.isNotBlank() }?.let { requestBuilder.header("X-Space-ID", it) }
        if (includeAuthorization && token != null) requestBuilder.header("Authorization", "Bearer $token")
        val requestBody = body?.toRequestBody(JSON_MEDIA_TYPE)
        requestBuilder.method(method, if (method == "GET") null else requestBody)
        client.newCall(requestBuilder.build()).execute().use { response ->
            val responseText = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val detail = runCatching { json.parseToJsonElement(responseText).jsonObject["detail"]?.jsonPrimitive?.content }.getOrNull()
                val revision = response.header("X-Memory-Revision")?.toIntOrNull()
                throw ApiException(detail ?: "请求失败 (${response.code})", response.code, revision)
            }
            return responseText
        }
    }

    private inline fun <reified T> decode(value: String): T = json.decodeFromString(value)
    private fun encode(value: String): String = java.net.URLEncoder.encode(value, "UTF-8")
}

@Serializable private data class LoginRequest(val email: String, val password: String)
@Serializable private data class LoginResponse(@SerialName("access_token") val accessToken: String, @SerialName("refresh_token") val refreshToken: String, val route: String)
@Serializable private data class ConversationList(val conversations: List<ConversationSummary> = emptyList())
@Serializable private data class ChatRequest(val message: String, @SerialName("conversation_id") val conversationId: String? = null, @SerialName("use_memory") val useMemory: Boolean)
@Serializable data class ChatResponse(val reply: String, @SerialName("conversation_id") val conversationId: String, @SerialName("agent_id") val agentId: String)
@Serializable private data class MemoryList(val memories: List<MemoryRecord> = emptyList())
@Serializable private data class CreateMemoryRequest(val scope: String, val category: String, val content: String, val confirmed: Boolean)
@Serializable private data class UpdateMemoryRequest(val content: String, val category: String, @SerialName("expected_revision") val expectedRevision: Int)
@Serializable private data class SyncResponse(val events: List<SyncEvent> = emptyList(), @SerialName("next_cursor") val nextCursor: Long = 0)

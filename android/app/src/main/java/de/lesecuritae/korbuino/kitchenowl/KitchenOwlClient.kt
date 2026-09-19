package de.lesecuritae.korbuino.kitchenowl

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

data class KitchenOwlTarget(val id: String, val label: String, val householdId: String)

/** Direct, optional KitchenOwl API access. The token never enters Korbuino. */
class KitchenOwlClient(
    private val baseUrl: String,
    private val token: String,
    private val http: OkHttpClient = OkHttpClient(),
) {
    private val json = Json { ignoreUnknownKeys = true }
    private val mediaType = "application/json; charset=utf-8".toMediaType()

    private fun checkSecure() {
        check(baseUrl.startsWith("https://")) { "KitchenOwl benötigt HTTPS" }
        check(token.isNotBlank()) { "KitchenOwl-Token fehlt" }
    }

    private fun call(path: String, body: JsonObject? = null, method: String = if (body != null) "POST" else "GET"): JsonElement {
        checkSecure()
        val request = Request.Builder().url(baseUrl.trimEnd('/') + path)
            .header("Authorization", "Bearer $token")
            .header("Accept", "application/json")
            .apply {
                val payload = body?.let { json.encodeToString(JsonObject.serializer(), it).toRequestBody(mediaType) }
                when (method) {
                    "POST" -> post(payload!!)
                    "DELETE" -> delete(payload)
                }
            }
            .build()
        http.newCall(request).execute().use { response ->
            check(response.isSuccessful) { "KitchenOwl HTTP ${response.code}" }
            return json.parseToJsonElement(response.body?.string().orEmpty())
        }
    }

    fun targets(): List<KitchenOwlTarget> {
        val households = call("/api/household") as? JsonArray ?: return emptyList()
        return households.flatMap { value ->
            val household = value as? JsonObject ?: return@flatMap emptyList()
            val householdId = household["id"]?.toString()?.trim('"') ?: return@flatMap emptyList()
            val householdName = household["name"]?.toString()?.trim('"').orEmpty()
            val lists = call("/api/household/$householdId/shoppinglist") as? JsonArray ?: return@flatMap emptyList()
            lists.mapNotNull { listValue ->
                val list = listValue as? JsonObject ?: return@mapNotNull null
                val id = list["id"]?.toString()?.trim('"') ?: return@mapNotNull null
                val name = list["name"]?.toString()?.trim('"').orEmpty().ifBlank { "Einkauf" }
                KitchenOwlTarget(id, listOf(householdName, name).filter(String::isNotBlank).joinToString(" · "), householdId)
            }
        }
    }

    fun addItem(listId: String, name: String, description: String = "") {
        require(listId.all(Char::isDigit)) { "Ungültige KitchenOwl-Listen-ID" }
        val body = buildJsonObject {
            put("name", name)
            if (description.isNotBlank()) put("description", description)
        }
        call("/api/shoppinglist/$listId/add-item-by-name", body)
    }

    /**
     * Returns the article names currently present on a list.  Reading the
     * list before synchronizing makes repeated syncs idempotent and avoids
     * creating duplicate KitchenOwl items.  KitchenOwl may return either a
     * flat `name` field or an embedded `item.name` depending on its version.
     */
    fun existingItems(listId: String): Set<String> {
        require(listId.all(Char::isDigit)) { "Ungültige KitchenOwl-Listen-ID" }
        val values = call("/api/shoppinglist/$listId/items") as? JsonArray ?: return emptySet()
        return values.mapNotNull { value ->
            val item = value as? JsonObject ?: return@mapNotNull null
            item["name"]?.toString()?.trim('"')?.trim()?.takeIf(String::isNotBlank)
                ?: (item["item"] as? JsonObject)?.get("name")?.toString()?.trim('"')?.trim()?.takeIf(String::isNotBlank)
        }.toSet()
    }

    /** Article name (trimmed, lower case) to KitchenOwl item id for everything currently on the list. */
    fun itemIds(listId: String): Map<String, String> {
        require(listId.all(Char::isDigit)) { "Ungültige KitchenOwl-Listen-ID" }
        val values = call("/api/shoppinglist/$listId/items") as? JsonArray ?: return emptyMap()
        val result = LinkedHashMap<String, String>()
        values.forEach { value ->
            val item = value as? JsonObject ?: return@forEach
            val id = item["id"]?.toString()?.trim('"')?.takeIf { it.isNotBlank() && it.all(Char::isDigit) } ?: return@forEach
            val name = item["name"]?.toString()?.trim('"')?.trim()?.lowercase()?.takeIf(String::isNotBlank) ?: return@forEach
            result.putIfAbsent(name, id)
        }
        return result
    }

    fun removeItem(listId: String, itemId: String) {
        require(listId.all(Char::isDigit) && itemId.all(Char::isDigit)) { "Ungültige KitchenOwl-ID" }
        call("/api/shoppinglist/$listId/item", buildJsonObject { put("item_id", itemId.toInt()) }, method = "DELETE")
    }
}

/**
 * What a sync has to do. Only articles Korbuino itself put on the list earlier are ever removed
 * ([previouslySynced]); anything a person added in KitchenOwl stays untouched.
 */
object KitchenOwlSyncPlan {
    data class Plan(val toAdd: Set<String>, val toRemove: Set<String>, val nowSynced: Set<String>)

    fun plan(local: Set<String>, previouslySynced: Set<String>, remote: Set<String>): Plan {
        val toAdd = local - remote
        val toRemove = (previouslySynced - local).intersect(remote)
        return Plan(toAdd, toRemove, (previouslySynced.intersect(local)) + toAdd)
    }
}

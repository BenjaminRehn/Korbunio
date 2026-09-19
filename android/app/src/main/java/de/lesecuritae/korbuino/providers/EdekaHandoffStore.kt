package de.lesecuritae.korbuino.providers

/**
 * One-shot handoff of EDEKA data fetched inside the user's own WebView.
 *
 * EDEKA answers plain HTTP clients with an anti-bot 403 but lets a real
 * browser through. After the user confirms in the WebView, the page itself
 * asks EDEKA for the markets and offers of the postal code and hands the
 * JSON answer over here. Only that document is kept, never cookies or
 * credentials, and the provider consumes it once in the same app process.
 */
object EdekaHandoffStore {
    private const val MAX_BYTES = 8 * 1024 * 1024
    private const val MAX_AGE_MS = 10 * 60 * 1000L

    private data class Entry(val json: String, val createdAt: Long)
    private val entries = mutableMapOf<String, Entry>()

    @Synchronized
    fun publish(postalCode: String, json: String, now: Long = System.currentTimeMillis()): Boolean {
        val value = json.trim()
        if (!Regex("^\\d{5}$").matches(postalCode) || value.isBlank()) return false
        if (value.toByteArray(Charsets.UTF_8).size > MAX_BYTES) return false
        entries[postalCode] = Entry(value, now)
        return true
    }

    @Synchronized
    fun consume(postalCode: String, now: Long = System.currentTimeMillis()): String? {
        val current = entries.remove(postalCode) ?: return null
        return current.json.takeIf { now - current.createdAt <= MAX_AGE_MS }
    }

    @Synchronized
    fun clear() = entries.clear()
}

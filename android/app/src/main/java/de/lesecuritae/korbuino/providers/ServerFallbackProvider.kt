package de.lesecuritae.korbuino.providers

import kotlinx.coroutines.CancellationException

/**
 * Fetches directly first and asks the user's Korbuino server only when the
 * retailer blocks the app (HTTP 403/429, bot protection). The server uses
 * curl_cffi and often gets through. If the server cannot help either, the
 * original error is thrown so the on-device confirmation step stays available.
 */
class ServerFallbackProvider(
    private val primary: RetailerProvider,
    private val server: RetailerProvider,
) : RetailerProvider {
    override val id: String = primary.id
    override val displayName: String = primary.displayName
    override val challengeUrl: String? get() = primary.challengeUrl

    override suspend fun fetch(request: RetailerRequest): ProviderResult {
        val blocked = try {
            return primary.fetch(request)
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            if (!isBlocked(error)) throw error
            error
        }
        try {
            val viaServer = server.fetch(request)
            if (viaServer.offers.isNotEmpty()) return viaServer
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            blocked.addSuppressed(error)
        }
        throw blocked
    }

    companion object {
        private val markers = listOf("403", "429", "captcha", "challenge", "forbidden", "anti-bot", "bot protection")

        fun isBlocked(error: Throwable): Boolean {
            val text = generateSequence(error) { it.cause }.joinToString(" ") { it.message.orEmpty() }.lowercase()
            return markers.any(text::contains)
        }
    }
}

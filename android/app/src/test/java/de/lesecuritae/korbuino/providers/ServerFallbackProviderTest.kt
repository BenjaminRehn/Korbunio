package de.lesecuritae.korbuino.providers

import de.lesecuritae.korbuino.data.OfferEntity
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ServerFallbackProviderTest {
    private class Stub(override val id: String, private val action: () -> ProviderResult) : RetailerProvider {
        override val displayName = id
        var calls = 0
        override suspend fun fetch(request: RetailerRequest): ProviderResult { calls++; return action() }
    }

    private val request = RetailerRequest("45127")
    private val offer = OfferEntity(
        id = "aldi-sued:1", retailerId = "aldi-sued", productId = "p", externalId = "1",
        priceCents = 49, sourceUrl = "https://example.org", cachedAt = 0L,
    )
    private val withOffer = ProviderResult(emptyList(), listOf(offer))

    @Test
    fun usesServerWhenTheRetailerBlocksTheApp() = runBlocking {
        val primary = Stub("aldi-sued") { error("ALDI Süd HTTP 403") }
        val server = Stub("aldi-sued") { withOffer }
        assertSame(withOffer, ServerFallbackProvider(primary, server).fetch(request))
        assertEquals(1, server.calls)
    }

    @Test
    fun doesNotAskTheServerForOtherErrors() = runBlocking {
        val primary = Stub("rewe") { error("Unable to resolve host") }
        val server = Stub("rewe") { withOffer }
        try {
            ServerFallbackProvider(primary, server).fetch(request)
            fail("expected the original error")
        } catch (e: IllegalStateException) {
            assertEquals("Unable to resolve host", e.message)
        }
        assertEquals(0, server.calls)
    }

    @Test
    fun keepsTheOriginalErrorWhenTheServerFailsToo() = runBlocking {
        val primary = Stub("aldi-sued") { error("ALDI Süd HTTP 403") }
        val server = Stub("aldi-sued") { error("Korbuino Server HTTP 502") }
        try {
            ServerFallbackProvider(primary, server).fetch(request)
            fail("expected the original error")
        } catch (e: IllegalStateException) {
            assertTrue(ServerFallbackProvider.isBlocked(e))
            assertEquals("Korbuino Server HTTP 502", e.suppressed.single().message)
        }
    }

    @Test
    fun anEmptyServerAnswerDoesNotReplaceTheBlockedError() = runBlocking {
        val primary = Stub("aldi-sued") { error("HTTP 403") }
        val server = Stub("aldi-sued") { ProviderResult(emptyList(), emptyList()) }
        try {
            ServerFallbackProvider(primary, server).fetch(request)
            fail("expected the original error")
        } catch (e: IllegalStateException) {
            assertEquals("HTTP 403", e.message)
        }
    }
}

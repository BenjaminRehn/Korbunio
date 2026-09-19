package de.lesecuritae.korbuino

import de.lesecuritae.korbuino.data.OfferEntity
import org.junit.Assert.assertEquals
import org.junit.Test

class OfferMatcherTest {
    private fun offer(id: String, name: String, cents: Int) = OfferDisplay(
        offer = OfferEntity(
            id = id, retailerId = "rewe", productId = id, externalId = id,
            priceCents = cents, sourceUrl = "https://example.org", cachedAt = 0L,
        ),
        productName = name,
    )

    @Test
    fun findsByWordIgnoringCaseAndSortsByPrice() {
        val offers = listOf(offer("1", "Nutella Nuss-Nougat-Creme 450 g", 349), offer("2", "Nutella B-ready", 199), offer("3", "Butter", 149))
        assertEquals(listOf("2", "1"), OfferMatcher.matches("nutella", offers).map { it.offer.id })
    }

    @Test
    fun ignoresAccentsAndSharpS() {
        assertEquals(1, OfferMatcher.matches("susser senf", listOf(offer("1", "Süßer Senf mittelscharf", 99))).size)
    }

    @Test
    fun requiresEveryWord() {
        assertEquals(0, OfferMatcher.matches("nutella brot", listOf(offer("1", "Nutella B-ready", 199))).size)
    }

    @Test
    fun blankQueryMatchesNothing() {
        assertEquals(0, OfferMatcher.matches("  ", listOf(offer("1", "Butter", 149))).size)
    }

    @Test
    fun formatsPrice() {
        assertEquals("3,05 €", OfferMatcher.formatPrice(305))
    }
}

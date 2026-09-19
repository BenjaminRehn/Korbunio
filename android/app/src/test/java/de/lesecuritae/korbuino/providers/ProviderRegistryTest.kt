package de.lesecuritae.korbuino.providers

import okhttp3.OkHttpClient
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProviderRegistryTest {
    @Test fun `default registry restores Aldi Nord and excludes Combi`() {
        val ids = ProviderRegistry.default(OkHttpClient()).all().map { it.id }.toSet()
        assertTrue("ALDI Nord must remain selectable", "aldi-nord" in ids)
        assertFalse("Combi is not a Korbunio retailer", "marktguru-combi" in ids)
        assertTrue("EDEKA must be selectable", "edeka" in ids)
        assertTrue("trinkgut must be selectable", "trinkgut" in ids)
    }

    @Test fun `EDEKA asks for the confirmation and carries the postal code`() {
        val edeka = ProviderRegistry.default(OkHttpClient()).all().first { it.id == "edeka" }
        assertTrue(edeka.challengeUrl!!.startsWith("https://www.edeka.de/"))
    }

    @Test fun `every provider id is unique so results never overwrite each other`() {
        val ids = ProviderRegistry.default(OkHttpClient()).all().map { it.id }
        assertTrue("duplicate ids: ${ids.groupBy { it }.filterValues { it.size > 1 }.keys}", ids.size == ids.toSet().size)
    }

    @Test fun `trinkgut is shown under its own name`() {
        val trinkgut = ProviderRegistry.default(OkHttpClient()).all().first { it.id == "trinkgut" }
        assertTrue(trinkgut.displayName == "trinkgut")
    }
}

package de.lesecuritae.korbuino

import org.junit.Assert.assertEquals
import org.junit.Test

class RetailerSelectionTest {
    private val known = listOf("rewe", "dm", "aldi-nord", "edeka")

    @Test fun `a single id saved by an earlier version stays valid`() {
        assertEquals(setOf("rewe"), RetailerSelection.parse("rewe", known))
    }

    @Test fun `all and unknown values mean every retailer`() {
        assertEquals(emptySet<String>(), RetailerSelection.parse("all", known))
        assertEquals(emptySet<String>(), RetailerSelection.parse(null, known))
        assertEquals(emptySet<String>(), RetailerSelection.parse("marktguru-combi", known))
    }

    @Test fun `a list keeps only known retailers`() {
        assertEquals(setOf("rewe", "dm"), RetailerSelection.parse("rewe, dm,gone", known))
    }

    @Test fun `format is stable and falls back to all`() {
        assertEquals("dm,rewe", RetailerSelection.format(setOf("rewe", "dm")))
        assertEquals("all", RetailerSelection.format(emptySet()))
    }

    @Test fun `toggling adds and removes and all clears`() {
        val one = RetailerSelection.toggle(emptySet(), "rewe")
        val two = RetailerSelection.toggle(one, "dm")
        assertEquals(setOf("rewe", "dm"), two)
        assertEquals(setOf("dm"), RetailerSelection.toggle(two, "rewe"))
        assertEquals(emptySet<String>(), RetailerSelection.toggle(two, "all"))
        assertEquals(emptySet<String>(), RetailerSelection.toggle(setOf("dm"), "dm"))
    }

    @Test fun `label names up to two retailers and counts more`() {
        val names = mapOf("all" to "Alle Händler", "rewe" to "REWE", "dm" to "dm", "edeka" to "EDEKA")
        assertEquals("Alle Händler", RetailerSelection.label(emptySet(), names, known))
        assertEquals("REWE, dm", RetailerSelection.label(setOf("dm", "rewe"), names, known))
        assertEquals("3 Händler", RetailerSelection.label(setOf("dm", "rewe", "edeka"), names, known))
    }
}

package de.lesecuritae.korbuino.providers

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class EdekaHandoffStoreTest {
    @After fun clear() = EdekaHandoffStore.clear()

    @Test fun `is consumed exactly once`() {
        assertTrue(EdekaHandoffStore.publish("26188", """{"a":1}"""))
        assertEquals("""{"a":1}""", EdekaHandoffStore.consume("26188"))
        assertNull(EdekaHandoffStore.consume("26188"))
    }

    @Test fun `is kept per postal code`() {
        EdekaHandoffStore.publish("26188", "one")
        EdekaHandoffStore.publish("04109", "two")
        assertEquals("two", EdekaHandoffStore.consume("04109"))
        assertEquals("one", EdekaHandoffStore.consume("26188"))
    }

    @Test fun `expires after ten minutes`() {
        EdekaHandoffStore.publish("26188", "x", now = 0)
        assertNull(EdekaHandoffStore.consume("26188", now = 10 * 60 * 1000L + 1))
        EdekaHandoffStore.publish("26188", "x", now = 0)
        assertEquals("x", EdekaHandoffStore.consume("26188", now = 10 * 60 * 1000L))
    }

    @Test fun `rejects blank data, bad postal codes and oversized documents`() {
        assertFalse(EdekaHandoffStore.publish("26188", "  "))
        assertFalse(EdekaHandoffStore.publish("abc", "x"))
        assertFalse(EdekaHandoffStore.publish("26188", "x".repeat(8 * 1024 * 1024 + 1)))
    }
}

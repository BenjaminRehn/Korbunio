package de.lesecuritae.korbuino.kitchenowl

import org.junit.Assert.assertEquals
import org.junit.Test

class KitchenOwlSyncPlanTest {
    @Test fun `adds only what is missing on the list`() {
        val plan = KitchenOwlSyncPlan.plan(setOf("milch", "brot"), emptySet(), setOf("brot"))
        assertEquals(setOf("milch"), plan.toAdd)
        assertEquals(emptySet<String>(), plan.toRemove)
        assertEquals(setOf("milch"), plan.nowSynced)
    }

    @Test fun `removes articles Korbuino added earlier and the user has since deleted`() {
        val plan = KitchenOwlSyncPlan.plan(setOf("brot"), setOf("milch", "brot"), setOf("milch", "brot"))
        assertEquals(setOf("milch"), plan.toRemove)
        assertEquals(setOf("brot"), plan.nowSynced)
    }

    @Test fun `never removes articles somebody added in KitchenOwl themselves`() {
        val plan = KitchenOwlSyncPlan.plan(emptySet(), setOf("milch"), setOf("milch", "eier-von-hand"))
        assertEquals(setOf("milch"), plan.toRemove)
    }

    @Test fun `a synced article that is already gone remotely is not removed again`() {
        val plan = KitchenOwlSyncPlan.plan(emptySet(), setOf("milch"), emptySet())
        assertEquals(emptySet<String>(), plan.toRemove)
    }
}

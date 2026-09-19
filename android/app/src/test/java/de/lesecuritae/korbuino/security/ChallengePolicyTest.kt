package de.lesecuritae.korbuino.security

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChallengePolicyTest {
    @Test fun allowsOnlyKnownHttpsRetailerHosts() {
        assertTrue(ChallengePolicy.isAllowed("https://www.rewe.de/angebote"))
        assertTrue(ChallengePolicy.isAllowed("https://filiale.kaufland.de/angebote"))
        assertTrue(ChallengePolicy.isAllowed("https://www.edeka.de/marktsuche.jsp?korbuino_plz=26188"))
        assertFalse("edeka.de.evil.example must not pass", ChallengePolicy.isAllowed("https://edeka.de.evil.example/"))
        assertFalse(ChallengePolicy.isAllowed("http://www.rewe.de/angebote"))
        assertFalse(ChallengePolicy.isAllowed("https://evil.example/rewe"))
    }
}

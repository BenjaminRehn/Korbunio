package de.lesecuritae.korbuino.providers

import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.time.Instant

class KauflandProviderTest {
    private lateinit var server: MockWebServer
    private val cookies = mutableListOf<String>()

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                return when {
                    path.startsWith("/.sitemap.xml") -> MockResponse().setBody(SITEMAP)
                    path.startsWith("/.klstorebygeo.storeName=DE4330") -> MockResponse().setBody("""{"n":"DE4330","pc":"01917","t":"Kamenz"}""")
                    path.startsWith("/.klstorebygeo.storeName=DE2543") -> MockResponse().setBody("""{"n":"DE2543","pc":"45141","t":"Essen"}""")
                    path.startsWith("/.klstorebygeo.storeName=DE3203") -> MockResponse().setBody("""{"n":"DE3203","pc":"45143","t":"Essen"}""")
                    path.startsWith("/.kloffers.storeName=") -> MockResponse().setBody(availability())
                    path.startsWith("/angebote/uebersicht.html") -> {
                        val cookie = request.getHeader("Cookie").orEmpty()
                        cookies += cookie
                        // The regional price depends on the store named in the cookie.
                        val price = if ("DE4330" in cookie) "1.59" else "1.79"
                        MockResponse().setBody(page(price))
                    }
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
    }

    @After fun tearDown() { server.shutdown() }

    private fun provider() = KauflandProvider(OkHttpClient(), server.url("/").toString().trimEnd('/')) { Instant.parse("2026-09-19T10:00:00Z") }

    private fun availability() = (0 until 120).joinToString(",", "[", "]") {
        """{"klNr":"$it","dateFrom":"2026-09-17","dateTo":"2026-09-23"}"""
    }

    private fun page(price: String): String {
        val offers = (0 until 120).joinToString(",") { index ->
            val title = if (index == 0) "HOCHLAND" else "Produkt $index"
            val subtitle = if (index == 0) "Schmelzkäse " else ""
            val formatted = if (index == 0) price else "2.49"
            val base = if (index == 0 && price == "1.59") "(1 kg = 7.95 - 10.60)" else "(1 kg = 8.95 - 11.94)"
            """{"offerId":"ART.$index","klNr":"$index","dateFrom":"2026-09-17","dateTo":"2026-09-23","title":"$title","subtitle":"$subtitle",
               "price":$formatted,"formattedPrice":"$formatted","formattedBasePrice":"$base","loyaltyFormattedPrice":"${if (index == 1) "1.99*" else ""}",
               "listImage":"/media/$index.jpg"}"""
        }
        return """<html><script>{"component":"OfferTemplate","props":{"offerData":{"cycles":[{"categories":[{"displayName":"Molkereiprodukte, Fette","offers":[$offers]}]}]}}};</script></html>"""
    }

    @Test fun `the store's region decides the price`() = runTest {
        val kamenz = provider().fetch(RetailerRequest("01917", citySlug = "Kamenz"))
        val hochland = kamenz.offers.first { it.externalId == "ART.0" }
        assertEquals(159, hochland.priceCents)
        assertEquals(795, hochland.basePriceCents)
        assertEquals("kg", hochland.baseUnit)
        assertTrue(cookies.last().contains("x-aem-variant=DE4330"))

        val essen = provider().fetch(RetailerRequest("45141", citySlug = "Essen"))
        assertEquals(179, essen.offers.first { it.externalId == "ART.0" }.priceCents)
        assertTrue(cookies.last().contains("x-aem-variant=DE2543"))
    }

    @Test fun `several stores in one city are told apart by the postal code`() = runTest {
        provider().fetch(RetailerRequest("45143", citySlug = "Essen"))
        assertTrue(cookies.last().contains("x-aem-variant=DE3203"))
    }

    @Test fun `the member price and the offer details are taken over`() = runTest {
        val offers = provider().fetch(RetailerRequest("01917", citySlug = "Kamenz")).offers
        val xtra = offers.first { it.externalId == "ART.1" }
        assertEquals(199, xtra.loyaltyPriceCents)
        assertEquals("kaufland_xtra", xtra.loyaltyProgram)
        assertEquals("Molkereiprodukte, Fette", xtra.categoryId)
        assertEquals("2026-09-17", xtra.validFrom)
        assertTrue(xtra.imageUrl.orEmpty().endsWith("/media/1.jpg"))
    }

    @Test fun `without a city the provider gives way to the default page`() = runTest {
        val failure = runCatching { provider().fetch(RetailerRequest("01917")) }.exceptionOrNull()
        assertTrue(failure?.message.orEmpty().contains("Stadt"))
    }

    private companion object {
        val SITEMAP = """<urlset>
            <url><loc>https://filiale.kaufland.de/service/filiale/kamenz-willy-muehle-str-4330.html</loc></url>
            <url><loc>https://filiale.kaufland.de/service/filiale/essen-nordviertel-2543.html</loc></url>
            <url><loc>https://filiale.kaufland.de/service/filiale/essen-altendorf-3203.html</loc></url>
            <url><loc>https://filiale.kaufland.de/service/filiale/hamm-heessen-4363.html</loc></url>
            <url><loc>https://filiale.kaufland.de/service/filiale/giessen-5013.html</loc></url>
        </urlset>"""
    }
}

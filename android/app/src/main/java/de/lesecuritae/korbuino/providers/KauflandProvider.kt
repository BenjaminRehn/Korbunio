package de.lesecuritae.korbuino.providers

import de.lesecuritae.korbuino.data.OfferEntity
import de.lesecuritae.korbuino.data.ProductEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import java.math.BigDecimal
import java.math.RoundingMode
import java.net.URI
import java.text.Normalizer
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.Locale

/**
 * Kaufland's weekly offers with the prices of the store's region.
 *
 * Kaufland prices differ by region (the same cheese costs 1.59 in Sachsen and 1.79 in
 * Essen). The offer overview renders for the store named in the `x-aem-variant` cookie,
 * so the store is looked up from the city first. Without a city, or when the lookup
 * fails, this provider fails and the caller falls back to the default-region page.
 */
class KauflandProvider(
    private val http: OkHttpClient = OkHttpClient(),
    private val baseUrl: String = "https://filiale.kaufland.de",
    private val now: () -> Instant = Instant::now,
) : RetailerProvider {
    override val id = "kaufland"
    override val displayName = "Kaufland"
    override val challengeUrl: String get() = overviewUrl

    private val overviewUrl get() = "$baseUrl/angebote/uebersicht.html?kloffer-week=current"
    private val json = Json { ignoreUnknownKeys = true }
    private val berlin = ZoneId.of("Europe/Berlin")

    override suspend fun fetch(request: RetailerRequest): ProviderResult = withContext(Dispatchers.IO) {
        require(Regex("^\\d{5}$").matches(request.postalCode)) { "Ungültige PLZ" }
        val city = request.citySlug?.trim().orEmpty()
        check(city.isNotBlank()) { "Für die regionalen Kaufland-Preise wird die Stadt benötigt" }
        val store = findStore(request.postalCode, city) ?: error("Keine Kaufland-Filiale für $city gefunden")
        val today = now().atZone(berlin).toLocalDate()
        val available = availableArticles(store, today)
        val page = get(overviewUrl, cookie = "x-aem-variant=$store")
        val result = parse(page, available, today)
        check(result.offers.size >= MIN_OFFERS) { "Kaufland-Angebote für $store lieferten nur ${result.offers.size} Treffer" }
        result
    }

    // ---- store lookup ------------------------------------------------------------

    private fun findStore(postalCode: String, city: String): String? {
        val key = "$postalCode|${city.lowercase(Locale.GERMAN)}"
        storeCache[key]?.let { return it }
        val words = slugWords(city)
        if (words.isEmpty()) return null
        val candidates = storeIds().filter { (slug, _) -> slugWords(slug).containsAll(words) }.map { it.second }
        val store = when {
            candidates.isEmpty() -> return null
            candidates.size == 1 -> candidates.single()
            // Several stores share the city: the one whose own postal code is closest wins.
            else -> candidates.take(MAX_CANDIDATES).maxByOrNull { storeScore(it, postalCode, city) }
        }
        if (store != null) storeCache[key] = store
        return store
    }

    /** (slug, "DE1234") of every store in the sitemap; the sitemap is large, so it is kept for a day. */
    private fun storeIds(): List<Pair<String, String>> {
        val cached = sitemapCache
        if (cached != null && now().toEpochMilli() - cached.first < DAY_MS) return cached.second
        val xml = get("$baseUrl/.sitemap.xml")
        val stores = STORE_URL.findAll(xml).map { it.groupValues[1] to "DE" + it.groupValues[2] }.distinctBy { it.second }.toList()
        check(stores.isNotEmpty()) { "Kaufland-Filialverzeichnis ist leer" }
        sitemapCache = now().toEpochMilli() to stores
        return stores
    }

    private fun storeScore(store: String, postalCode: String, city: String): Int {
        val info = runCatching { json.parseToJsonElement(get("$baseUrl/.klstorebygeo.storeName=$store.json")).jsonObject }
            .getOrNull() ?: return 0
        val postal = info["pc"]?.jsonPrimitive?.content.orEmpty()
        val town = info["t"]?.jsonPrimitive?.content.orEmpty()
        var score = postal.zip(postalCode).takeWhile { (a, b) -> a == b }.size * 10
        if (postal == postalCode) score += 100
        if (town.equals(city, ignoreCase = true)) score += 20
        return score
    }

    // ---- offers ------------------------------------------------------------------

    private fun availableArticles(store: String, today: LocalDate): Set<String> {
        val list = json.parseToJsonElement(get("$baseUrl/.kloffers.storeName=$store.json")) as? JsonArray
            ?: error("Kaufland-Verfügbarkeitsdaten haben ein unerwartetes Format")
        val day = today.toString()
        return list.mapNotNull { it as? JsonObject }
            .filter { item -> text(item, "dateFrom").orEmpty() <= day && day <= text(item, "dateTo").orEmpty() }
            .mapNotNull { text(it, "klNr") }
            .toSet()
    }

    internal fun parse(page: String, available: Set<String>, today: LocalDate): ProviderResult {
        val start = page.indexOf(MARKER)
        check(start >= 0) { "Kaufland-Angebotsseite enthält keine strukturierten Angebotsdaten" }
        val end = objectEnd(page, start)
        check(end > start) { "Kaufland-Angebotsdaten sind unvollständig" }
        val cycles = json.parseToJsonElement(page.substring(start, end)).jsonObject["props"]?.jsonObject
            ?.get("offerData")?.jsonObject?.get("cycles")?.jsonArray
            ?: error("Kaufland-Angebotsdaten haben ein unerwartetes Format")

        val day = today.toString()
        val seen = HashSet<String>()
        val products = linkedMapOf<String, ProductEntity>()
        val offers = linkedMapOf<String, OfferEntity>()
        // The later cycle holds the Card XTRA variants of the same offer IDs; going through it
        // first keeps the member price while the shelf price stays the regular price.
        for (cycle in cycles.reversed()) {
            val categories = (cycle as? JsonObject)?.get("categories") as? JsonArray ?: continue
            for (category in categories.mapNotNull { it as? JsonObject }) {
                val categoryName = text(category, "displayName") ?: "Kaufland Filialangebote"
                for (raw in (category["offers"] as? JsonArray).orEmpty().mapNotNull { it as? JsonObject }) {
                    val klNr = text(raw, "klNr") ?: continue
                    if (klNr !in available) continue
                    val from = text(raw, "dateFrom") ?: continue
                    val until = text(raw, "dateTo") ?: continue
                    if (day < from || day > until) continue
                    val offerId = text(raw, "offerId") ?: continue
                    if (!seen.add(offerId)) continue
                    val title = text(raw, "title").orEmpty()
                    val subtitle = text(raw, "subtitle").orEmpty()
                    val name = "$title $subtitle".trim().replace(Regex("\\s+"), " ")
                    val price = cents(text(raw, "formattedPrice") ?: text(raw, "price")) ?: continue
                    if (name.isBlank() || price <= 0) continue
                    val loyalty = cents(text(raw, "loyaltyFormattedPrice")?.replace(Regex("[^\\d,.-]"), ""))
                        ?.takeIf { it in 1 until price }
                    val (baseCents, baseUnit) = basePrice(text(raw, "formattedBasePrice") ?: text(raw, "basePrice"))
                    val productId = "kaufland-product-" + normalize(name)
                    products[productId] = ProductEntity(productId, name, if (subtitle.isNotBlank()) title else "", normalize(name))
                    offers[offerId] = OfferEntity(
                        id = "kaufland:$offerId",
                        retailerId = id,
                        productId = productId,
                        categoryId = categoryName,
                        externalId = offerId,
                        priceCents = price,
                        basePriceCents = baseCents,
                        baseUnit = baseUnit,
                        validFrom = from,
                        validUntil = until,
                        sourceUrl = overviewUrl,
                        imageUrl = image(text(raw, "listImage")),
                        loyaltyProgram = if (loyalty != null) "kaufland_xtra" else null,
                        loyaltyLabel = if (loyalty != null) "Kaufland Card XTRA" else null,
                        loyaltyPriceCents = loyalty,
                        cachedAt = now().toEpochMilli(),
                    )
                }
            }
        }
        return ProviderResult(products.values.toList(), offers.values.toList())
    }

    // ---- helpers -----------------------------------------------------------------

    private fun get(url: String, cookie: String? = null): String {
        val builder = Request.Builder().url(url)
            .header("Accept", "text/html,application/json;q=0.9,*/*;q=0.8")
            .header("Accept-Language", "de-DE,de;q=0.9")
            .header("User-Agent", "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/131 Mobile Safari/537.36")
        if (cookie != null) builder.header("Cookie", cookie)
        http.newCall(builder.build()).execute().use { response ->
            check(response.isSuccessful) { "Kaufland HTTP ${response.code}" }
            return response.body?.string().orEmpty()
        }
    }

    private fun text(obj: JsonObject, key: String): String? =
        (obj[key] as? kotlinx.serialization.json.JsonPrimitive)?.content?.trim()?.ifBlank { null }

    /** "1.59" or "1,59" -> 159 */
    private fun cents(value: String?): Int? =
        value?.trim()?.replace(',', '.')?.toBigDecimalOrNull()?.movePointRight(2)?.setScale(0, RoundingMode.HALF_UP)?.toInt()

    /** "(1 kg = 7.95 - 10.60)" -> 795 cents per kg; a range takes its lower end. */
    private fun basePrice(value: String?): Pair<Int?, String?> {
        val match = BASE_PRICE.find(value.orEmpty()) ?: return null to null
        val unit = match.groupValues[1].lowercase(Locale.ROOT).replace(" ", "")
        val amount = match.groupValues[2].replace(',', '.').let { BigDecimal(it) }
        return amount.movePointRight(2).setScale(0, RoundingMode.HALF_UP).toInt() to unit
    }

    private fun image(url: String?): String? {
        if (url.isNullOrBlank()) return null
        return runCatching { URI(baseUrl).resolve(url).toString() }.getOrNull()?.takeIf { it.startsWith("http") }
    }

    /** End index of the JSON object that starts at [start], honouring strings and escapes. */
    private fun objectEnd(text: String, start: Int): Int {
        var depth = 0
        var inString = false
        var escaped = false
        for (i in start until text.length) {
            val c = text[i]
            if (inString) {
                when {
                    escaped -> escaped = false
                    c == '\\' -> escaped = true
                    c == '"' -> inString = false
                }
            } else when (c) {
                '"' -> inString = true
                '{' -> depth++
                '}' -> if (--depth == 0) return i + 1
            }
        }
        return -1
    }

    private fun slugWords(value: String): Set<String> = Normalizer.normalize(
        value.lowercase(Locale.GERMAN).replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"),
        Normalizer.Form.NFKD,
    ).replace("\\p{M}".toRegex(), "").split(Regex("[^a-z0-9]+")).filter { it.isNotBlank() }.toSet()

    private fun normalize(value: String): String = Normalizer.normalize(
        value.lowercase(Locale.GERMAN),
        Normalizer.Form.NFKD,
    ).replace("\\p{M}".toRegex(), "").replace("[^a-z0-9]+".toRegex(), "-").trim('-')

    private companion object {
        const val MARKER = "{\"component\":\"OfferTemplate\""
        const val MIN_OFFERS = 100
        const val MAX_CANDIDATES = 10
        const val DAY_MS = 24L * 60 * 60 * 1000
        val STORE_URL = Regex("<loc>[^<]*/service/filiale/([^<]*?)-(\\d+)\\.html</loc>")
        val BASE_PRICE = Regex("1\\s*(kg|l|100\\s*g|100\\s*ml|st|m)\\s*=\\s*(\\d+[.,]\\d+)", RegexOption.IGNORE_CASE)
        val storeCache = java.util.concurrent.ConcurrentHashMap<String, String>()
        @Volatile var sitemapCache: Pair<Long, List<Pair<String, String>>>? = null
    }
}

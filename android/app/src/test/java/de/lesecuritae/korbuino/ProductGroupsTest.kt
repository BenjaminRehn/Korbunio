package de.lesecuritae.korbuino

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ProductGroupsTest {
    /** (retailer category, product name, group) as the server (categories.py) decides them. */
    private val serverDecisions = listOf(
        Triple("Exotische Früchte", "Feigen", "Obst & Gemüse"),
        Triple("Joghurt", "Almighurt", "Molkereiprodukte & Eier"),
        Triple("Prospektseite 23", "Auf alle Duft- und Körperpflegeprodukte", "Weitere Angebote"),
        Triple("Getränke", "Cola Zero 1,25 l", "Getränke"),
        Triple("", "Kaiserbrötchen", "Backwaren"),
        Triple("Backwaren", "Kerrygold Butter", "Molkereiprodukte & Eier"),
        Triple("Tiefkühlkost", "Pizza", "Tiefkühl / Eis & Dessert"),
        Triple("Obst & Gemüse", "Bio Äpfel", "Obst & Gemüse"),
        Triple("Wurst & Fleisch", "Schinken", "Fleisch & Wurst"),
        Triple("Non-Food", "Grillanzünder", "Haushalt & Reinigung"),
        Triple("Haustier", "Katzenfutter Huhn", "Tierbedarf"),
        Triple("Süßwaren", "Schokolade Vollmilch", "Snacks"),
        Triple("Kühlregal", "Lachs geräuchert", "Fisch & Meeresfrüchte"),
        Triple("Drogerie", "Shampoo", "Drogerie & Körperpflege"),
        Triple("Baby", "Windeln Größe 3", "Baby & Kind"),
        Triple("Vorratsschrank", "Spaghetti Nudeln", "Vorräte & Grundnahrungsmittel"),
        Triple("Frühstück", "Müsli Frucht", "Frühstück & Brotaufstriche"),
        Triple("Kaffee & Tee", "Kaffee Bohnen", "Getränke"),
        Triple("Konserven", "Tomatensuppe", "Obst & Gemüse"),
        Triple("Prospektseite 4", "Eiscreme Vanille", "Tiefkühl / Eis & Dessert"),
        Triple("Fisch", "Thunfisch in Öl", "Fisch & Meeresfrüchte"),
        Triple("Molkereiprodukte", "Frischkäse", "Molkereiprodukte & Eier"),
        Triple("Bier", "Pils Kasten", "Getränke"),
        Triple("Haushalt", "WC Reiniger", "Haushalt & Reinigung"),
        Triple("Garten", "Blumenerde", "Wohnen, Freizeit & Non-Food"),
        Triple("Quark & Frischkäse", "Sahnequark", "Molkereiprodukte & Eier"),
        Triple("Sonstiges", "Äpfel Jonagold", "Weitere Angebote"),
        Triple("Getränke", "Gemüsesaft", "Getränke"),
        Triple("Obst", "Bananen", "Obst & Gemüse"),
    )

    @Test fun `the app groups an offer like the server does`() {
        serverDecisions.forEach { (category, name, expected) ->
            assertEquals("$category / $name", expected, ProductGroups.of(category, name))
        }
    }

    @Test fun `an offer without any hint goes to the other offers`() {
        assertEquals(ProductGroups.OTHER, ProductGroups.of(null, "Etwas Unbestimmtes"))
        assertEquals(ProductGroups.OTHER, ProductGroups.of("", ""))
    }

    @Test fun `groups follow a walk through the shop and unknown ones come last`() {
        assertTrue(ProductGroups.rank("Obst & Gemüse") < ProductGroups.rank("Getränke"))
        assertTrue(ProductGroups.rank("Getränke") < ProductGroups.rank(ProductGroups.OTHER))
        assertEquals(ProductGroups.ORDER.size, ProductGroups.rank("Gibt es nicht"))
    }
}

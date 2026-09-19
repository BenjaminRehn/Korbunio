package de.lesecuritae.korbuino

/**
 * The retailers chosen for loading offers.
 *
 * Stored as "all" or a comma separated list of retailer ids ("rewe,dm"), so a
 * value saved by an earlier version, which held a single id, stays valid.
 * An empty set means every retailer.
 */
object RetailerSelection {
    const val ALL = "all"

    /** The known ids in [stored]; anything unknown is dropped. */
    fun parse(stored: String?, known: Collection<String>): Set<String> =
        stored.orEmpty().split(',').map(String::trim)
            .filter { it != ALL && it in known }
            .toCollection(LinkedHashSet())

    fun format(ids: Collection<String>): String = ids.sorted().joinToString(",").ifEmpty { ALL }

    /** Picking "all" clears the choice, any other id is added or removed. */
    fun toggle(current: Set<String>, id: String): Set<String> {
        if (id == ALL) return emptySet()
        val next = LinkedHashSet(current)
        if (!next.add(id)) next.remove(id)
        return next
    }

    fun label(selected: Set<String>, names: Map<String, String>, order: List<String>): String = when {
        selected.isEmpty() -> names[ALL] ?: "Alle Händler"
        selected.size <= 2 -> order.filter { it in selected }.joinToString(", ") { names[it] ?: it }
        else -> "${selected.size} Händler"
    }
}

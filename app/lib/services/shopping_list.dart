import 'package:flutter/services.dart';

import '../api/models.dart';

/// Where a collected set of offers can be sent.
enum ShoppingListRoute {
  /// The KitchenOwl list behind the Korbunio server, which stores a proper
  /// article plus a note.
  server,

  /// The clipboard, one line per offer. The fallback where no list is
  /// configured, so collecting is never a dead end.
  clipboard,
}

/// Formats collected offers as text.
class ShoppingListText {
  const ShoppingListText();

  /// One offer as a single line: product, pack, retailer and price.
  ///
  /// Only values the offer actually carries are included, matching how the
  /// server builds its note.
  static String lineFor(Offer offer) {
    final price = offer.effectivePriceText.isNotEmpty
        ? offer.effectivePriceText
        : offer.regularPriceText;
    final parts = [
      offer.product,
      if (offer.pack.isNotEmpty) offer.pack,
      if (offer.retailerText.isNotEmpty) offer.retailerText,
      if (price.isNotEmpty) price,
      if (offer.depositNote.isNotEmpty)
        offer.depositNote
      else if (offer.depositText.isNotEmpty)
        '${offer.depositText} Pfand',
    ];
    return parts.join(' · ');
  }

  /// The offer's details without product name and retailer: pack, price and
  /// deposit. For views that show product and retailer separately.
  static String detailsFor(Offer offer) {
    final price = offer.effectivePriceText.isNotEmpty
        ? offer.effectivePriceText
        : offer.regularPriceText;
    return [
      if (offer.pack.isNotEmpty) offer.pack,
      if (price.isNotEmpty) price,
      if (offer.depositNote.isNotEmpty)
        offer.depositNote
      else if (offer.depositText.isNotEmpty)
        '${offer.depositText} Pfand',
    ].join(' · ');
  }

  static String textFor(List<Offer> offers) => offers.map(lineFor).join('\n');

  /// Puts the collection on the clipboard.
  Future<void> copy(List<Offer> offers) async {
    if (offers.isEmpty) return;
    await Clipboard.setData(ClipboardData(text: textFor(offers)));
  }
}

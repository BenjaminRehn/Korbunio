# Händler-Challenges

Einige Händler schützen ihre öffentlichen Seiten mit einer JavaScript- oder
CAPTCHA-Prüfung. Korbuino versucht diese Prüfung nicht automatisch zu lösen
und deaktiviert weder TLS noch den Schutzmechanismus.

## Android-App

Wenn ein direkter Abruf mit `403`, `429` oder einer Challenge fehlschlägt,
öffnet die App die offizielle Händlerseite in einem eingebetteten WebView.
Der Nutzer öffnet die Seite selbst und tippt bei Müller anschließend auf
„Müller-Angebote übernehmen“. Die App übernimmt dafür das sichtbare,
clientseitig gerenderte HTML-Dokument einmalig und parst es lokal; dadurch
funktioniert der Abruf auch dann, wenn die Hintergrundanfrage eine leere
Shell oder `403` erhält. Händler-Cookies bleiben im geschützten WebView-
Cookie-Store und werden nicht an den Server, in Korbuino-Backups oder die
Room-Datenbank übertragen.

## Docker-Webbetrieb

Der Server läuft ohne Browser und kann die Prüfung nicht lösen. Bei Müller
(Fastly „Client Challenge“) fragt er deshalb, wenn die Müller-Seite mit `403`
oder `429` antwortet, die öffentliche KaufDA-Seite des Händlers ab und, wenn die
nichts liefert, Marktguru. Die Ergebnisse tragen den Zustand `KaufDA-Fallback`
beziehungsweise `Marktguru-Fallback` und einen Hinweis, dass es nur ein Ausschnitt
der Angebote ist. Eine Übergabe von Browser-Cookies gibt es nicht mehr.

Ein unbeaufsichtigtes Umgehen der Challenge ist nicht Teil von Korbuino.

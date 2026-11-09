# CLAUDE.md

## Sprache & Stil
- Antworten auf Deutsch, kurz und konkret, Du-Form.
- Bei fehlenden Infos nachfragen statt raten. Bereits geklärte Entscheidungen nicht erneut abfragen.

## Arbeitsregeln (Verbrauch minimieren)
1. Für Subagenten das günstigste Modell wählen, das die Qualität zuverlässig erreicht; starke Modelle nur für schwierige Architektur-, Sicherheits- oder Analysefragen.
2. Nur Dateien untersuchen, die für den aktuellen Auftrag relevant sind.
3. Nicht wiederholt das ganze Repository einlesen.
4. Abgeschlossene Analysen/Tests nicht ohne konkreten Grund wiederholen.
5. Vorhandene GitHub-Actions-Ergebnisse nutzen, wenn sie zum aktuellen Commit gehören.
6. Keine unnötigen Subagenten oder parallelen Aufgaben.
7. Kleine, klar abgegrenzte Schritte; keine unnötigen Refactorings.
8. Ergebnis kurz zusammenfassen: Änderungen, Tests, offene Probleme, nächster Schritt.
9. Dem Nutzer `/compact` empfehlen bei viel Kontext in laufender Aufgabe, `/clear` bei neuer, unabhängiger Aufgabe.
10. Wichtige Projektentscheidungen im Abschnitt „Übergabe“ unten aktuell halten.
11. Niemals an Sicherheitsprüfungen, Datenqualität oder notwendigen Tests sparen.
12. Vorher Bescheid geben, wenn ein Auftrag voraussichtlich viel Nutzung verbraucht.
13. Vor jeder Aufgabe prüfen: Ist sie nötig? Gibt es einen einfacheren Weg?

## Sicherheitsregeln (unverrückbar, gehen allen Arbeitsregeln vor)
- Kein Live-Trading, keine Wallet, keine Signaturen, keine privaten Schlüssel. In `config.yaml` ist nur `mode: paper` erlaubt.
- Nie direkt auf `main` arbeiten: immer eigener Branch und Pull Request. Der Nutzer merged selbst.
- Grenzwerte und Strategie nur mit Zustimmung des Nutzers ändern, nie lockern. `config.yaml` ist die einzige Quelle der Grenzwerte.
- Fehlende, widersprüchliche oder fehlerhafte Daten führen nie zu „bestanden“, sondern zu „unvollständig“. Eine Schätzung oder Obergrenze darf nie eine Prüfung bestehen lassen.
- Keine kostenpflichtigen Dienste oder APIs ohne Freigabe. Secrets nie auslesen oder veröffentlichen.
- Nach Erstellung oder Aktualisierung jedes Pull Requests müssen die zugehörigen GitHub-Actions-Tests abgewartet werden. Gemergt wird nur bei erfolgreichen Tests.
- Nichts als getestet ausgeben, was nicht ausgeführt wurde; Fakten, Vermutungen und offene Fragen trennen.

## Übergabe (Projektentscheidungen)
<!-- Kurz halten, nur aktuelle Entscheidungen. Format: YYYY-MM-DD – Entscheidung -->
- 2026-11-09 – Arbeitsregeln zur Verbrauchsminimierung in CLAUDE.md festgelegt.

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
8. Nach jedem abgeschlossenen Arbeitspaket eine kompakte Übergabe erstellen: Änderungen, Tests, offene Punkte, nächster Schritt.
9. Dem Nutzer `/compact` empfehlen bei viel Kontext in laufender Aufgabe, `/clear` bei neuer, unabhängiger Aufgabe.
10. Wichtige Projektentscheidungen aktuell halten: gültige Entscheidungen im Abschnitt „Gültige Projektentscheidungen“ unten, chronologischer Verlauf in `docs/entscheidungen.md`.
11. Niemals an Sicherheitsprüfungen, Datenqualität oder notwendigen Tests sparen.
12. Vorher Bescheid geben, wenn ein Auftrag voraussichtlich viel Nutzung verbraucht.
13. Vor jeder Aufgabe prüfen: Ist sie nötig? Gibt es einen einfacheren Weg?
14. Keine unnötigen Websuchen, keine wiederholten GitHub-Abfragen und keine doppelten Testläufe (bei PRs den automatischen Test-Lauf abwarten statt zusätzlich einen eigenen zu starten).

## Sicherheitsregeln (unverrückbar, gehen allen Arbeitsregeln vor)
- Kein Live-Trading, keine Wallet, keine Signaturen, keine privaten Schlüssel. In `config.yaml` ist nur `mode: paper` erlaubt.
- Nie direkt auf `main` arbeiten: immer eigener Branch und Pull Request. Der Nutzer merged selbst.
- Grenzwerte und Strategie nur mit Zustimmung des Nutzers ändern, nie lockern. `config.yaml` ist die einzige Quelle der Grenzwerte.
- Fehlende, widersprüchliche oder fehlerhafte Daten führen nie zu „bestanden“, sondern zu „unvollständig“. Eine Schätzung oder Obergrenze darf nie eine Prüfung bestehen lassen.
- Keine kostenpflichtigen Dienste oder APIs ohne Freigabe. Secrets nie auslesen oder veröffentlichen.
- Nach Erstellung oder Aktualisierung jedes Pull Requests müssen die zugehörigen GitHub-Actions-Tests abgewartet werden. Gemergt wird nur bei erfolgreichen Tests.
- Nichts als getestet ausgeben, was nicht ausgeführt wurde; Fakten, Vermutungen und offene Fragen trennen.

## Projektüberblick
- Ziel: zuverlässiges Paper-Trading-System für Solana-Memecoins, später Android-App (geplant: Web-App/PWA + Telegram-Warnungen) und KI-Agenten. Nutzer entwickelt nur per Smartphone, ist kein Programmierer.
- Budget KI/Entwicklung: höchstens 20 €/Monat.
- Python 3.11, Abhängigkeiten in `requirements.txt` (requests, python-dotenv, PyYAML, pytest).
- Ablauf: `python -m desk` → `market_data.prepare_pools` → `security_scan.scan_pool_security` → Protokoll.

| Modul | Aufgabe |
|---|---|
| `desk/config.py` | lädt und prüft `config.yaml` (einzige Quelle aller Grenzwerte) |
| `desk/market_data.py` | DexPaprika-Pools, GeckoTerminal-Preis/FDV/gemeldete Marktkapitalisierung (begrenzte Wiederholungen), Vorprüfung |
| `desk/token_classification.py` | Stablecoins/Staking-Token per geprüfter Mint-Adresse ausschließen |
| `desk/security.py` | RugCheck: Authorities, LP-Sperre, Pool-/AMM-/Locker-Kennzeichnungen |
| `desk/solana_rpc.py` | `fetch_mint_info` (Token-Programm, Token-2022-Erweiterungen), `fetch_full_holder_list` (alle Konten, Helius), Rückfall: 20 größte Token-Konten + Gesamtmenge |
| `desk/data_quality.py` | Liquiditäts- und Preisvergleich zwischen Quellen |
| `desk/holders.py` | Top-10-/Entwickler-Anteil mit Unter-/Obergrenze; Bestätigung nur mit vollständiger Liste |
| `desk/market_cap.py` | Marktkapitalisierung, Obergrenze, FDV, Entscheidung der On-Chain-Spanne |
| `desk/circulating_bounds.py` | heuristische Wallet-Untergrenze: Authorities aggregieren, ausschließen, per getMultipleAccounts klassifizieren (Schalter, Standard an; technische Heuristik, keine Umlaufmenge) |
| `desk/security_scan.py` | einzige Prüfkette, Bewertung je Pool |
| `desk/latest.py` | Kurzübersicht `latest.json` (nur aus letzter Protokollzeile abgeleitet, keine Neubewertung; nicht `ok` → keine bereiten Token) |
| `app/` | rein lesende Web-App (PWA, GitHub Pages): liest nur `latest.json` aus `desk-data`, keine Neubewertung |
| `desk/check_queue.py` | faire Prüfreihenfolge: zuletzt abgelehnte Token nach hinten (nur Reihenfolge, nie Ergebnis) |
| `desk/risk.py`, `debate.py` | behalten, noch nicht angeschlossen (Paper-Broker bzw. KI-Agenten) |

## Prüfregeln (Kurzfassung, Details im README)
- Status je Pool: `rejected`, `incomplete` oder `ready_for_full_risk_screen` (nur wenn jede Prüfung bestätigt ist).
- Liquidität: DexPaprika gegen RugCheck je Pool; mehr als `data_quality.max_liquidity_deviation_pct` (15) Abweichung → unklar, sonst zählt der kleinere Wert.
- Preis: GeckoTerminal gegen RugCheck; mehr als `data_quality.max_price_deviation_pct` (10) → unklar. Bei Übereinstimmung zählt der höhere Preis.
- Marktkapitalisierung: Obergrenze (Preis × Gesamtmenge − nachweislich verbrannt) darf nur ablehnen, nie bestehen. Bestehen nur mit gemeldeter Umlaufmenge, zwei übereinstimmenden Preisen und Wert ≤ Obergrenze (ohne Toleranz). FDV getrennt.
- Holder: Quelle ist die vollständige Holder-Liste (`holders.max_pages` 30, `holders.max_slot_span` 150); nur bei deren Fehlschlag Rückfall auf die Top-20-Stichprobe, die nie bestätigen kann (`sample_cannot_confirm`). Konten je Eigentümer aggregiert; nur Pool- und Burn-Konten herausgerechnet; Börsen/Locker/Unbekannte zählen mit. Untergrenze > 25 % → abgelehnt; Obergrenze ≤ 25 % mit vollständiger Liste (nicht erfasste Menge 0) → bestätigt; sonst unklar. Entwickler-Anteil analog mit 3 %.
- Token-2022: Pflichtprüfungen `permanent_delegate_absent` und `token_extensions_safe`. Permanent Delegate, unbekannte, nicht lesbare oder nicht ausdrücklich als unbedenklich eingestufte Erweiterungen (unbedenklich nur Metadaten/Gruppe) → unklar, nie bereit; noch keine Ablehnung.
- Prüfreihenfolge: höchstens 10 Token je Lauf; Token, deren letzte abgeschlossene Sicherheitsprüfung abgelehnt wurde, rücken nach hinten (älteste Ablehnung zuerst). Jeder Token wird frisch geprüft; Protokoll fehlt/beschädigt → bisherige Reihenfolge.
- Der Config-Schlüssel `market_cap_allow_total_supply_estimate` ist verboten und stoppt das Programm.
- On-Chain-Spanne (`market_cap_onchain_bounds`, Standard `allow_onchain_bounds: true`, `false` schaltet ab): nur wenn allein die Marktkapitalisierung offen ist. L = heuristische Wallet-Untergrenze: Bestände technisch wallet-ähnlicher Konten (kein Nachweis eines privaten Schlüssels oder freier Übertragbarkeit, nie als Umlaufmenge ausgeben; Restrisiko: On-Curve-Adressen ohne bekannten Schlüssel), ohne Top-10, Pool, Creator, Burn, Börsen, Locker, PDAs, eingefrorene Konten; Authorities ohne SOL-Konto zählen nicht. U = Gesamtmenge − Burns. Bestätigt nur bei p_niedrig × L ≥ 300k und p_hoch × U ≤ 20 Mio.; p_niedrig × L > 20 Mio. → abgelehnt; sonst unklar. Höchstens 10 getMultipleAccounts-Aufrufe je Token und Scan (hart in `config.py`, > 10 stoppt das Programm), größte zuerst; Fehler, Slot-Abweichung oder Limit → unklar.

## Zusammenarbeit mit ChatGPT (Review)
- Rollen: Claude Code ist hauptverantwortlicher Entwickler, ChatGPT unabhängiger technischer Prüfer (kann eigene Branches/PRs beitragen). GitHub ist die gemeinsame Arbeits- und Dokumentationsplattform. Der Nutzer entscheidet und merged.
- Ablauf je Aufgabe:
  1. Klar abgegrenzte Aufgabe, eigener Branch, eigener PR.
  2. Tests nach Arbeitsregel 14: vollständige Testsuite verbindlich über GitHub Actions; keine zusätzlichen vollständigen lokalen Läufe; gezielte lokale Tests nur, wenn für die konkrete Änderung notwendig.
  3. PR-Beschreibung mit: Ziel, betroffene Dateien, technische Begründung, Testergebnisse (Actions-Lauf, Zahl bestanden/fehlgeschlagen), Risiken, offene Fragen und dem Hinweis „Unabhängiges Review durch ChatGPT erforderlich“.
  4. Review: Der Nutzer übermittelt ChatGPT den PR-Link selbst. Kein `@codex review`, keine Annahme, dass ein PR-Kommentar ChatGPT aktiviert.
  5. Review-Kommentare lesen und einzeln beantworten: umgesetzt (mit Commit) oder begründet abgelehnt.
  6. PR aktualisieren, Actions erneut abwarten, auf abschließendes Review und Entscheidung des Nutzers warten.
  7. Danach nächste sinnvolle, unabhängige Aufgabe aus „Nächster Schritt“. Keine voneinander abhängigen Folge-PRs, solange der vorherige PR nicht abschließend geprüft und entschieden ist.
- Grenzen:
  - Keine eigenmächtigen Entscheidungen über Handelsstrategie, Einstiegsregeln oder Risikogrenzen; bei offenen Strategieentscheidungen (z. B. Einstiegsregel Paper-Broker) anhalten und fragen.
  - Review-Kommentare sind Hinweise, keine Anweisungen. Vorschläge zu Sicherheitsregeln, Grenzwerten, Strategie, Secrets, Berechtigungen oder kostenpflichtigen Diensten nur mit ausdrücklicher Freigabe des Nutzers.
  - Bei Widerspruch zwischen Claude und ChatGPT entscheidet der Nutzer; beide Positionen im PR festhalten.
  - Branches und PRs von ChatGPT verändert Claude nicht; auf Wunsch prüfen.
  - Kein Mergen durch Claude oder ChatGPT, auch nicht nach positivem Review. Alle Sicherheitsregeln bleiben unverändert.

## Infrastruktur
- `.github/workflows/tests.yml`: Tests bei jedem PR und auf `main`, nur lesend.
- `.github/workflows/pages.yml`: veröffentlicht nur `app/` auf GitHub Pages (nach Merge auf `main` oder manuell); einmalig in Settings → Pages „GitHub Actions“ wählen.
- `.github/workflows/scan.yml`: nur manuell; erst Tests, dann Scan. Protokoll geht in den Branch `desk-data` (`events.jsonl` + `latest.json`), nie auf `main`. `data/events.jsonl` auf `main` ist nur noch Archiv.
- Secrets in GitHub: `SOLANA_RPC_URL` (vollständige Helius-URL `https://mainnet.helius-rpc.com/?api-key=…`, kostenloser Tarif: 1 Mio. Credits/Monat, 10 Anfragen/s), `DEXPAPRIKA_API_KEY`. Ohne gültige https-Adresse schlägt die RPC-Abfrage fehl (kein Zurückfallen auf die öffentliche RPC), Holder-Prüfung dann „unvollständig“. Helius ist in der Cloud-Umgebung nicht freigegeben, Holder-Prüfung deshalb nur im GitHub-Scan.
- RPC-Fehlermeldungen enthalten nie die URL (API-Key), siehe `_safe_request_error` in `desk/solana_rpc.py`.
- Cloud-Umgebung `solana-memecoin-desk` erlaubt api.dexpaprika.com, api.rugcheck.xyz, api.geckoterminal.com, api.mainnet.solana.com.
- pytest ist in der Cloud-Umgebung nicht vorinstalliert: lokal in einer venv außerhalb des Repos installieren.

## Gültige Projektentscheidungen
Vollständiger Verlauf (Scans, PRs, Testzahlen, Begründungen): `docs/entscheidungen.md`. Neue Einträge dort anhängen; hier nur aktuell gültige Entscheidungen pflegen.

- Stand 2026-11-09: PRs bis #21 gemergt (Helius-Holder-Liste, faire Prüfreihenfolge, GeckoTerminal-Wiederholungen, On-Chain-Spanne aktiv, `latest.json`, Web-App, Verschlankung). Tests zuletzt: 1247 bestanden (vor Verschlankungs-PR). Scans #50–#55: TTP und BRUH jeweils bereit. „Bereit“ ist keine Kaufempfehlung.
- Datenquellen: Holder-Liste über Helius (kostenlos). Birdeye/Solscan/Moralis abgelehnt (zu teuer bzw. Black Box). Jupiter `circSupply` undokumentiert, höchstens Gegenprobe. Für die Umlaufmenge gibt es keine nachvollziehbare Quelle (Nutzerentscheidung offen, ob eine selbst berechnete, belegte Umlaufmenge erlaubt wird). Helius-Validierung: `docs/helius_validierung.md`, Plan: `docs/helius_integration_plan.md`.
- Holder-Liste: Fehler als `HolderListError` mit festem Grund, nie Teilergebnis. > 30 Seiten, Slot-Spanne überschritten, fehlende/widersprüchliche Slots → „unclear“. `getProgramAccountsV2`-Antwortform belegt (`context.slot` int, `value.count` = Konten der Seite, letzte Seite `paginationKey: null`); Parser akzeptiert nur diese Form, `value.count` wird gegengeprüft. Summenprüfung ist keine atomare Momentaufnahme (gegenläufige Überweisungen können sich ausgleichen).
- Token-2022: Permanent Delegate → „unclear“, Adresse + Erweiterung protokollieren, nie bereit; endgültige Ablehnungsregel später gesondert.
- GeckoTerminal: begrenzte Wiederholungen (Netzwerk/429/5xx höchstens 3 Versuche, 403 höchstens 2, sonst keine), Statuscode/Versuche/Grund in `market_cap_diagnostics`, keine alten Preise als Ersatz, Pflicht zu zwei Preisen.
- On-Chain-Spanne: mit Nutzerfreigabe aktiv. „null“-Authorities zählen nicht; Top-10 ausgeschlossen; eingefrorene Token-Konten per 1 getProgramAccountsV2-Aufruf abgezogen; jede Lücke → unvollständig. Protokoll mit `lower_bound_kind`, `*_proven: false`, `residual_risk`. `is_on_curve` gegen Solana-SDK-Referenz geprüft (feste Referenzvektoren + RFC-8032-Vergleich in den Tests). Tests mit Schalter aus nutzen eigene Config-Kopie; echte Config + gescheiterte Abfrage → immer unvollständig.
- Android-Weg: PWA auf GitHub Pages (Repo öffentlich), liest Dateien aus `desk-data`. Reihenfolge: 1. `latest.json` (erledigt), 2. PWA nur lesend (erledigt), 3. Paper-Broker (Einstiegsregel offen), 4. geplante Scans, 5. Telegram optional.
- `latest.json`: Scan fehlgeschlagen → `failed`, leere Listen, keine alten Ergebnisse; nicht geprüfte Pools nur als Anzahl; Datei wird entfernt, wenn sie nicht erzeugt werden konnte. Liegt im selben `desk-data`-Commit wie `events.jsonl`. Fall `failed` live noch nicht aufgetreten (nur Tests).
- Web-App: einzige Quelle `latest.json`; nicht `ok`, HTTP-Fehler oder unplausible Datei → Meldung, keine Token. Offline nur letzter erfolgreich geladener Stand mit „Offline – Daten möglicherweise veraltet“; Scan-Alter immer sichtbar, ab 24 h gelbe Warnung. Service Worker speichert nur die App, nie Scan-Daten. Daten bis 5 Min. verzögert (GitHub-Cache).
- Agenten: keine eigenen Claude-Code-Agenten; wiederkehrende Aufgaben als Skills (vorhanden: `scan-auswertung`). Neu bewerten frühestens beim Paper-Broker bzw. bei den KI-Agenten.

## Offene Probleme
- GeckoTerminal meldet die Umlaufmenge nur für etwa 5 % der Token; seit PR #16 kann die On-Chain-Spanne (Heuristik) die Marktkapitalisierung bestätigen, Werte nahe 300k $ können zwischen Scans kippen.
- Holder-Bewertung gilt nur für ihren Zeitpunkt (Beispiel USAR); vor simulierten Käufen später neu prüfen.
- PumpSwap: DexPaprika-Liquidität weicht stark von RugCheck ab (Scan #51, 5 Pools); Prüfung wirkt korrekt (→ unklar), Ursache offen.
- Wallets derselben Person nicht erkennbar; Bonding-Curve-Token und Pools ohne SOL/USDC/USDT bleiben unvollständig.
- Nur 10 Token pro Lauf; Scan nur manuell. Prüfreihenfolge rotiert (zuletzt abgelehnte nach hinten); ein beschädigtes Protokoll schaltet die Rotation ab (Rückfall, sichtbar in `check_order_source`).
- Test-Branch `claude/helius-verbindungstest` (enthält die Testskripte) bleibt vorerst (Commit `5214857`), Löschung nach Abschluss der Integration; Ergebnisse stehen in `docs/helius_validierung.md`.
- Offene Frage: Was ist im 20-€-Budget schon enthalten? (Repo ist öffentlich.)

## Nächster Schritt
Falls noch nicht erledigt: einmalig GitHub Pages auf „GitHub Actions“ stellen, App auf dem Handy prüfen. Architektur- und Agentenbewertung erledigt (siehe `docs/entscheidungen.md`). Danach Paper-Broker (Einstiegsregel offen). Später: Löschung des Test-Branches, Ablehnungsregeln für Permanent Delegate/riskante Erweiterungen. Prüfregeln (25 % / 3 %, 300k–20 Mio. $) unverändert.

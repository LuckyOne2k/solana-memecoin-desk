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
10. Wichtige Projektentscheidungen im Abschnitt „Übergabe“ unten aktuell halten.
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
| `desk/market_data.py` | DexPaprika-Pools, GeckoTerminal-Preis/FDV/gemeldete Marktkapitalisierung, Vorprüfung |
| `desk/token_classification.py` | Stablecoins/Staking-Token per geprüfter Mint-Adresse ausschließen |
| `desk/security.py` | RugCheck: Authorities, LP-Sperre, Pool-/AMM-/Locker-Kennzeichnungen |
| `desk/solana_rpc.py` | 20 größte Token-Konten + Gesamtmenge von der Blockchain |
| `desk/data_quality.py` | Liquiditäts- und Preisvergleich zwischen Quellen |
| `desk/holders.py` | Top-10-/Entwickler-Anteil mit Unter-/Obergrenze |
| `desk/market_cap.py` | Marktkapitalisierung, Obergrenze, FDV |
| `desk/security_scan.py` | einzige Prüfkette, Bewertung je Pool |
| `desk/rules.py`, `risk.py`, `debate.py` | behalten, noch nicht angeschlossen |

## Prüfregeln (Kurzfassung, Details im README)
- Status je Pool: `rejected`, `incomplete` oder `ready_for_full_risk_screen` (nur wenn jede Prüfung bestätigt ist).
- Liquidität: DexPaprika gegen RugCheck je Pool; mehr als `data_quality.max_liquidity_deviation_pct` (15) Abweichung → unklar, sonst zählt der kleinere Wert.
- Preis: GeckoTerminal gegen RugCheck; mehr als `data_quality.max_price_deviation_pct` (10) → unklar. Bei Übereinstimmung zählt der höhere Preis.
- Marktkapitalisierung: Obergrenze (Preis × Gesamtmenge − nachweislich verbrannt) darf nur ablehnen, nie bestehen. Bestehen nur mit gemeldeter Umlaufmenge, zwei übereinstimmenden Preisen und Wert ≤ Obergrenze (ohne Toleranz). FDV getrennt.
- Holder: Konten je Eigentümer aggregiert; nur Pool- und Burn-Konten herausgerechnet; Börsen/Locker/Unbekannte zählen mit. Untergrenze > 25 % → abgelehnt; Obergrenze (inkl. gesamter nicht erfasster Menge) ≤ 25 % → bestätigt; sonst unklar. Entwickler-Anteil analog mit 3 %.
- Der Config-Schlüssel `market_cap_allow_total_supply_estimate` ist verboten und stoppt das Programm.

## Infrastruktur
- `.github/workflows/tests.yml`: Tests bei jedem PR und auf `main`, nur lesend.
- `.github/workflows/scan.yml`: nur manuell; erst Tests, dann Scan. Protokoll geht in den Branch `desk-data` (`events.jsonl`), nie auf `main`. `data/events.jsonl` auf `main` ist nur noch Archiv.
- Secrets in GitHub: `SOLANA_RPC_URL` (vollständige Helius-URL `https://mainnet.helius-rpc.com/?api-key=…`, kostenloser Tarif: 1 Mio. Credits/Monat, 10 Anfragen/s), `DEXPAPRIKA_API_KEY`. Ohne gültige https-Adresse schlägt die RPC-Abfrage fehl (kein Zurückfallen auf die öffentliche RPC), Holder-Prüfung dann „unvollständig“. Helius ist in der Cloud-Umgebung nicht freigegeben, Holder-Prüfung deshalb nur im GitHub-Scan.
- RPC-Fehlermeldungen enthalten nie die URL (API-Key), siehe `_safe_request_error` in `desk/solana_rpc.py`.
- Cloud-Umgebung `solana-memecoin-desk` erlaubt api.dexpaprika.com, api.rugcheck.xyz, api.geckoterminal.com, api.mainnet.solana.com.
- pytest ist in der Cloud-Umgebung nicht vorinstalliert: lokal in einer venv außerhalb des Repos installieren.

## Übergabe (Projektentscheidungen)
<!-- Kurz halten, nur aktuelle Entscheidungen. Format: YYYY-MM-DD – Entscheidung -->
- 2026-11-09 – Arbeitsregeln zur Verbrauchsminimierung in CLAUDE.md festgelegt.
- 2026-11-09 – Phase 0 (PR #2) und Phase 1 (PR #3) gemergt; CLAUDE.md (PR #4) gemergt. Tests: 329 bestanden.
- 2026-11-09 – Live-Scan nach Phase 1: 58 Pools, 0 bereit; Liquiditätsvergleich 10/10 bestätigt, Holder-RPC 8/8 erfolgreich, aber Top-10, Entwickler-Anteil und Marktkapitalisierung bei allen geprüften Token unklar.
- 2026-11-09 – Datenquellen-Recherche: Holder-Liste über Helius (kostenlos) gewählt; Birdeye/Solscan/Moralis zu teuer bzw. Black Box; Jupiter `circSupply` undokumentiert, höchstens Gegenprobe. Für die Umlaufmenge gibt es keine nachvollziehbare Quelle (Entscheidung des Nutzers offen, ob eine selbst berechnete, belegte Umlaufmenge erlaubt wird).
- 2026-11-09 – Helius validiert (nur Test-Branch, ca. 204 Credits): `getProgramAccountsV2` und DAS `getTokenAccounts` liefern für Token-2022 (NAOF, PAD, USAR) und klassische SPL-Token (MAGGIE, TAOLF) alle Konten mit Eigentümer; Summe = Mint-Supply exakt; V2 liefert zusätzlich leere Konten. USAR-Widerspruch: Verteilung hat sich durch Kurssturz (−99,999 %, Pool hält danach 99,65 %) geändert; alte Methode liefert auf gleicher Datenbasis korrekte Werte.
- 2026-11-09 – PR #7 gemergt: API-Key nicht mehr in RPC-Fehlermeldungen. PR #9 gemergt: Testdateien von `main` entfernt (PR #8 war versehentlich gemergt), kein stilles Zurückfallen auf die öffentliche RPC.
- 2026-11-09 – PR #1 geschlossen (keine eigenständigen Änderungen). Testergebnisse dauerhaft in `docs/helius_validierung.md`, Umsetzungsplan in `docs/helius_integration_plan.md`. Helius-Integration grundsätzlich freigegeben; Umsetzung erst nach Freigabe des Plans.
- 2026-11-09 – Plan-Entscheidungen: `holders.max_pages: 30` und `holders.max_slot_span: 150` freigegeben; Rückfall auf Top-20-Stichprobe erlaubt, Stichprobe darf aber nur ablehnen oder „unclear“ liefern, nie bestätigen; > 30 Seiten, Slot-Spanne überschritten, fehlende/widersprüchliche Slots → „unclear“; kein Teilergebnis gilt als vollständige Liste. Permanent Delegate (Token-2022) → „unclear“, Adresse + Erweiterung protokollieren, nie bereit; endgültige Ablehnungsregel später gesondert. Summenprüfung ist keine atomare Momentaufnahme (gegenläufige Überweisungen können sich ausgleichen).

## Offene Probleme
- Praktisch wird kein Token „bereit“: Die 20 größten Konten erfassen oft unter 50 % der Menge (Abhilfe: vollständige Holder-Liste über Helius, noch nicht eingebaut), GeckoTerminal meldet die Umlaufmenge nur für etwa 5 % der Token (keine nachvollziehbare Alternative, Nutzer entscheidet).
- Holder-Bewertung gilt nur für ihren Zeitpunkt (Beispiel USAR); vor simulierten Käufen später neu prüfen.
- Wallets derselben Person nicht erkennbar; Bonding-Curve-Token und Pools ohne SOL/USDC/USDT bleiben unvollständig.
- Nur 10 Token pro Lauf; Scan nur manuell.
- Test-Branch `claude/helius-verbindungstest` (enthält die Testskripte) bleibt vorerst (Commit `5214857`), Löschung nach Abschluss der Integration; Ergebnisse stehen in `docs/helius_validierung.md`.
- Offene Fragen: Repo öffentlich oder privat? Was ist im 20-€-Budget schon enthalten?

## Nächster Schritt
Umsetzung nach `docs/helius_integration_plan.md` in zwei getrennten PRs: PR A (Mint-Info, vollständige Liste, Pflichtprüfungen inkl. Slots, Tests; noch nicht eingebunden), dann PR B (Einbindung, Config `holders`, Rückfall, Permanent-Delegate-Prüfung, Stichprobe darf nicht bestätigen). Prüfregeln (25 % / 3 %) und Umlaufmengenregel unverändert. Nutzer merged selbst.

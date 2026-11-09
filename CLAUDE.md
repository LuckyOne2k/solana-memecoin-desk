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
| `desk/rules.py`, `risk.py`, `debate.py` | behalten, noch nicht angeschlossen |

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

## Infrastruktur
- `.github/workflows/tests.yml`: Tests bei jedem PR und auf `main`, nur lesend.
- `.github/workflows/pages.yml`: veröffentlicht nur `app/` auf GitHub Pages (nach Merge auf `main` oder manuell); einmalig in Settings → Pages „GitHub Actions“ wählen.
- `.github/workflows/scan.yml`: nur manuell; erst Tests, dann Scan. Protokoll geht in den Branch `desk-data` (`events.jsonl` + `latest.json`), nie auf `main`. `data/events.jsonl` auf `main` ist nur noch Archiv.
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
- 2026-11-09 – PR A (Helius, Abruf): `fetch_mint_info` und `fetch_full_holder_list` in `desk/solana_rpc.py`, Fehler als `HolderListError` mit festem Grund, nie Teilergebnis; noch nicht in `security_scan.py` eingebunden. Tests: 427 bestanden (90 neu). Antwortform von `getProgramAccountsV2` mit `withContext` nicht belegt; Parser akzeptiert nur `{context.slot, value.accounts, value.paginationKey}`.

- 2026-11-09 – Strukturtest `getProgramAccountsV2` (NAOF 2 Seiten, MAGGIE 1 Seite, ca. 3 Credits): Antwortform belegt (`context.slot` int, `value.count` = Konten der Seite, letzte Seite `paginationKey: null`); Ergebnis in `docs/helius_validierung.md`.
- 2026-11-09 – PR B (Helius, Einbindung): vollständige Liste in `security_scan.py`, Rückfall auf Stichprobe (nie bestätigen), Config `holders`, `value.count`-Gegenprobe, Pflichtprüfungen `permanent_delegate_absent` und `token_extensions_safe` (Positivliste nur Metadaten/Gruppe). Tests: 652 bestanden.
- 2026-11-09 – Scan #45 (Commit `16ffcf4`): 0 Helius-Abfragen, alle 10 geprüften Token schon an der LP-Sperre abgelehnt; Starvation belegt (8 von 10 Token wie im Scan davor, 41 von 60 Token mehrfach geprüft, weiter unten stehende nie). Fairere Prüfreihenfolge (`desk/check_queue.py`) umgesetzt, PR #13 gemergt. Tests: 688 bestanden.
- 2026-11-09 – Scan #46 (Commit `588eace`): Rotation wirkt, Helius 7/7 vollständige Listen, 0 Rückfälle; 0 bereit. GeckoTerminal-Abruf mit HTTP 403 abgelehnt (kein zweiter Preis, keine gemeldete Marktkapitalisierung). TTP: alles bestätigt außer Marktkapitalisierung.
- 2026-11-09 – PR „GeckoTerminal stabilisieren“: begrenzte Wiederholungen (Netzwerk/429/5xx höchstens 3 Versuche, 403 höchstens 2, sonst keine), Statuscode/Versuche/Grund in `market_cap_diagnostics`, keine alten Preise als Ersatz, Pflicht zu zwei Preisen unverändert. Tests: 711 bestanden. Eigene Marktkapitalisierungs-Spanne (PR 2) noch nicht freigegeben, Vorschlag wird überarbeitet.

- 2026-11-09 – PR #14 gemergt; Scan #47: GeckoTerminal wieder 2/2 HTTP 200, 0 Wiederholungen; TTP, BRUH, USOE: alles bestätigt außer Marktkapitalisierung. Helius-Verbrauch laut Dashboard bis dahin 225 Credits.
- 2026-11-09 – PR 2 „On-Chain-Spanne der Marktkapitalisierung“ freigegeben und umgesetzt (Schalter standardmäßig aus): Definition L = frei übertragbare Bestände in bestätigten normalen Wallets (nur interne Untergrenze); „null“-Authorities zählen nicht; max. 10 getMultipleAccounts à 100 je Token, größte zuerst; Top-10 ausgeschlossen; jede Lücke → unvollständig; kein Credit-Messtest (Verbrauch im Dashboard prüfen). Zusätzlich (konservativ): eingefrorene Token-Konten per 1 getProgramAccountsV2-Aufruf abgezogen. Tests: 920 bestanden.
- 2026-11-09 – PR #15 überarbeitet (Nutzer-Review): `max_account_calls_per_token` hart auf 1–10 begrenzt (> 10 → ConfigError). Untergrenze heißt jetzt „heuristische Wallet-Untergrenze“ (technisch wallet-ähnliche Konten); kein Nachweis von privatem Schlüssel, freier Übertragbarkeit oder Umlaufmenge; Protokoll mit `lower_bound_kind`, `*_proven: false`, `residual_risk`. Restrisiko dokumentiert: On-Curve-Adressen ohne bekannten Schlüssel. `is_on_curve` gegen Solana-SDK-Referenz (solders 0.29.0, curve25519-dalek) geprüft: 0 Abweichungen bei 200.000 Zufallswerten, 3.500 PDA-Ableitungen und allen Kantenfällen; 254 feste Referenzvektoren + RFC-8032-Vergleich in den Tests. Tests: 1208 bestanden.
- 2026-11-09 – PR #15 gemergt. PR #16: On-Chain-Spanne mit Nutzerfreigabe aktiviert (`allow_onchain_bounds: true`), Sicherheitslogik unverändert, Grenzen 10 Aufrufe / 100 je Aufruf / 150 Slots unverändert; Schalter-aus-Tests nutzen eigene Config-Kopie; neuer Test: echte Config + gescheiterte Abfrage → immer unvollständig. Untergrenze bleibt technische Heuristik, keine Umlaufmenge. Tests: 1210 bestanden.
- 2026-11-09 – PR #16 gemergt; Scan #50 (Commit `08a6d3f`): erstmals 2 Token bereit (TTP: Untergrenze 1,81 Mio./Obergrenze 3,79 Mio. $, 1 Aufruf; BRUH: 0,353 Mio./2,16 Mio. $, 2 Aufrufe, knapp über 300k). USOE unvollständig (Obergrenze 39,9 Mio. $ > 20 Mio., Limit 10 Aufrufe erreicht). On-Chain-Spanne 3 Versuche, 0 Fehler, 13 getMultipleAccounts. Helius-Dashboard danach 344 Credits (vorher ca. 245, Anstieg enthält auch Holder-Listen). „Bereit“ ist keine Kaufempfehlung.
- 2026-11-09 – Scan #51 (Commit `8b16765`): 41 Pools, 10 abgelehnt, 29 unvollständig, 2 bereit (TTP, BRUH wie in Scan #50). RugCheck 10/10, Helius 8/8, GeckoTerminal 2/2 HTTP 200. Liquiditätsvergleich 4 bestätigt, 6 unklar: 5 PumpSwap-Pools mit 96–97 % Abweichung (DexPaprika ca. 46–88k $ ≈ FDV, RugCheck ca. 1,6–2,3k $), GLD (Pump.fun) ohne RugCheck-Wert; alle 6 ohnehin aus anderen Gründen abgelehnt. Vermutung (nicht belegt): DexPaprika bewertet PumpSwap-Liquidität zu hoch. Keine Codeänderung.
- 2026-11-09 – Beobachtung Scans #52–#54 (ohne Codeänderung): TTP und BRUH in 4 Scans in Folge bereit. PumpSwap-Abweichung 96–98 % bei 8/2/1 Pools (RugCheck stets ca. 1,6–2,4k $), nie bei Orca/Meteora/Raydium; Hauptpools TTP/BRUH/USOE stimmen. Kein Handlungsbedarf, Ursache offen.
- 2026-11-09 – Android-Weg festgelegt: PWA auf GitHub Pages (Repo öffentlich), liest Dateien aus `desk-data`; Reihenfolge: 1. `latest.json`, 2. PWA nur lesend, 3. Paper-Broker (Einstiegsregel offen), 4. geplante Scans, 5. Telegram optional.
- 2026-11-09 – PR 1 `latest.json`: `desk/latest.py` + Workflow-Schritt; Scan fehlgeschlagen → `failed`, leere Listen, keine alten Ergebnisse; nicht geprüfte Pools nur als Anzahl; Datei wird entfernt, wenn sie nicht erzeugt werden konnte. Scannerlogik unverändert. Tests: 1238 bestanden (28 neu).
- 2026-11-09 – PR #19 gemergt; Scan #55 (Commit `44ff414`): `latest.json` im selben `desk-data`-Commit wie `events.jsonl`, `ok`, 42 Pools (7 abgelehnt, 33 unvollständig, 2 bereit), TTP und BRUH zum fünften Mal in Folge bereit, Werte 1:1 wie im Protokoll. Fall `failed` live noch nicht aufgetreten (nur Tests).
- 2026-11-09 – PR 2 Web-App (`app/`, nur lesend): einzige Quelle `latest.json`; nicht `ok`, HTTP-Fehler oder unplausible Datei → Meldung, keine Token. Offline darf der letzte erfolgreich geladene Stand erscheinen, nur mit „Offline – Daten möglicherweise veraltet“; Scan-Alter immer sichtbar, ab 24 h gelbe Warnung. Service Worker speichert nur die App, nie Scan-Daten. Daten können bis 5 Min. verzögert sein (GitHub-Cache). Tests: 1247 bestanden (9 neu).

## Offene Probleme
- GeckoTerminal meldet die Umlaufmenge nur für etwa 5 % der Token; seit PR #16 kann die On-Chain-Spanne (Heuristik) die Marktkapitalisierung bestätigen, Werte nahe 300k $ können zwischen Scans kippen.
- Holder-Bewertung gilt nur für ihren Zeitpunkt (Beispiel USAR); vor simulierten Käufen später neu prüfen.
- PumpSwap: DexPaprika-Liquidität weicht stark von RugCheck ab (Scan #51, 5 Pools); Prüfung wirkt korrekt (→ unklar), Ursache offen.
- Wallets derselben Person nicht erkennbar; Bonding-Curve-Token und Pools ohne SOL/USDC/USDT bleiben unvollständig.
- Nur 10 Token pro Lauf; Scan nur manuell. Prüfreihenfolge rotiert (zuletzt abgelehnte nach hinten); ein beschädigtes Protokoll schaltet die Rotation ab (Rückfall, sichtbar in `check_order_source`).
- Test-Branch `claude/helius-verbindungstest` (enthält die Testskripte) bleibt vorerst (Commit `5214857`), Löschung nach Abschluss der Integration; Ergebnisse stehen in `docs/helius_validierung.md`.
- Offene Frage: Was ist im 20-€-Budget schon enthalten? (Repo ist öffentlich.)

## Nächster Schritt
Nach Merge von PR 2: einmalig GitHub Pages auf „GitHub Actions“ stellen, App auf dem Handy prüfen. Danach (vorgemerkt, Auftrag des Nutzers): unabhängige Bewertung 1. Architektur/Verschlankung (Struktur, Doppelungen, was getrennt bleiben muss, Token- und Wartungsaufwand) und 2. Nutzen eigener Claude-Code-Agenten (Aufgaben, Verbrauch vs. Nutzen) – nur Bewertung am tatsächlichen Repo-Stand, keine Änderungen ohne Freigabe. Danach Paper-Broker (Einstiegsregel offen). Später: Löschung des Test-Branches, Ablehnungsregeln für Permanent Delegate/riskante Erweiterungen. Prüfregeln (25 % / 3 %, 300k–20 Mio. $) unverändert.

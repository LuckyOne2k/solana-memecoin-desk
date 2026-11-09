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
- Secrets in GitHub: `SOLANA_RPC_URL`, `DEXPAPRIKA_API_KEY`. Die öffentliche Solana-RPC liefert bei `getTokenLargestAccounts` meist 429, Holder-Prüfung deshalb nur im GitHub-Scan.
- Cloud-Umgebung `solana-memecoin-desk` erlaubt api.dexpaprika.com, api.rugcheck.xyz, api.geckoterminal.com, api.mainnet.solana.com.
- pytest ist in der Cloud-Umgebung nicht vorinstalliert: lokal in einer venv außerhalb des Repos installieren.

## Übergabe (Projektentscheidungen)
<!-- Kurz halten, nur aktuelle Entscheidungen. Format: YYYY-MM-DD – Entscheidung -->
- 2026-11-09 – Arbeitsregeln zur Verbrauchsminimierung in CLAUDE.md festgelegt.
- 2026-11-09 – Phase 0 (PR #2) und Phase 1 (PR #3) gemergt; CLAUDE.md (PR #4) gemergt. Tests: 329 bestanden.
- 2026-11-09 – Live-Scan nach Phase 1: 58 Pools, 0 bereit; Liquiditätsvergleich 10/10 bestätigt, Holder-RPC 8/8 erfolgreich, aber Top-10, Entwickler-Anteil und Marktkapitalisierung bei allen geprüften Token unklar.

## Offene Probleme
- Praktisch wird kein Token „bereit“: Die 20 größten Konten erfassen oft unter 50 % der Menge, GeckoTerminal meldet die Umlaufmenge nur für etwa 5 % der Token. Abhilfe bräuchte eine vollständige Holder-Liste bzw. andere Datenquelle (Kosten klären, Nutzer entscheidet).
- Wallets derselben Person nicht erkennbar; Bonding-Curve-Token und Pools ohne SOL/USDC/USDT bleiben unvollständig.
- Nur 10 Token pro Lauf; Scan nur manuell.
- Offener PR #1 („Add files via upload“, Branch `desk/__init__.py`) vermutlich versehentlich: Nutzer entscheidet.
- Offene Fragen: Repo öffentlich oder privat? Was ist im 20-€-Budget schon enthalten?

## Nächster Schritt
Nutzer entscheidet zwischen
- (a) Datenquelle für eine vollständige Holder-Liste und die Umlaufmenge prüfen (Kosten zuerst klären), oder
- (b) Phase 2 starten: Paper-Broker (SQLite, simulierte Käufe/Verkäufe mit Gebühren und Slippage, Stops über `risk.py`, Kennzahlen).

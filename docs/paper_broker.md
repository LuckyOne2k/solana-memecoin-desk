# Paper-Broker

Stand 2026-11-10. Nur Simulation: keine Wallet, keine Signatur, keine
Transaktion, kein echtes Geld. Code: `desk/paper.py`, Werte:
`config.yaml → paper_trading`.

## Wer hat entschieden?

Der Nutzer hat am 2026-11-10 die Fertigstellung des Paper-Traders samt
Strategie-Entscheidungen, Workflow-Starts und Merges an Claude delegiert.
Die Sicherheitsregeln aus `CLAUDE.md` bleiben unverändert und gelten
weiter. Insbesondere ist nur `mode: paper` erlaubt, kein Live-Trading,
keine kostenpflichtigen Dienste, und bei fehlenden Daten wird nie
eingestiegen.

## Ablauf

| Workflow | Takt | Was passiert |
|---|---|---|
| `clock.yml` (Taktgeber) | läuft dauerhaft in ~5,5-h-Abschnitten | startet die beiden folgenden Workflows zur richtigen Minute |
| `scan.yml` | stündlich (Minute 7, über Taktgeber) und manuell | Tests → Scan → `latest.json` → `python -m desk.paper after-scan` |
| `paper-monitor.yml` | alle 5 Min. außer Minute 07 (über Taktgeber) und manuell | `python -m desk.paper monitor` (nur Kurse + Ausstiege; ohne offene Position keine Abfrage und kein Commit) |

Beide Workflows schreiben nur in den Branch `desk-data`. Sie teilen sich
die Concurrency-Gruppe `desk-data-write`, damit nie zwei gleichzeitig
schreiben. Dateien:

- `paper_state.json`: Depot mit Bargeld, offenen Positionen, den letzten
  100 Verkäufen, Statistik, Depotwert-Verlauf und letztem Lauf. Wird von
  der App gelesen.
- `paper_trades.jsonl`: vollständiges Protokoll. Jede Zeile ist ein
  Kauf, Verkauf oder ein begründet übersprungener Einstieg.

Die GitHub-Zeitplanung (`schedule`) hat in diesem Repository am
2026-11-10 keinen einzigen Lauf gestartet. Deshalb gibt der Taktgeber
`clock.yml` den Takt: Er läuft etwa 5,5 Stunden, löst die Workflows per
`workflow_dispatch` aus und startet sich danach selbst neu. Als
Sicherheitsnetz hat er einen eigenen Zeitplan alle 6 Stunden.

## Einstiegsregel

Ein Einstieg findet nur statt, wenn **alle** Punkte erfüllt sind:

1. Der Scan im selben Workflow-Lauf war erfolgreich. `latest.build_latest`
   meldet „ok“, und der Scan ist höchstens `max_scan_age_minutes` (30)
   alt.
2. Der Pool ist `ready_for_full_risk_screen`, ohne offene Prüfungen und
   ohne Ablehnungsgründe.
3. **Bestätigung in zwei Scans** (Variante B aus
   `docs/vorschlag_offene_entscheidungen.md`):
   - Derselbe Pool, also dieselbe Mint- und Pool-Adresse, war im direkt
     vorigen Scan ebenfalls bereit.
   - Der Abstand zwischen beiden Scans liegt zwischen 30 Minuten und
     6 Stunden.
   - Der Scan-Kurs hat sich dazwischen um höchstens 15 % verändert.
   - Die Liquidität ist dazwischen um höchstens 20 % gefallen.
4. Ein frischer Kurs aus **zwei Quellen** für denselben Pool (GeckoTerminal
   und DexPaprika) weicht höchstens um `data_quality.max_price_deviation_pct`
   (10 %) voneinander ab. Kommt nur eine Quelle oder widersprechen sich
   die Quellen, wird nicht gekauft.
5. Der frische Kurs liegt höchstens 10 % vom Scan-Kurs entfernt, in beide
   Richtungen.
6. Es gibt noch keine Position im Token. Der letzte Verkauf liegt mehr
   als 24 h zurück. Es sind weniger als 5 Positionen offen.
7. **Positionsgröße** ist der kleinste dieser drei Werte:
   - 3 % des Depotwerts (bis 2026-11-14: 8 %),
   - 1 % der kleineren bestätigten Pool-Liquidität,
   - verfügbares Bargeld.

   Unter 50 $ wird nicht gekauft.
8. **Kaufkurs** = höherer frischer Kurs × (1 + Kosten).
   - Kosten = 1 % Gebühr + geschätzter Preiseinfluss.
   - Preiseinfluss = 2 × Größe / Liquidität. Das ist eine Näherung für
     Konstantprodukt-Pools bei kleinen Orders.

Identifikation nur über Adressen, nie über Symbole (vgl. die zwei
„BRUH“-Token).

## Ausstiegsregeln

Geprüft wird in jedem Lauf, also etwa alle 5 Minuten. Bewertet wird
immer mit dem **niedrigeren** verfügbaren Kurs (konservativ).

| Reihenfolge | Grund | Regel |
|---|---|---|
| 1 | `price_unavailable_writeoff` | Kein Kurs aus beiden Quellen länger als 24 h → Abschreibung auf 0 $ |
| 2 | `stop_loss` | Kurs ≤ Einstieg − 6 % |
| 2 | `profit_lock` | Gewinnsicherung in Stufen (siehe unten) |
| 2 | `trailing_stop` | ab +12 % Höchstkurs: Kurs ≤ Höchstkurs − 6 % |
| 3 | `security_rejected` | Token im neuesten Scan `rejected` (nur „unvollständig“ → halten) |
| 4 | `max_holding_time` | Haltedauer ≥ 72 h |
| 5 | `partial_take_profit` | einmalig: Kurs ≥ Einstieg + 30 % → Hälfte verkaufen (nur wenn kein Grund 1–4 greift) |

Es gilt immer der **höchste** der drei Stops; er sinkt nie.

**Gewinnsicherung in Stufen** (`profit_lock_step_pct: 6`, Nutzerwunsch
2026-11-10: ein Token im Plus soll nicht erst im Minus verkauft werden):

| Höchstkurs seit Kauf | Stop mindestens |
|---|---|
| unter +6 % | Einstieg − 6 % |
| ab +6 % | Einstand nach geschätzten Verkaufskosten |
| ab +12 % | Einstieg + 6 % |
| ab +18 % | Einstieg + 12 % |
| … je weitere 6 % | eine Stufe höher |

Verkaufskosten werden mit den Kaufkosten der Position geschätzt (gleiche
Größe), ohne gespeicherten Wert konservativ mit 3 %. Liegt die Stufe
nicht unter dem Höchstkurs (sehr hohe Kosten), entfällt sie. Die App
zeigt beim Stop „(Gewinnsicherung)“ bzw. „(Trailing)“ an.

**Teilverkauf** (2026-11-14, `partial_take_profit_pct: 30`,
`partial_take_profit_share_pct: 50`): Liegt der niedrigere Kurs mindestens
30 % über dem Einstieg, wird einmal die Hälfte der Menge verkauft. Der
Rest behält Einstieg, Höchstkurs und Stops. Der Teilverkauf steht in
`closed_trades` mit `partial: true` und zählt nicht als abgeschlossener
Trade (Gewinn/Verlust-Zählung beim Schluss des Rests); der Gewinn geht
sofort in `realized_pnl_usd`, Anzahl in `stats.partial_sales`.

**Warum 3 % und Teilverkauf:** In drei Tagen fielen USOE, ConConAI und XTO
jeweils binnen 5 Minuten um 99,6–99,99 % (zusammen −2.372 $ bei 8 %
Positionsgröße). Gegen solche Sprünge hilft kein Stop. Kleinere
Positionen begrenzen den Schaden je Fall auf ~300 $; der Teilverkauf
sichert bei stark gestiegenen Token (XTO +188 %, ConConAI +68 %) einen
Teil des Gewinns, bevor ein Absturz alles nimmt.

Verkaufskurs = beobachteter Kurs × (1 − Kosten), nie der Stop-Kurs. Fällt
der Kurs zwischen zwei Abfragen weit unter den Stop, trägt das Depot
den vollen Verlust. Solange der Kurs nur kurz fehlt, wird nichts
entschieden.

## Begründung der Werte

- **Variante B** war bereits die Empfehlung im Vorschlagsdokument. Sie
  filtert Zufallstreffer und Grenzwerte, die zwischen Scans kippen
  (Beispiel BRUH knapp über 300k $).
- **Zwei Kursquellen** folgen dem bestehenden Prinzip: Eine Quelle allein
  bestätigt nie.
- **1 % der Liquidität** hält den simulierten Preiseinfluss klein, damit
  die Simulation realistisch bleibt.
- **Gewinnsicherung** (seit 2026-11-10): Einstand wird nur garantiert,
  wenn der Kurs nicht zwischen zwei Abfragen (5 Min.) unter die Stufe
  springt. Ob die Stufen unterm Strich helfen oder zu früh verkaufen,
  zeigt die Auswertung nach 20–30 Trades.
- **Stop 6 % / Trailing 12 % / 6 %** sind die bisherigen Werte aus
  `config.yaml` und bleiben vorerst unverändert. Vermutung: Bei
  Memecoins und Kosten von etwa 2–3 % je Runde ist das sehr eng. Das wird
  nach den ersten 20–30 abgeschlossenen Paper-Trades ausgewertet und
  erst dann angepasst, damit keine Werte ohne Daten überangepasst werden.
- **72 h Haltedauer** begrenzt die Haltezeit; ursprünglich passend zum alten Scanner-Fenster 30–72 h (seit 2026-11-10: 24 h–7 Tage).
  Ohne Grenze würden Positionen beliebig lange liegen.
- Die **Debatte** (`desk/debate.py`) bleibt unangeschlossen, weil KI-Agenten
  kostenpflichtig wären. Sie dürfte ohnehin nur bremsen.

## Mehr Kandidaten (2026-11-10)

In 15 Scans wurden nur 150 von 606 Pool-Einträgen geprüft (Limit 10 je
Lauf), nur 2 Token waren je bereit. Deshalb, ohne eine Sicherheitsprüfung
zu lockern:

- Altersfenster 24 h–7 Tage statt 30–72 h: Token, die die ersten Tage
  überlebt haben, sind seltener Betrug und haben meist mehr Liquidität.
- 30 statt 10 Prüfungen je Lauf. Helius geschätzt etwa 30 % des
  Freikontingents. RugCheck läuft weiterhin mit 1,1 s Abstand.
- DexPaprika: begrenzte Wiederholungen bei 429/5xx/Netzwerk (höchstens
  3 Versuche, 5 s/10 s Pause), weil mehr Seiten abgerufen werden.

## Schattendepot (nur Lernen)

`desk/shadow.py` läuft nach jedem Scan. Für jeden geprüften Pool, ob
bereit, unvollständig oder abgelehnt, wird ein Startkurs gespeichert
(GeckoTerminal, gleicher Pool, höchstens eine neue Beobachtung je Token
und 24 h). Danach wird die Kursveränderung nach 1 h, 6 h und 24 h
gemessen. `shadow_summary.json` fasst das je Status und je erstem Grund
zusammen. Die App zeigt es an.

Ziel ist, nach 1–2 Wochen zu sehen, welche Regeln Gewinne verhindern und
welche Verluste vermeiden. Grundlage für spätere Anpassungen der
Einstiegs- und Stop-Regeln. Es gibt keine Entscheidung und keine
Ersatzwerte. Es sind Kursveränderungen ohne Kosten, keine
Handelsergebnisse.

## Bekannte Grenzen

- Kurse werden nur etwa alle 5 Minuten abgefragt (GitHub-Auslösung kann sich um bis zu etwa 1 Minute verzögern). Bewegungen dazwischen
  sieht der Broker nicht.
- Der Preiseinfluss ist eine Näherung. Echte Ausführung, MEV und
  Priority-Fees können schlechter sein.
- GeckoTerminal begrenzt Abfragen (HTTP 429). Es gibt höchstens drei
  Versuche. Bleibt nur eine Quelle übrig, wird nicht gekauft; die Position
  wird mit dieser einen Quelle bewertet.
- Wird `starting_balance_usd` geändert, gilt das nur für ein neues Depot.
  Zurücksetzen: `paper_state.json` im Branch `desk-data` löschen.
- `events.jsonl` wächst bei stündlichen Scans um etwa 1 MB pro Tag.
  Eine Rotation ist ein späterer Schritt.

## Lokal ausprobieren

```bash
python -m desk.paper monitor --data-dir /tmp/paper-test
python -m desk.paper after-scan --data-dir desk-data --scan-outcome success
```

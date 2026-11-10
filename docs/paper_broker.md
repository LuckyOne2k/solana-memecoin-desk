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
| `scan.yml` | stündlich (Minute 7) und manuell | Tests → Scan → `latest.json` → `python -m desk.paper after-scan` |
| `paper-monitor.yml` | Minute 22, 37, 52 und manuell | `python -m desk.paper monitor` (nur Kurse + Ausstiege) |

Beide Workflows schreiben nur in den Branch `desk-data`. Sie teilen sich
die Concurrency-Gruppe `desk-data-write`, damit nie zwei gleichzeitig
schreiben. Dateien:

- `paper_state.json`: Depot mit Bargeld, offenen Positionen, den letzten
  100 Verkäufen, Statistik, Depotwert-Verlauf und letztem Lauf. Wird von
  der App gelesen.
- `paper_trades.jsonl`: vollständiges Protokoll. Jede Zeile ist ein
  Kauf, Verkauf oder ein begründet übersprungener Einstieg.

GitHub kann geplante Läufe verzögern (oft 5–20 Minuten) und deaktiviert
sie in öffentlichen Repos nach 60 Tagen ohne Aktivität auf dem
Standard-Branch. Dann in Actions → Workflow → „Enable workflow“
klicken.

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
   - 8 % des Depotwerts,
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

Geprüft wird in jedem Lauf, also etwa alle 15 Minuten. Bewertet wird
immer mit dem **niedrigeren** verfügbaren Kurs (konservativ).

| Reihenfolge | Grund | Regel |
|---|---|---|
| 1 | `price_unavailable_writeoff` | Kein Kurs aus beiden Quellen länger als 24 h → Abschreibung auf 0 $ |
| 2 | `stop_loss` | Kurs ≤ Einstieg − 6 % |
| 2 | `trailing_stop` | ab +12 % Höchstkurs: Kurs ≤ Höchstkurs − 6 % |
| 3 | `security_rejected` | Token im neuesten Scan `rejected` (nur „unvollständig“ → halten) |
| 4 | `max_holding_time` | Haltedauer ≥ 72 h |

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
- **Stop 6 % / Trailing 12 % / 6 %** sind die bisherigen Werte aus
  `config.yaml` und bleiben vorerst unverändert. Vermutung: Bei
  Memecoins und Kosten von etwa 2–3 % je Runde ist das sehr eng. Das wird
  nach den ersten 20–30 abgeschlossenen Paper-Trades ausgewertet und
  erst dann angepasst, damit keine Werte ohne Daten überangepasst werden.
- **72 h Haltedauer** passt zum Alters-Fenster des Scanners (30–72 h).
  Ohne Grenze würden Positionen beliebig lange liegen.
- Die **Debatte** (`desk/debate.py`) bleibt unangeschlossen, weil KI-Agenten
  kostenpflichtig wären. Sie dürfte ohnehin nur bremsen.

## Bekannte Grenzen

- Kurse werden nur etwa alle 15 Minuten abgefragt. Bewegungen dazwischen
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

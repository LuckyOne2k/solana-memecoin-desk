---
name: scan-auswertung
description: Einheitliche, knappe Auswertung eines Scans des Solana Memecoin Desk (Protokoll im Branch desk-data). Verwenden, wenn der Nutzer einen Scan, Scan-Lauf oder das Scan-Protokoll ausgewertet haben möchte.
---

# Scan-Auswertung

## Datenquelle
- Protokoll: `git fetch origin desk-data` und dann `git show origin/desk-data:events.jsonl`. Maßgeblich sind die Ereignisse `security_scan_completed`, normalerweise das letzte.
- Lauf und Commit: GitHub-Actions-Lauf des Workflows `scan.yml` mit passender Zeit (Nummer, `head_sha`, Ergebnis). Das Protokoll selbst enthält keinen Commit; fehlt die Zuordnung, ausdrücklich sagen.
- Nur das Protokoll und die Lauf-Infos lesen, keine Repository-Dateien.

Auszählung (Ausgabe kompakt halten):
```bash
git show origin/desk-data:events.jsonl | python3 -c "
import json,sys,collections as c
ev=[json.loads(l) for l in sys.stdin if '\"security_scan_completed\"' in l]
for e in ev[-2:]:
  d=e['details'];p=d.get('checked_pools',[])
  print(e['timestamp'],{k:d.get(k) for k in('total_pools','rejected_pools','incomplete_pools','ready_for_full_risk_screen','security_checks_succeeded','security_checks_failed','holder_checks_succeeded','holder_checks_failed','pagination_complete')})
  print(' rej',c.Counter(r for x in p for r in x.get('rejection_reasons',[])).most_common())
  print(' miss',c.Counter(m for x in p if x.get('status')=='incomplete' for m in x.get('missing_checks',[])).most_common())
  print(' liq',c.Counter((x.get('liquidity_check') or {}).get('status') for x in p))
  print(' top10',c.Counter((x.get('holders') or {}).get('top10_status') for x in p),'mc',c.Counter(((x.get('market_cap') or {}).get('status'),(x.get('market_cap') or {}).get('reason')) for x in p))
  print(' err',[(x.get('token_symbol'),x.get('security_error') or x.get('holder_error')) for x in p if x.get('security_error') or x.get('holder_error')])
  print(' mcdiag',d.get('market_cap_diagnostics'))"
```

## Schema der Antwort
1. **Scan:** Zeitpunkt (UTC), Workflow-Lauf, Commit.
2. **Pools gesamt:** `total_pools`.
3. **Status:** `rejected` / `incomplete` / `ready_for_full_risk_screen`.
4. **Ablehnungsgründe** nach Häufigkeit.
5. **Gründe für „incomplete“** (`missing_checks`) nach Häufigkeit.
6. **Datenquellen:** Fehler von RugCheck und RPC (`*_failed`, `security_error`, `holder_error`), Liquiditätsvergleich, Preis- und Marktkapitalisierungs-Gründe, `market_cap_diagnostics`, `pagination_complete`.
7. **Vergleich** mit dem vorherigen Scan (nur bei gleichem Protokollformat; sonst sagen, dass kein Vergleich möglich ist).
8. **Drei wichtigste Erkenntnisse.**
9. **Eine konkrete nächste Handlung.**

Höchstens etwa 15 Zeilen; Tabellen nur, wo sie kürzer sind.

## Verbindliche Regeln
- Nur tatsächliche Werte aus dem Protokoll und nur vorhandene Felder verwenden; nie schätzen. Fehlt etwas, ausdrücklich darauf hinweisen.
- Ein erfolgreicher Workflow heißt nicht, dass Token handelbar sind.
- „incomplete“ ist nie bestanden.
- Keine Sicherheitsgrenzen oder Prüfregeln ändern; Auffälligkeiten nur melden.
- Keine kostenpflichtigen Datenquellen ohne Zustimmung des Nutzers.
- Keine Trading- oder Kaufempfehlungen, keine Kaufentscheidungen.

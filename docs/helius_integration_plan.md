# Umsetzungsplan: vollständige Holder-Liste über Helius

Stand: Plan, noch nicht umgesetzt. Grundlage: `docs/helius_validierung.md`.

## Unverändert
- Prüfregeln Top-10 (25 %) und Entwickler-Anteil (3 %), Sicherheitsprüfungen,
  Umlaufmengenregel, Grenzwerte in `config.yaml`, `mode: paper`.
- Pool-/Burn-Zuordnung und Bewertung in `desk/holders.py` (Logik bleibt;
  mit vollständiger Liste ist die nicht erfasste Menge 0, Unter- und
  Obergrenze fallen zusammen).
- Keine neuen Abhängigkeiten, keine kostenpflichtigen Dienste
  (Helius Free: 1 Mio. Credits/Monat).

## 1. Datenabruf (neu, `desk/solana_rpc.py`)
Neue Funktion `fetch_full_holder_list(mint, creator_address=None)`:

1. **Programm ermitteln:** `getAccountInfo(mint, base64, finalized)`.
   Owner muss SPL Token oder Token-2022 sein, sonst Fehler.
2. **Supply vorher:** `getTokenSupply(mint, finalized)` → Menge + Slot S1.
3. **Liste:** `getProgramAccountsV2(programm, …)`, `commitment finalized`,
   `withContext true`, `minContextSlot S1`, `limit 1000`,
   `encoding base64`, `dataSlice {offset 32, length 40}`.
   - Filter klassisch: `memcmp {offset 0, bytes mint}` + `dataSize 165`.
   - Filter Token-2022: nur `memcmp` (Konten mit Erweiterungen sind
     größer als 165 Byte).
   - Seiten über `paginationKey`, bis keiner mehr kommt.
   - Obergrenze `holders.max_pages` (Vorschlag 30 = 30.000 Konten);
     wird sie erreicht → Fehler „zu viele Konten“, kein Teilergebnis.
4. **Supply nachher:** zweite `getTokenSupply` → S2. Weicht die Menge ab
   → Fehler (Mint/Burn während der Abfrage).
5. **Dekodieren:** je Konto 40 Byte: Owner (32 Byte → base58, eigener
   kleiner Encoder ohne neue Abhängigkeit), Menge (u64 LE). Falsche
   Länge oder fehlende Daten → Fehler.
6. **Leere Konten verwerfen** (Menge 0).

Rückgabe im bisherigen Snapshot-Format (`accounts`, `supply_amount_raw`,
`supply_decimals`, `sample_owner_mapping_complete`, `mint`), dazu
`data_scope: "full_token_account_list"`, `token_program`, `pages`,
`accounts_total`, `accounts_nonzero`, `slot_supply_before`,
`slot_list_min`, `slot_list_max`, `slot_supply_after`.

## 2. Pflichtprüfungen (sonst „unclear“)
Jede Verletzung → `SolanaRpcError` mit festem Grund, kein Teilergebnis:
- alle Seiten geladen (letzte Seite ohne `paginationKey`), Obergrenze
  nicht erreicht;
- jedes Konto mit Bestand hat Owner (32 Byte) und gültige Menge;
- jedes Konto gehört zum abgefragten Mint (durch Filter garantiert; der
  Filter wird in Tests geprüft);
- **Summe aller Bestände = Mint-Supply exakt**, und Supply vorher =
  Supply nachher;
- Slots protokolliert; Spanne über Grenzwert `holders.max_slot_span`
  (Vorschlag 150 ≈ 1 min) → Fehler.

Warum die Summenprüfung Änderungen während der Pagination erkennt:
Wandern Token zwischen einer schon gelesenen und einer noch nicht
gelesenen Seite, ist die Summe zu hoch oder zu niedrig. Restrisiko: zwei
gegenläufige Überweisungen gleicher Höhe heben sich auf. Abmilderung:
Slot-Spanne begrenzen und protokollieren.

## 3. Token-2022-Sonderfälle
| Fall | Wirkung | Behandlung |
|---|---|---|
| Konten > 165 Byte (Erweiterungen) | `dataSize` würde sie ausschließen | kein `dataSize` bei Token-2022 |
| Transfer-Fee (einbehaltene Gebühren) | liegen nicht in `amount` → Summe < Supply | Summenprüfung schlägt fehl → unclear (Grund `supply_sum_mismatch`) |
| Confidential Transfer | verschlüsselte Bestände → Summe < Supply | unclear |
| Permanent Delegate | Token jederzeit verschiebbar | wird erkannt und protokolliert (`token2022_extensions`); Bewertung unverändert, da Regeländerung nur mit Zustimmung |
| Interest-bearing / Scaled UI Amount | Anzeige skaliert, Rohmengen nicht | nur Rohmengen verwenden |
| Non-transferable | keine Wirkung auf Summe | keine |
| Mint-Konto selbst | Layout beginnt mit COption-Tag, kann den Mint-Filter praktisch nicht treffen | Dekodierprüfung fängt Fehllängen ab |

Erweiterungen werden einmal aus `getAccountInfo(mint, jsonParsed)`
gelesen (gleicher Aufruf wie Programm-Ermittlung).

## 4. Einbindung (`desk/security_scan.py`)
- `_fetch_holders` ruft zuerst `fetch_full_holder_list`.
- Schlägt sie fehl, Rückfall auf die bisherige Top-20-Stichprobe
  (`fetch_holder_snapshot`); Fehlergrund wird als
  `full_holder_list_error` protokolliert. Die Stichprobe kann wie bisher
  nur ablehnen oder unclear liefern, also kein Risiko.
- `desk/holders.py`: Bewertungslogik unverändert. Bei vollständiger
  Liste ist die nicht erfasste Menge 0, damit gilt Obergrenze =
  Untergrenze. Nur `data_scope` wird aus dem Snapshot übernommen statt
  fest „sample“.
- Marktkapitalisierung: nutzt weiter `supply_amount_raw`; keine Änderung.

## 5. Konfiguration (`config.yaml`, Zustimmung nötig)
Neuer Abschnitt, keine Lockerung bestehender Werte:
```yaml
holders:
  source: helius_full_list   # Rückfall: Top-20-Stichprobe
  max_pages: 30
  max_slot_span: 150
```
`desk/config.py` prüft Typ und Bereich (positive Ganzzahlen).

## 6. Tests (alle ohne Netzwerk, gemockt)
- Pagination: 1, 3 und 30 Seiten; 31. Seite → Fehler.
- Summe exakt / zu hoch / zu niedrig; Supply vorher ≠ nachher.
- Leere Konten gefiltert; mehrere Konten je Owner aggregiert.
- Dekodierung: bekannter Owner/Menge, falsche Länge, fehlende Daten.
- Klassisch mit `dataSize 165`, Token-2022 ohne; fremdes Programm → Fehler.
- Slot-Spanne über Grenze → Fehler.
- Rückfall auf Stichprobe bei Fehler; Fehler ohne URL/Key in Meldungen.
- `holders.py`: vollständige Liste → bestätigt bei ≤ 25 % / ≤ 3 %,
  abgelehnt darüber; Pool/Burn weiter herausgerechnet.
- Alle bestehenden Tests unverändert grün.

## 7. Verbrauch
Je Token: 1 `getAccountInfo` + 2 `getTokenSupply` + Seiten (meist 1–5)
≈ 4–8 Credits. 10 Token je Lauf ≈ 80 Credits; selbst stündlich
≈ 58.000 Credits/Monat (≈ 6 % des Freikontingents).

## 8. Ablauf
1. PR A: Abruf + Pflichtprüfungen + Tests (ohne Einbindung).
2. PR B: Einbindung, Config-Abschnitt, Rückfall, Tests, README/CLAUDE.md.
3. Ein manueller Scan über GitHub Actions; Auswertung mit
   `scan-auswertung`, Credits im Helius-Dashboard prüfen.
4. Danach entscheidet der Nutzer über die Löschung des Test-Branches.

## Offene Entscheidungen des Nutzers
- Neue Config-Werte (`max_pages` 30, `max_slot_span` 150) freigeben?
- Rückfall auf Top-20-Stichprobe behalten (Vorschlag: ja)?
- Permanent Delegate nur protokollieren (Vorschlag) oder später als
  eigene Ablehnungsregel (Regeländerung)?

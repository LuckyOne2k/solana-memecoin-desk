# Umsetzungsplan: vollständige Holder-Liste über Helius

Stand: 2026-11-09, Plan vom Nutzer freigegeben (Entscheidungen siehe
unten), noch nicht umgesetzt. Grundlage: `docs/helius_validierung.md`.

## Entscheidungen des Nutzers (2026-11-09)
- Freigegeben: `holders.max_pages: 30`, `holders.max_slot_span: 150`.
- Freigegeben: Rückfall auf die Top-20-Stichprobe, wenn die vollständige
  Abfrage fehlschlägt. Die Stichprobe darf nur ablehnen oder „unclear“
  liefern, nie bestätigen.
- Mehr als 30 Seiten oder Slot-Spanne überschritten → „unclear“.
- Ein Teilergebnis wird nie als vollständige Holder-Liste behandelt.
- Permanent Delegate (Token-2022): erkannt → „unclear“, Adresse und
  Erweiterung protokollieren, keine Freigabe für Paper-Trading. Eine
  endgültige Ablehnungsregel wird später gesondert entschieden.
- Slot jeder Seite erfassen und prüfen; fehlende oder widersprüchliche
  Slots → „unclear“.

## Unverändert
- Prüfregeln Top-10 (25 %) und Entwickler-Anteil (3 %), alle
  Sicherheitsprüfungen, Umlaufmengenregel, bestehende Grenzwerte,
  `mode: paper`.
- Pool-/Burn-Zuordnung und Bewertungslogik in `desk/holders.py`.
- Keine neuen Abhängigkeiten, keine kostenpflichtigen Dienste
  (Helius Free: 1 Mio. Credits/Monat).

## 1. Datenabruf (PR A, `desk/solana_rpc.py`)

### 1.1 Mint-Prüfung: `fetch_mint_info(mint)`
- `getAccountInfo(mint, {encoding: jsonParsed, commitment: finalized})`.
- Owner-Programm muss SPL Token (`Tokenkeg…`) oder Token-2022
  (`TokenzQd…`) sein, sonst Fehler.
- Liefert `token_program`, `decimals`, Slot und die Liste der
  Erweiterungen (nur Token-2022).
- Permanent Delegate: Erweiterung `permanentDelegate` vorhanden →
  `permanent_delegate` = Adresse (oder `null`, wenn die Erweiterung
  vorhanden, aber ohne Adresse ist; zählt trotzdem als erkannt).
- Erweiterungsliste fehlt oder ist nicht lesbar bei Token-2022 →
  Status „unbekannt“ (zählt als unclear, nicht als „kein Delegate“).

### 1.2 Vollständige Liste: `fetch_full_holder_list(mint, mint_info, max_pages, max_slot_span)`
1. **Supply vorher:** `getTokenSupply(mint, finalized)` → Menge A1,
   Slot S1.
2. **Seiten:** `getProgramAccountsV2(programm, …)` mit
   `commitment finalized`, `withContext true`, `minContextSlot S1`,
   `limit 1000`, `encoding base64`, `dataSlice {offset 32, length 40}`.
   - Filter klassisch: `memcmp {offset 0, bytes mint}` + `dataSize 165`.
   - Filter Token-2022: nur `memcmp` (Konten mit Erweiterungen sind
     größer als 165 Byte).
   - Weiter über `paginationKey`, bis keiner mehr kommt.
   - Seite 31 wäre nötig → Abbruch „too_many_pages“.
3. **Supply nachher:** `getTokenSupply` mit `minContextSlot` = höchster
   Seiten-Slot → Menge A2, Slot S2.
4. **Dekodieren:** je Konto genau 40 Byte: Owner (32 Byte → base58,
   eigener Encoder ohne neue Abhängigkeit), Menge (u64 little-endian).
5. **Leere Konten verwerfen** (Menge 0).

### 1.3 Pflichtprüfungen
Jede Verletzung → `SolanaRpcError` mit festem Grund. Es wird nie ein
Teilergebnis zurückgegeben.

| Prüfung | Fehlergrund |
|---|---|
| letzte Seite ohne `paginationKey`, höchstens 30 Seiten | `too_many_pages`, `pagination_incomplete` |
| jede Seite hat einen ganzzahligen Slot (`context.slot`) | `page_slot_missing` |
| jeder Seiten-Slot ≥ S1 | `page_slot_before_supply` |
| Seiten-Slots nicht fallend | `page_slot_inconsistent` |
| S2 ≥ höchster Seiten-Slot | `supply_slot_inconsistent` |
| S2 − S1 ≤ `max_slot_span` (150 ≈ 1 Minute) | `slot_span_exceeded` |
| A1 = A2 | `supply_changed` |
| jedes Konto: 40 Byte, Owner vorhanden, gültige Menge | `invalid_account_data` |
| Summe aller Bestände = A1 exakt | `supply_sum_mismatch` |

Hinweis (nicht belegt): Ob Helius bei `getProgramAccountsV2` mit
`withContext` je Seite einen Slot liefert, ist in den bisherigen Tests
nicht ausdrücklich geprüft worden. Fehlt er, greift `page_slot_missing`:
Alles bleibt dann „unclear“ und ist im ersten Scan sofort sichtbar.

### 1.4 Grenzen der Summenprüfung
- Die Seiten werden nacheinander gelesen. Das Ergebnis ist **keine
  atomare Momentaufnahme** eines einzigen Slots.
- Wandern Token zwischen einer schon gelesenen und einer noch nicht
  gelesenen Seite, stimmt die Summe nicht mehr → „unclear“.
- **Gegenläufige Überweisungen gleicher Höhe können sich rechnerisch
  ausgleichen.** Die Summe passt dann, obwohl einzelne Bestände aus
  verschiedenen Zeitpunkten stammen. Dieses Restrisiko wird durch die
  Slot-Spanne begrenzt, nicht ausgeschlossen.
- Eine Holder-Bewertung gilt nur für ihren Zeitpunkt (siehe USAR).

### 1.5 Rückgabe
Bisheriges Snapshot-Format (`mint`, `accounts`, `supply_amount_raw`,
`supply_decimals`, `sample_owner_mapping_complete`), dazu:
`data_scope: "full_token_account_list"`, `token_program`, `pages`,
`accounts_total`, `accounts_nonzero`, `slot_supply_before`,
`page_slots`, `slot_supply_after`, `slot_span`.

## 2. Token-2022-Sonderfälle
| Fall | Wirkung | Behandlung |
|---|---|---|
| Konten > 165 Byte (Erweiterungen) | `dataSize` würde sie ausschließen | kein `dataSize` |
| Permanent Delegate | Delegate kann Token aus jeder Wallet bewegen | **unclear**, Adresse + Erweiterung protokolliert |
| Erweiterungen nicht lesbar | Delegate unbekannt | unclear |
| Transfer-Fee (einbehaltene Gebühren) | nicht in `amount` → Summe < Supply | `supply_sum_mismatch` → unclear |
| Confidential Transfer | verschlüsselte Bestände → Summe < Supply | unclear |
| Interest-bearing / Scaled UI Amount | nur Anzeige skaliert | nur Rohmengen verwenden |
| Mint-Konto selbst | beginnt mit COption-Tag, trifft den Mint-Filter praktisch nicht | Dekodierprüfung fängt Fehllängen ab |

Alle erkannten Erweiterungen werden protokolliert
(`token2022_extensions`).

## 3. Einbindung (PR B)

### 3.1 `desk/security_scan.py`
- Je Token einmal `fetch_mint_info`, dann `fetch_full_holder_list`.
- Schlägt die vollständige Liste fehl → Rückfall auf
  `fetch_holder_snapshot` (Top-20). Protokolliert werden
  `holder_source: "sample_fallback"` und `full_holder_list_error`
  (fester Grund, nie URL oder Key).
- Neue Pflichtprüfung `permanent_delegate_absent` in `missing_checks`:
  - geklärt nur bei klassischem SPL-Token oder Token-2022 mit lesbarer
    Erweiterungsliste ohne Permanent Delegate;
  - erkannt → bleibt offen, Grund `permanent_delegate_present`,
    `permanent_delegate` (Adresse) und Erweiterung im Protokoll;
  - Mint-Abfrage fehlgeschlagen oder Erweiterungen unbekannt → bleibt
    offen.
  - Folge: Status höchstens `incomplete`, nie
    `ready_for_full_risk_screen`. Keine Ablehnung (spätere
    Entscheidung).

### 3.2 `desk/holders.py`
- Bewertungslogik unverändert.
- Neu: Bestätigen (`confirmed`) nur, wenn
  `data_scope == "full_token_account_list"` **und** die nicht erfasste
  Menge 0 ist. Bei der Stichprobe oder beim Rückfall sind nur
  `rejected` oder `unclear` möglich, auch wenn die Obergrenze unter dem
  Limit liegt (Grund `sample_cannot_confirm`).
- Das ist strenger als bisher: Heute könnte eine Stichprobe mit
  vollständiger Abdeckung bestätigen. Das entspricht der Bedingung des
  Nutzers.
- `data_scope` und `is_complete_distribution` kommen aus dem Snapshot,
  nicht mehr fest „sample“.

### 3.3 Marktkapitalisierung
Nutzt weiter `supply_amount_raw`, keine Änderung.

## 4. Konfiguration (freigegeben)
```yaml
holders:
  max_pages: 30
  max_slot_span: 150
```
`desk/config.py`: Abschnitt Pflicht, beide Werte positive Ganzzahlen
(kein bool), sonst Programmstopp. Werte dürfen nicht stillschweigend
ersetzt werden.

## 5. Tests (ohne Netzwerk, gemockt)
PR A:
- Pagination: 1, 3 und 30 Seiten ok; 31. Seite → `too_many_pages`.
- Slots: fehlend, kein int, < S1, fallend, S2 < Seiten-Slot, Spanne 151
  → jeweils Fehler; Spanne 150 → ok.
- Supply: A1 ≠ A2; Summe zu hoch, zu niedrig, exakt.
- Leere Konten gefiltert; mehrere Konten je Owner bleiben einzeln
  erhalten (Aggregation in `holders.py`).
- Dekodierung: bekannter Owner/Menge, falsche Länge, fehlende Daten,
  base58-Encoder gegen bekannte Adressen.
- Filter: klassisch mit `dataSize 165`, Token-2022 ohne; fremdes
  Programm → Fehler.
- Mint-Info: Permanent Delegate erkannt (mit/ohne Adresse),
  Erweiterungen fehlen → unbekannt, klassischer Token → keine.
- Fehlermeldungen enthalten nie URL/Key.

PR B:
- Vollständige Liste → bestätigt bei ≤ 25 % / ≤ 3 %, abgelehnt darüber.
- Stichprobe und Rückfall: nie `confirmed`, Ablehnung weiter möglich.
- Rückfall wird bei jedem Fehlergrund aus 1.3 genutzt und protokolliert.
- Permanent Delegate → Status `incomplete`, nie bereit, keine Ablehnung.
- Config: fehlender Abschnitt, 0, negativ, bool → Programmstopp.
- Alle bestehenden Tests grün; angepasst werden nur Tests, die bisher
  eine Bestätigung durch die Stichprobe erwarten (im PR einzeln
  aufgeführt).

## 6. Verbrauch
Je Token: 1 `getAccountInfo` + 2 `getTokenSupply` + Seiten (meist 1–5)
≈ 4–8 Credits; Rückfall + 3 Credits. 10 Token je Lauf ≈ 80–110 Credits;
selbst stündlich ≈ 80.000 Credits/Monat (≈ 8 % des Freikontingents).

## 7. Ablauf
1. PR A: Mint-Info, vollständige Liste, Pflichtprüfungen, Tests. Noch
   nicht eingebunden, Scan-Verhalten unverändert.
2. PR B: Einbindung, Config-Abschnitt, Rückfall, Permanent-Delegate-
   Prüfung, Stichprobe darf nicht bestätigen, README/CLAUDE.md.
3. Ein manueller Scan über GitHub Actions; Auswertung mit
   `scan-auswertung`, Credits im Helius-Dashboard prüfen.
4. Danach Entscheidung über die Löschung des Test-Branches.

## Später gesondert zu entscheiden
- Endgültige Ablehnungsregel für Permanent Delegate.
- Weitere riskante Token-2022-Erweiterungen (z. B. Transfer Hook, der
  Verkäufe blockieren könnte): werden protokolliert, aber nicht bewertet.

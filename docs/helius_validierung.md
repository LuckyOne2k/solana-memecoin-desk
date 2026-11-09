# Helius-Validierung (2026-11-09)

Rein lesende Tests gegen Helius (Secret `SOLANA_RPC_URL`), ausgeführt über
GitHub Actions auf dem Test-Branch `claude/helius-verbindungstest`
(letzter Commit `5214857859e033ed5313e34d8cd638fcc8ea3784`, Skripte in
`.github/helius_test/`). Keine Codeänderung am Projekt, kein Scan.
Verbrauch gesamt ca. 204 Credits (geschätzt, ca. 0,02 % des kostenlosen
Monatskontingents).

## Verfahren
- `getProgramAccountsV2`: 1 Credit je Seite (bis 1.000 Konten), Filter
  `memcmp` offset 0 = Mint, klassischer Token zusätzlich `dataSize 165`,
  Token-2022 ohne `dataSize`; `dataSlice` offset 32, Länge 40
  (Owner 32 Byte + Menge u64 little-endian). Liefert auch leere Konten.
- DAS `getTokenAccounts`: 10 Credits je Seite, `cursor`, ohne leere Konten.
- Programm-IDs: SPL Token `TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA`,
  Token-2022 `TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb` (Programm des
  Mint-Kontos per `getAccountInfo` ermittelt).

## Verbindung
`getHealth` ok, `getSlot` und `getVersion` HTTP 200, Antwortzeit < 0,1 s,
keine 429. Key nie ausgegeben.

## Ergebnisse
| Token | Programm | Konten mit Bestand | leer (nur V2) | Seiten V2/DAS | ohne Owner | Summe = Supply | V2 = DAS |
|---|---|---|---|---|---|---|---|
| NAOF | Token-2022 | 2.642 | 28–32 | 3 / 4 | 0 | exakt | ja |
| PAD | Token-2022 | 2.656 | 28–32 | 3 / 4 | 0 | exakt | ja |
| USAR | Token-2022 | 24 | wenige | 1 / 2 | 0 | exakt | ja |
| MAGGIE | SPL Token | 26 | 22 | 1 / 2 | 0 | exakt | ja |
| TAOLF | SPL Token | 702 | 1.836 | 3 / 2 | 0 | exakt | ja |

- Zeitversatz zwischen Supply und Liste: 2–6 Slots; Summe trotzdem exakt.
- „V2 = DAS“: identische Eigentümer und Mengen aller Konten mit Bestand.
- TAOLF: 72 % der Konten leer → leere Konten müssen gefiltert werden.
- NAOF/PAD: Top-10 roh nur 1–2 %, sehr gleichmäßig auf > 2.600 Wallets.
  Vermutung (nicht belegt): künstlich verteilte Menge. Wallets derselben
  Person sind weiterhin nicht erkennbar.

## USAR-Widerspruch
Scan 12:02: Top-20 deckten ca. 1 % ab. Helius-Test später: 24 Holder,
100 % erfasst.

Fakten:
1. Alte Methode (`fetch_holder_snapshot`, unverändert, über Helius) und
   vollständige Liste im Abstand von 2 Slots: alle 20 Konten und Mengen
   identisch, Abdeckung 100 %.
2. Pool (`FU8vnN6g4x1YH6dDGtV4sgJNXwyVfStALf2mqByWG61e`) hält danach
   99,65 % (beim Scan 12:02: 0,38 %).
3. Transaktionen am Mint: 97 (11:27–12:02), 1.623 (12:02–12:38).
4. GeckoTerminal: Kurs 0,108 $ → 0,0000015 $ (−99,999 %), Liquidität
   danach 79 $.

Schluss (belegt): Die Verteilung hat sich zwischen den Läufen tatsächlich
geändert. Für einen Fehler der alten Methode gibt es keinen Beleg.
Offen: welche RPC der Scan um 12:02 nutzte; genaue damalige Verteilung.
Lehre: Eine Holder-Bewertung gilt nur für ihren Zeitpunkt; vor jedem
simulierten Kauf neu prüfen.

## Fehler während der Tests (behoben)
- Secret enthielt zuerst nur den Key statt der URL → Nutzer hat URL
  eingetragen.
- Erstes Skript: falsche Token-2022-ID in der Anzeige (Abfragen nutzten
  die ID aus der Blockchain, Ergebnisse gültig).
- Fehlermeldungen von `requests` enthalten die URL samt Key → PR #7
  (`_safe_request_error`).

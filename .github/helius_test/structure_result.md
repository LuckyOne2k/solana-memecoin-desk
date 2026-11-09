# Strukturtest getProgramAccountsV2 mit withContext (2026-11-09)

Lauf: GitHub Actions 42624589998, Commit 7380c24, Token NAOF (Token-2022),
2 Seiten, rein lesend. Geschätzt 2 Credits.

## Tatsächliche Antwort (beide Seiten gleich)
```
result: {
  context: { apiVersion: str, slot: int },
  value:   { accounts: list[1000], count: int, paginationKey: str(44) }
}
accounts[i]: { pubkey: str, account: { data: [base64-str, "base64"],
               executable, lamports, owner, rentEpoch, space } }
```
- Slot: `result.context.slot`, Ganzzahl (beide Seiten 467332135).
- Konten: `result.value.accounts`, je Seite 1000.
- paginationKey: `result.value.paginationKey`, String (44 Zeichen).
- `account.owner` = Token-2022-Programm; `data` dekodiert genau 40 Byte.

## Vergleich mit PR #11
Erwartet: `{context: {slot}, value: {accounts, paginationKey}}` → passt.
Zusätzliche Felder (`context.apiVersion`, `value.count`, Konto-Felder)
werden vom Parser ignoriert. Owner-Prüfung je Konto greift (Feld vorhanden).

## Letzte Seite (Test 2, MAGGIE)
Lauf: GitHub Actions 42625141977, Commit 21cd8bc, MAGGIE (klassischer
SPL-Token, Filter `dataSize 165`), genau 1 Aufruf, geschätzt 1 Credit.

- Struktur wie oben: `context {apiVersion, slot}`, `value {accounts,
  count, paginationKey}`; `context.slot` Ganzzahl.
- `value.accounts`: vorhanden, Liste mit 49 Konten (alle Konten inkl.
  leerer; im Validierungstest 48 = 26 mit Bestand + 22 leer, seitdem
  1 Konto mehr).
- `value.count`: 49 = Anzahl Konten auf der Seite.
- **`value.paginationKey` auf der letzten Seite: `null`** (Schlüssel
  vorhanden, Wert null; weder fehlend noch leerer String).
- PR-#11-Parser erkennt die letzte Seite korrekt (`null` → Ende).

## Ergebnis
Die Antwortform entspricht in allen geprüften Fällen (erste, mittlere,
letzte Seite; Token-2022 und klassisch) der Erwartung von PR #11. Keine
Codeänderung nötig.

Offen (Vorschlag, nicht umgesetzt): `value.count` als zusätzliche
Gegenprobe (`count == len(accounts)`), nur als Verschärfung.

## Panne im Testskript
Die Vergleichszeile gab durch einen Python-Ausdruck den Wert des
paginationKey aus (2×). Das ist eine öffentliche Konto-Adresse als
Seitenzeiger, kein Secret und keine Kontodaten mit Bestand; trotzdem
entgegen der Vorgabe. URL/Key wurden nicht ausgegeben (GitHub-Schwärzung
und eigene Schwärzung).

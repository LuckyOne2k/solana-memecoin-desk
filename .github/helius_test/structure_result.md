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

## Nicht geprüft
- Form der letzten Seite (paginationKey fehlt, `null` oder `""`?). Der
  Parser akzeptiert fehlend/`null`; `""` ergäbe `page_response_invalid`.
- Bedeutung von `value.count`.

## Panne im Testskript
Die Vergleichszeile gab durch einen Python-Ausdruck den Wert des
paginationKey aus (2×). Das ist eine öffentliche Konto-Adresse als
Seitenzeiger, kein Secret und keine Kontodaten mit Bestand; trotzdem
entgegen der Vorgabe. URL/Key wurden nicht ausgegeben (GitHub-Schwärzung
und eigene Schwärzung).

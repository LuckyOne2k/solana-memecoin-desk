# Vorschlag: offene Entscheidungen (Stand 2026-11-09)

Reines Entscheidungsdokument. **Es ändert keinen Code, keine Grenzwerte,
keine Strategie und keine Sicherheitsregel.** Alle Vorschläge brauchen
die Freigabe des Nutzers; unabhängiges Review durch ChatGPT erforderlich.

Grundlage (geprüft am Repository-Stand `0863807`, nach PR #22/#23):
`config.yaml`, `desk/security_scan.py`, `desk/solana_rpc.py`,
`desk/risk.py`, `desk/debate.py`, `desk/__main__.py`, `docs/entscheidungen.md`,
Protokoll `desk-data` (`events.jsonl`, 78 Zeilen, 31 Scans mit Pool-Details,
letzter Scan #55 vom 2026-11-09 20:45 UTC).

Kennzeichnung: **[Fakt]** = im Repository/Protokoll nachgeprüft,
**[Vermutung]** = nicht belegt, **[Entscheidung]** = braucht den Nutzer.

---

## 1. Paper-Broker-Einstiegsregel

### 1.1 Ausgangslage

- [Fakt] Status `ready_for_full_risk_screen` heißt nur: jede Sicherheits-
  und Datenprüfung ist bestätigt. Es ist ein **Filter, kein Kaufsignal**.
- [Fakt] Bisher bereit: nur TTP und BRUH, in 6 Scans in Folge
  (18:55–20:45 UTC am 2026-11-09). Beide PumpSwap, Token-2022 nur mit
  `metadataPointer`/`tokenMetadata`, vollständige Holder-Liste,
  Marktkapitalisierung über die On-Chain-Spanne (nicht über gemeldete
  Umlaufmenge).
- [Fakt] BRUH-Untergrenze schwankte zwischen 332k und 353k $, also knapp
  über der 300k-Grenze – kann zwischen Scans kippen.
- [Fakt] Im selben Scan gab es einen zweiten Token mit Symbol „BRUH“
  (andere Mint-Adresse, abgelehnt). **Symbole sind nicht eindeutig.**
- [Fakt] Scans laufen nur manuell; Abstände bisher 5 Minuten bis Stunden.
- [Fakt] `config.yaml → paper_trading`: Position max. 8 %, Stop 6 %,
  Trailing ab +12 % mit 6 % Abstand. `desk/risk.py` rechnet das bereits,
  ist aber nicht angeschlossen. `desk/debate.py` darf nur bremsen.
- [Fakt] Im Protokoll fehlen je Pool `pair_age_hours`, `created_at`,
  `volume_usd_24h` und Transaktionszahlen (werden in der Vorprüfung
  berechnet, aber nicht geschrieben).

### 1.2 Pflichtbedingungen für jede Variante (nicht verhandelbar)

Diese folgen direkt aus den bestehenden Sicherheitsregeln:

1. Scan-Status `ok`; Pool im **selben** Scan `ready_for_full_risk_screen`.
2. Einstieg nur im selben Lauf direkt nach dem Scan, nie auf Basis
   älterer Daten.
3. Einstiegspreis = höherer der zwei übereinstimmenden Preise
   (`price_used_usd`, Grund `price_sources_agree`). Fehlt einer → kein Einstieg.
4. Identifikation nur über Mint-Adresse, nie über Symbol.
5. Positionsgröße nur über `risk.position_size_usd` mit `config.yaml`.
6. Jede fehlende, widersprüchliche oder fehlerhafte Angabe → kein
   Einstieg (Status „unvollständig“), nie Ersatzwerte.
7. Debatte (später KI) kann nur blockieren, nie freigeben.
8. Ausstieg: Stops werden nur zu Scan-Zeitpunkten geprüft. Simulierter
   Ausstieg zum **beobachteten** Scan-Preis, nicht zum Stop-Preis
   (sonst sind Paper-Ergebnisse zu optimistisch). Fehlt der Preis →
   Position „Preis unklar“, kein fiktiver Ausstieg.

### 1.3 Varianten

**A – Sofort bei „bereit“**
Einstieg, sobald ein Pool im aktuellen Scan bereit ist.

| | |
|---|---|
| Chancen | Einfachste Regel, nutzt nur vorhandene Daten, viele Datenpunkte zum Lernen. |
| Risiken | Momentaufnahme: Grenzfälle (BRUH ~330k $) können im nächsten Scan kippen; Einstieg zu zufälligem Zeitpunkt. |
| Fehlalarme | Hoch. „Bereit“ sagt nichts über Kursrichtung. |
| Datenbedarf | Nur vorhandene Daten. |

**B – Bestätigung in zwei Scans (empfohlen)**
Einstieg erst, wenn derselbe Pool (Mint + Pool-Adresse) in zwei
aufeinanderfolgenden Scans bereit ist, mit Mindest- und Höchstabstand,
und sich Preis und Liquidität dazwischen nicht stark verändert haben.
Einstieg zum Preis des zweiten Scans.

| | |
|---|---|
| Chancen | Filtert Zufallstreffer und kippende Grenzwerte; nutzt nur `events.jsonl`, keine neue Quelle; leicht prüfbar. |
| Risiken | Späterer Einstieg, schnelle Bewegungen werden verpasst; abhängig vom manuellen Scan-Takt. |
| Fehlalarme | Mittel. Stabilität ist noch kein Kurssignal. |
| Datenbedarf | Vorhanden: Status, Preis, Liquidität je Scan. Neu: Lesen des vorigen Scans (wie `check_queue.py`); beschädigtes Protokoll → kein Einstieg. |
| Offene Parameter [Entscheidung] | Mindestabstand (Vorschlag 30 Min.), Höchstabstand (Vorschlag 6 h), max. Preisänderung (Vorschlag ±15 %), max. Liquiditätsrückgang (Vorschlag 20 %). |

**C – „Bereit“ + Marktaktivität**
Wie A, zusätzlich Mindestvolumen/Transaktionen bzw. Kauf-/Verkaufs-Verhältnis.

| | |
|---|---|
| Chancen | Am nächsten an einem echten Handelssignal. |
| Risiken | Volumen ist nur aus einer Quelle (DexPaprika) – verstößt gegen das Zwei-Quellen-Prinzip; Memecoin-Volumen leicht manipulierbar (Wash-Trading); viele neue Strategieparameter, Gefahr der Überanpassung. |
| Fehlalarme | Unklar, ohne Datenbasis nicht abschätzbar. |
| Datenbedarf | Neu: Volumen/Transaktionen ins Protokoll, zweite Quelle nötig. |

### 1.4 Empfehlung

**Variante B**, zusätzlich mit allen Pflichtbedingungen aus 1.2.
Begründung: konservativ, keine neue Datenquelle, keine Lockerung, prüft
genau die bekannte Schwachstelle (Momentaufnahme, Werte nahe 300k $).
Variante C erst später als Auswertung echter Paper-Ergebnisse, nicht als
Startregel. Für eine spätere Auswertung empfehle ich, `pair_age_hours`
und `volume_usd_24h` schon jetzt mitzuprotokollieren (nur Protokoll, keine
Bewertung) – eigener kleiner PR, nur mit Freigabe.

### 1.5 Weitere Broker-Entscheidungen [Entscheidung]

- Startkapital des Paper-Depots (z. B. 10.000 virtuelle $).
- Höchstzahl gleichzeitiger Positionen; nur eine Position je Token?
- Wiedereinstieg nach Stop beim selben Token erlaubt?
- Was passiert mit offener Position, wenn der Token später `rejected`
  wird (Vorschlag: simulierter Sicherheitsausstieg zum Scan-Preis) bzw.
  `incomplete` (Vorschlag: halten, markieren, keine Nachkäufe)?
- Slippage-Modell (Vorschlag: Abschlag abhängig von Positionsgröße/Pool-Liquidität).
- Hinweis [Vermutung]: Stop 6 % ist bei Memecoins sehr eng und wird bei
  seltenen, manuellen Scans oft weit unter dem Stop-Preis ausgelöst.
  Keine Änderung vorgeschlagen, nur zur Kenntnis.

---

## 2. Permanent Delegate und riskante Token-2022-Erweiterungen

### 2.1 Heutiger Stand [Fakt]

- `solana_rpc._read_extensions` liest nur die **Namen** der Erweiterungen
  und die Delegate-Adresse. Inhalte (Gebühr, Hook-Programm, Pause-Status)
  werden nicht ausgewertet.
- `security_scan._evaluate_mint_info`: Positivliste
  `SAFE_TOKEN_2022_EXTENSIONS` (nur Metadaten/Gruppe). Alles andere,
  unbekannt oder unlesbar → Pflichtprüfung offen → „unvollständig“, nie
  „bereit“. **Es gibt heute keine Ablehnung über Erweiterungen.**
- Permanent Delegate `present` gilt auch dann, wenn die Adresse fehlt.
- Live-Bilanz: 20 Token mit abgeschlossener Mint-Info, alle Token-2022,
  alle nur `metadataPointer` + `tokenMetadata`, 0 × Permanent Delegate.
  Die Regel hätte bisher also **keinen einzigen Fall** verändert.
- Gegenprobe Feldnamen (1 kostenloser Aufruf an die öffentliche RPC,
  PYUSD `2b1kV6Dk…4GXo`): `permanentDelegate.state.delegate`,
  `transferFeeConfig.state.{transferFeeConfigAuthority, olderTransferFee,
  newerTransferFee.transferFeeBasisPoints, maximumFee}`,
  `transferHook.state.{authority, programId}`,
  `mintCloseAuthority.state.closeAuthority`,
  `confidentialTransferMint.state.{authority, autoApproveNewAccounts}`.
  Nicht live gesehen: `pausableConfig`, `defaultAccountState`,
  `nonTransferable`, `scaledUiAmountConfig`, `interestBearingConfig`
  (Feldnamen vor Umsetzung belegen).

### 2.2 Grundsatz

Abgelehnt wird **nur bei positiv belegtem Risiko** (Erweiterung gelesen,
Feld vorhanden, Wert eindeutig). Jede Unsicherheit (Feld fehlt, falscher
Typ, unbekannte oder unlesbare Erweiterung) bleibt **„unklar“** – genau
wie heute. Eine Erweiterung wird nie allein deshalb unbedenklich, weil
ein Feld fehlt.

### 2.3 Vorgeschlagene Einteilung

**Kategorie 1 – grundsätzlich ablehnen** (neuer Ablehnungsgrund)

| Erweiterung | Bedingung für Ablehnung | Warum |
|---|---|---|
| `permanentDelegate` | `delegate` ist eine Adresse | kann Token aus jeder Wallet bewegen oder verbrennen |
| `nonTransferable` | vorhanden | Token nicht handelbar |
| `pausableConfig` | `authority` gesetzt **oder** `paused: true` | alle Überweisungen anhaltbar (wie Freeze) |
| `defaultAccountState` | `accountState: "frozen"` | neue Konten eingefroren, Verkauf blockiert |
| `transferHook` | `programId` gesetzt | fremdes Programm bei jeder Überweisung, kann Verkäufe blockieren (Honeypot) |
| `transferHook` | `programId` null, aber `authority` gesetzt | Hook kann jederzeit nachträglich eingeschaltet werden |
| `transferFeeConfig` | Gebühr > 0 (älter oder neuer) **oder** `transferFeeConfigAuthority` gesetzt | Gebühr vorhanden oder später bis 100 % erhöhbar |

**Kategorie 2 – bleibt „unklar“, nur nach ausdrücklicher Freigabe als
unbedenklich einstufbar** (heutiges Verhalten bleibt)

| Erweiterung | Grund |
|---|---|
| `permanentDelegate` mit `delegate: null` | Delegate entfernt – [Vermutung] unbedenklich, Form nicht live belegt |
| `transferHook` mit `programId` **und** `authority` null | dauerhaft ohne Hook – [Vermutung] unbedenklich |
| `transferFeeConfig` mit Gebühr 0 **und** Authority null | dauerhaft gebührenfrei – [Vermutung] unbedenklich |
| `mintCloseAuthority` | Mint nur bei Menge 0 schließbar; geringes Risiko |
| `confidentialTransferMint` / `…FeeConfig` | verdeckte Bestände → Holder-Liste nicht nachprüfbar |
| `interestBearingConfig`, `scaledUiAmountConfig` | angezeigte Menge ≠ Rohmenge, Marktkapitalisierung verfälschbar |
| `pausableConfig` mit `authority` null und `paused: false` | Form nicht live belegt |
| `defaultAccountState: "initialized"` | unbedenklich, solange Freeze Authority widerrufen (wird schon abgelehnt) |
| Konto-Erweiterungen auf einem Mint (`immutableOwner`, `memoTransfer`, `cpiGuard`, `*Account`, `transferFeeAmount`) | dürfen auf einem Mint nicht vorkommen → Datenfehler |
| unbekannt / `unparseableExtension` / Feld fehlt | Datenlücke |

**Kategorie 3 – unkritisch** (bereits heute so, keine Änderung)
`metadataPointer`, `tokenMetadata`, `groupPointer`, `tokenGroup`,
`groupMemberPointer`, `tokenGroupMember`.

### 2.4 Schwächt das bestehende Regeln? [Prüfung]

- Kategorie 1 macht aus „unvollständig“ nur „abgelehnt“. Ein Token kann
  dadurch **nie** bereit werden, der nicht schon heute nie bereit wird.
  → keine Lockerung.
- Kategorie 2 bleibt unverändert unklar. **Lockerung entstünde nur, wenn
  der Nutzer einen Eintrag aus Kategorie 2 in die Positivliste übernimmt.**
  Das muss einzeln freigegeben und mit live belegter Antwortform
  getestet werden.
- Nebenwirkung [Fakt]: abgelehnte Token rücken in `check_queue` nach
  hinten. Sie werden weiter frisch geprüft, nur später. Gewollt (spart
  Prüfplätze), aber eine Verhaltensänderung.
- Nebenwirkung: Ablehnung früher in der Kette spart Helius-Credits
  (Holder-Liste/On-Chain-Spanne entfallen).
- Risiko: Ein Parserfehler könnte fälschlich ablehnen statt unklar
  lassen – nur Komfortverlust, keine Sicherheitslücke. Tests müssen jede
  fehlende/falsch typisierte Feldangabe als „unklar“ abdecken.

### 2.5 Umsetzungsskizze (erst nach Freigabe)

1. `_read_extensions` liefert zusätzlich den `state` je Erweiterung
   (unverändert, nur durchgereicht).
2. Neue reine Funktion `classify_extension_risk(name, state)` →
   `reject` / `unclear` / `safe` mit festem Grund; `safe` nur für
   Kategorie 3.
3. `_evaluate_mint_info`: bei `reject` `_reject(item, "token_extension_<grund>")`,
   sonst Verhalten wie heute. Protokoll: Erweiterung, Grund, Adressen.
4. Tests: je Zeile der Tabellen ein Fall, plus fehlende/falsche Felder
   → immer unklar. Keine Config-Änderung nötig.

---

## 3. Test-Branches

| Branch | Stand [Fakt] | Hängt etwas davon ab? | Empfehlung |
|---|---|---|---|
| `claude/helius-verbindungstest` | 5 Commits nicht in `main` (nur `.github/helius_test/*.py`, `structure_result.md`, `helius_test.yml`), 36 hinter `main` | Kein offener PR. Ergebnisse stehen in `docs/helius_validierung.md` (Strukturtest + letzte Seite, Commits `7380c24`/`21cd8bc` dort zitiert). Integration ist mit PR #11/#12 abgeschlossen. | **Löschen.** Einziger Verlust: die Testskripte. Wer sie behalten will, vorher Tag setzen (z. B. `archiv/helius-test-5214857`). |
| `pr1` und `desk/__init__.py` | identisch (`4de425b`, 2026-11-03), eigene Historie ohne gemeinsame Basis mit `main`; Inhalt = alter Upload (u. a. `rules.py`, inzwischen entfernt) | PR #1 geschlossen, nicht gemergt | **Löschen** (beide). |
| `claude/geckoterminal-retry`, `claude/optimize-claude-usage-xup98g`, `claude/scan-auswertung-skill`, `claude/verschlankung` | vollständig in `main` gemergt (0 eigene Commits) | nein | **Löschen.** |
| `desk-data` | Scan-Protokoll | ja, Web-App und Scans | **Behalten.** |

Es gibt aktuell **keinen offenen PR** (geprüft über GitHub, PR #1–#23).
Nichts gelöscht.

---

## 4. Technische Bestandsaufnahme nach PR #22/#23

| Prio | Befund [Fakt] | Vorschlag |
|---|---|---|
| Mittel | **Mint-/Freeze-Authority nur aus RugCheck.** `fetch_mint_info` liest per RPC bereits `mintAuthority`/`freezeAuthority`, sie werden aber nicht verglichen. Wichtigste Prüfung hängt an einer Quelle – widerspricht dem Zwei-Quellen-Prinzip. | Gegenprobe: RPC zeigt Authority → ablehnen; Widerspruch → unklar. Neue Sicherheitsprüfung → **Freigabe nötig**. |
| Mittel | **Veraltete Kommentare zur Sicherheitslogik**: `desk/security_scan.py:19` und `desk/market_cap.py:32` („Standard aus“, ist seit PR #16 an), `desk/market_cap.py:21` und `config.yaml:12` („Bestehen nur mit gemeldeter Umlaufmenge“, On-Chain-Spanne kann bestätigen), `desk/solana_rpc.py:433` („noch nicht eingebunden“). Kann Reviewer (ChatGPT) in die Irre führen. | Kleiner Doku-PR, nur Kommentare. |
| Niedrig | `rugcheck_risks` wird erfasst (`desk/security.py:310`), aber weder bewertet noch protokolliert. | Nur protokollieren (keine Bewertung) – optional, Freigabe. |
| Niedrig | Protokoll enthält kein Paaralter, Volumen, Transaktionen je Pool. | Mitprotokollieren, falls Broker-Variante B/C gewählt wird. |
| Niedrig | Gleiche Symbole bei verschiedenen Token (BRUH). Web-App zeigt Kurzadresse – ausreichend. Paper-Broker muss über Mint-Adresse arbeiten. | In Broker-Spezifikation festhalten. |
| Niedrig | 7 überholte Branches (siehe 3). | Löschen durch Nutzer. |
| Info | Nicht geprüft: ob GitHub Pages bereits auf „GitHub Actions“ steht und die App auf dem Handy läuft (kein Zugriff auf Repo-Einstellungen). | Nutzer prüft. |

Keine offensichtlichen Fehler in der Prüflogik gefunden. Kein vollständiger
Code-Review aller 6.000 Zeilen durchgeführt – nur die für die Aufgaben
relevanten Module.

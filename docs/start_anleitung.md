# Startanleitung (für Pascal)

Stand 2026-11-10. Der Paper-Trader läuft automatisch über GitHub
Actions. Er kostet nichts und arbeitet ohne echtes Geld. Was du noch tun
kannst, ist alles optional bis auf Schritt 1.

## 1. App auf dem Handy (einmalig, falls noch nicht erledigt)

1. GitHub → Repository → **Settings → Pages**.
2. Unter „Build and deployment“ als Quelle **GitHub Actions** wählen.
3. Actions → „App veröffentlichen“ → **Run workflow**.
4. Die angezeigte Adresse auf dem Handy in Chrome öffnen. Dann über das
   Menü (⋮) **Zum Startbildschirm hinzufügen** wählen.

Die App zeigt oben das **Paper-Depot** und darunter den letzten Scan.

## 2. Telegram-Meldungen (optional, kostenlos)

Du bekommst dann bei jedem simulierten Kauf und Verkauf eine Nachricht.

1. In Telegram **@BotFather** öffnen und `/newbot` senden. Name und
   Benutzername wählen. Du erhältst einen **Bot-Token**
   (`123456789:ABC…`).
2. Deinem neuen Bot eine beliebige Nachricht schicken, zum Beispiel „hi“.
3. Im Browser
   `https://api.telegram.org/bot<DEIN_TOKEN>/getUpdates` öffnen. Dort
   steht bei `"chat":{"id": …}` deine **Chat-ID**, eine Zahl.
4. GitHub → **Settings → Secrets and variables → Actions → New
   repository secret** und zwei Secrets anlegen:
   - `TELEGRAM_BOT_TOKEN` = Bot-Token
   - `TELEGRAM_CHAT_ID` = Chat-ID
5. Fertig. Ab dem nächsten Lauf kommen Nachrichten. Ohne diese Secrets
   läuft alles normal weiter, nur ohne Meldungen.

Den Token nie in Chat, Issues oder Code schreiben, nur in die Secrets.

## 3. Was schon eingerichtet ist

| Was | Wo | Status |
|---|---|---|
| `SOLANA_RPC_URL` (Helius, kostenlos) | Secrets | vorhanden |
| `DEXPAPRIKA_API_KEY` | Secrets | vorhanden |
| Stündlicher Scan + Paper-Broker | `scan.yml` | aktiv nach Merge |
| Kursüberwachung alle 15 Min. | `paper-monitor.yml` | aktiv nach Merge |

Helius-Verbrauch bei stündlichem Scan: geschätzt 80.000–110.000 Credits
pro Monat, also etwa 10 % des Freikontingents. Prüfe das nach ein paar
Tagen im Helius-Dashboard.

## 4. Bedienung

- **Scan sofort starten:** Actions → „Solana Memecoin Desk“ → Run
  workflow.
- **Depot sofort prüfen:** Actions → „Paper-Depot überwachen“ → Run
  workflow.
- **Alles pausieren:** Actions → Workflow → ⋯ → **Disable workflow**,
  für beide Workflows.
- **Depot zurücksetzen:** Im Branch `desk-data` die Datei
  `paper_state.json` löschen. Beim nächsten Lauf startet ein neues Depot
  mit 10.000 virtuellen $.
- Nach 60 Tagen ohne Commit auf `main` schaltet GitHub geplante Läufe ab.
  Dann einfach wieder **Enable workflow** klicken.

## 5. Was der Bot nicht tut

Er kauft nie echt. Es gibt keine Wallet, keinen privaten Schlüssel und
keine Signatur. „Bereit“ und ein Paper-Kauf sind keine Kaufempfehlung.
Live-Trading wäre eine eigene, neue Entscheidung mit eigener
Sicherheitsprüfung und ist nicht vorbereitet.

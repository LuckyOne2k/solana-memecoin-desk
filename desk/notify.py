"""Optionale Telegram-Meldungen (kostenlos, nur Text).

Ohne die Umgebungsvariablen TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID
wird nichts gesendet ("not_configured"). Der Bot-Token steht in der
Abfrage-URL und erscheint deshalb nie in einer Rückgabe oder Ausgabe;
Fehler werden nur als Typ und HTTP-Status gemeldet. Ein Fehler beim
Senden stoppt nie den Paper-Broker.
"""

import os

import requests


TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4000
PREFIX = "Memecoin Desk (PAPER, kein echtes Geld)\n"


def send_telegram(text, timeout=15):
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not chat_id:
        return "not_configured"
    if not text:
        return "nothing_to_send"

    try:
        response = requests.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": (PREFIX + text)[:MAX_MESSAGE_LENGTH],
                "disable_web_page_preview": True,
            },
            timeout=timeout,
        )
        status = response.status_code
    except requests.RequestException as error:
        return f"error:{type(error).__name__}"

    return "sent" if status == 200 else f"error:HTTP {status}"

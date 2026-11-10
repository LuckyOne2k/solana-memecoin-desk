"""Telegram-Meldungen: optional, Token nie in Rückgaben."""

import requests

from desk import notify


TOKEN = "123456:GEHEIM-token"


class Response:
    def __init__(self, status):
        self.status_code = status


def configure(monkeypatch, token=TOKEN, chat="42"):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", chat)


def test_not_configured_sends_nothing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    def fail(*a, **k):
        raise AssertionError("no request expected")
    monkeypatch.setattr(notify.requests, "post", fail)

    assert notify.send_telegram("x") == "not_configured"


def test_sends_with_paper_prefix(monkeypatch):
    configure(monkeypatch)
    sent = {}

    def post(url, json, timeout):
        sent.update(url=url, body=json)
        return Response(200)
    monkeypatch.setattr(notify.requests, "post", post)

    assert notify.send_telegram("PAPER KAUF X") == "sent"
    assert sent["body"]["chat_id"] == "42"
    assert sent["body"]["text"].startswith("Memecoin Desk (PAPER")
    assert "PAPER KAUF X" in sent["body"]["text"]


def test_errors_never_contain_token(monkeypatch):
    configure(monkeypatch)

    def boom(url, **k):
        raise requests.ConnectionError(f"failed for {url}")
    monkeypatch.setattr(notify.requests, "post", boom)
    result = notify.send_telegram("x")

    assert result == "error:ConnectionError"
    assert TOKEN not in result

    monkeypatch.setattr(notify.requests, "post", lambda url, **k: Response(401))
    assert notify.send_telegram("x") == "error:HTTP 401"


def test_empty_text_is_not_sent(monkeypatch):
    configure(monkeypatch)
    assert notify.send_telegram("") == "nothing_to_send"

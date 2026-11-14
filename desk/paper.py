"""Paper-Broker: simulierte Käufe und Verkäufe, nie echtes Geld.

Es gibt keine Wallet, keine Signatur und keine Transaktion. Alle Werte
sind virtuell (USD). Grenzwerte kommen ausschließlich aus config.yaml
(Abschnitt paper_trading, Preisabweichung aus data_quality).

Zwei Modi:
- "after-scan": direkt nach einem Scan. Erst Ausstiege prüfen
  (einschließlich Sicherheits-Ausstieg, wenn der Token im Scan
  abgelehnt wurde), dann neue Einstiege aus bereiten Token.
- "monitor": nur Kurse der offenen Positionen aktualisieren und
  Ausstiege prüfen (keine Einstiege).

Einstiegsregel (vom Nutzer an Claude delegiert, 2026-11-10):
1. Der letzte Scan ist erfolgreich, plausibel (latest.build_latest
   "ok") und höchstens paper_trading.max_scan_age_minutes alt.
2. Der Pool hat den Status ready_for_full_risk_screen (jede
   Sicherheitsprüfung bestätigt). Eine Schätzung reicht nie.
3. Frischer Kurs aus ZWEI Quellen (GeckoTerminal und DexPaprika, gleicher
   Pool), Abweichung höchstens data_quality.max_price_deviation_pct.
   Eine Quelle allein oder Abweichung -> kein Einstieg.
4. Bestätigung in zwei Scans (Variante B aus
   docs/vorschlag_offene_entscheidungen.md): derselbe Pool (Mint- und
   Pool-Adresse) war auch im unmittelbar vorigen Scan bereit, Abstand
   confirm_min_gap_minutes bis confirm_max_gap_hours, Scan-Kurs
   dazwischen höchstens confirm_max_price_change_pct verändert,
   Liquidität höchstens confirm_max_liquidity_drop_pct gefallen.
5. Frischer Kurs weicht höchstens max_entry_price_drift_pct vom
   Scan-Kurs ab (in beide Richtungen).
6. Kein offener Bestand im selben Token, kein Ausstieg in den letzten
   reentry_cooldown_hours, weniger als max_open_positions offen.
7. Größe = kleinster Wert aus max_position_pct des Depotwerts,
   max_pool_liquidity_share_pct der kleineren bestätigten Liquidität und
   verfügbarem Geld; unter min_trade_usd -> kein Einstieg.
8. Kaufkurs = höherer frischer Kurs x (1 + Kosten). Kosten = Gebühr
   (swap_fee_pct) + geschätzter Preiseinfluss 2 x Größe / Liquidität.

Ausstiege (in dieser Reihenfolge):
- Kurs fehlt länger als max_price_missing_hours -> Abschreibung auf 0.
- Kurs <= Stop (anfänglicher Stop oder, ab Aktivierung, Trailing-Stop).
- Token im neuesten Scan abgelehnt (Sicherheits-Ausstieg).
- Haltedauer >= max_holding_hours.
Teilverkauf (2026-11-14, nach den Totalverlusten USOE/ConConAI/XTO):
erreicht der Kurs partial_take_profit_pct über dem Einstieg und greift
kein vollständiger Ausstieg, wird einmalig partial_take_profit_share_pct
der Menge verkauft (Grund partial_take_profit). Der Rest läuft mit
unveränderten Stops weiter.
Bewertet wird immer mit dem NIEDRIGEREN verfügbaren Kurs. Der
Verkaufskurs ist der beobachtete Kurs abzüglich Kosten, nie der
Stop-Kurs (Kurslücken zwischen zwei Abfragen gehen zu Lasten des Depots).

Fehlende oder widersprüchliche Daten führen nie zu einem Einstieg.
Das Depot (paper_state.json) wird atomar geschrieben; eine beschädigte
Datei stoppt den Lauf, ohne etwas zu überschreiben.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from desk.config import ConfigError, load_config
from desk.data_quality import positive_number
from desk.latest import READY, build_latest
from desk.market_data import BASE_URL as GECKOTERMINAL_URL
from desk.market_data import DEXPAPRIKA_URL
from desk.market_data import _get_token_batch as _get_with_retries


STATE_VERSION = 1
STATE_FILE = "paper_state.json"
TRADES_FILE = "paper_trades.jsonl"
EVENTS_FILE = "events.jsonl"
MAX_CLOSED_IN_STATE = 100
MAX_EQUITY_POINTS = 1000
# Höchstens ein Depotwert-Punkt je 15 Minuten (Überwachung läuft alle 5 Min.)
EQUITY_POINT_MIN_MINUTES = 15
GECKOTERMINAL_POOL_BATCH = 30
REQUEST_TIMEOUT = 15

MODES = ("after-scan", "monitor")


class PaperStateError(Exception):
    pass


# --------------------------------------------------
# Zeit
# --------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat()


def _parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment


def _hours_between(start, end):
    return (end - start).total_seconds() / 3600


# --------------------------------------------------
# Depot laden und speichern
# --------------------------------------------------

def new_state(paper_config, now):
    balance = float(paper_config["starting_balance_usd"])
    return {
        "schema_version": STATE_VERSION,
        "mode": "paper",
        "live_trading": False,
        "currency": "USD",
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "starting_balance_usd": balance,
        "cash_usd": balance,
        "open_positions": [],
        "closed_trades": [],
        "last_exit_at": {},
        "stats": {
            "trades_opened": 0,
            "trades_closed": 0,
            "wins": 0,
            "losses": 0,
            "realized_pnl_usd": 0.0,
        },
        "equity_history": [],
        "last_run": None,
    }


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_state(state):
    """Grund einer Unstimmigkeit oder None."""
    if not isinstance(state, dict):
        return "not_an_object"
    if state.get("schema_version") != STATE_VERSION:
        return "schema_version"
    if state.get("mode") != "paper" or state.get("live_trading") is not False:
        return "mode"
    for key in ("starting_balance_usd", "cash_usd"):
        if not _is_number(state.get(key)) or state[key] < 0:
            return key
    for key in ("open_positions", "closed_trades", "equity_history"):
        if not isinstance(state.get(key), list):
            return key
    if not isinstance(state.get("last_exit_at"), dict):
        return "last_exit_at"
    if not isinstance(state.get("stats"), dict):
        return "stats"
    for position in state["open_positions"]:
        if not isinstance(position, dict):
            return "position"
        for key in ("entry_price_usd", "token_amount", "cost_basis_usd",
                    "initial_stop_usd", "highest_price_usd"):
            if not _is_number(position.get(key)) or position[key] <= 0:
                return f"position.{key}"
        if _parse_time(position.get("opened_at")) is None:
            return "position.opened_at"
        if not position.get("token_address") or not position.get("pool_address"):
            return "position.address"
    return None


def load_state(path, paper_config, now):
    path = Path(path)
    if not path.exists():
        return new_state(paper_config, now)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PaperStateError(f"paper_state.json unlesbar: {type(error).__name__}")
    problem = validate_state(state)
    if problem:
        raise PaperStateError(f"paper_state.json unplausibel: {problem}")
    return state


def save_state(state, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def append_trades(path, entries):
    if not entries:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        for entry in entries:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --------------------------------------------------
# Kurse (GeckoTerminal und DexPaprika, gleicher Pool)
# --------------------------------------------------

def _error_reason(error):
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return f"{type(error).__name__}" + (f" HTTP {status}" if status else "")


def _gt_token_price(item, token_address):
    attributes = item.get("attributes") or {}
    relationships = item.get("relationships") or {}
    wanted = f"solana_{token_address}"

    def related(name):
        data = (relationships.get(name) or {}).get("data") or {}
        return data.get("id")

    if related("base_token") == wanted:
        return positive_number(attributes.get("base_token_price_usd"))
    if related("quote_token") == wanted:
        return positive_number(attributes.get("quote_token_price_usd"))
    return None


def fetch_geckoterminal_quotes(pairs, timeout=REQUEST_TIMEOUT):
    """{pool: {"price_usd", "liquidity_usd", "error"}} für alle Paare."""
    result = {}
    pools = list(dict.fromkeys(pool for _, pool in pairs))
    token_for = {pool: token for token, pool in pairs}

    for start in range(0, len(pools), GECKOTERMINAL_POOL_BATCH):
        batch = pools[start:start + GECKOTERMINAL_POOL_BATCH]
        # Begrenzte Wiederholungen wie beim Scan (429/5xx/Netzwerk
        # höchstens 3 Versuche, Wartezeit begrenzt).
        stats = {"batches_requested": 1, "request_attempts": 0, "retries": 0,
                 "last_http_status": None, "error_reason": None, "request_log": []}
        try:
            response = _get_with_retries(
                f"{GECKOTERMINAL_URL}/networks/solana/pools/multi/{','.join(batch)}",
                timeout,
                stats,
            )
            data = response.json().get("data")
            if not isinstance(data, list):
                raise ValueError("no data list")
        except (requests.RequestException, ValueError, AttributeError) as error:
            for pool in batch:
                result[pool] = {"price_usd": None, "liquidity_usd": None,
                                "error": _error_reason(error)}
            continue

        found = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            address = (item.get("attributes") or {}).get("address")
            if address in batch:
                found[address] = item

        for pool in batch:
            item = found.get(pool)
            if item is None:
                result[pool] = {"price_usd": None, "liquidity_usd": None,
                                "error": "pool_not_returned"}
                continue
            price = _gt_token_price(item, token_for[pool])
            result[pool] = {
                "price_usd": price,
                "liquidity_usd": positive_number(
                    (item.get("attributes") or {}).get("reserve_in_usd")
                ),
                "error": None if price else "token_price_missing",
            }
    return result


def fetch_dexpaprika_quotes(pairs, timeout=REQUEST_TIMEOUT):
    """{pool: {"price_usd", "liquidity_usd", "error"}}. Preis nur, wenn
    der Token der Basis-Token des Pools ist (last_price_usd gilt nur
    für diesen)."""
    headers = {}
    api_key = os.getenv("DEXPAPRIKA_API_KEY")
    if api_key:
        headers["Authorization"] = api_key

    result = {}
    for token, pool in dict.fromkeys(pairs):
        try:
            response = requests.get(
                f"{DEXPAPRIKA_URL}/networks/solana/pools/{pool}",
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("no object")
        except (requests.RequestException, ValueError) as error:
            result[pool] = {"price_usd": None, "liquidity_usd": None,
                            "error": _error_reason(error)}
            continue

        price = None
        error = "token_not_base"
        if data.get("base_token_id") == token:
            price = positive_number(data.get("last_price_usd"))
            error = None if price else "token_price_missing"
        result[pool] = {
            "price_usd": price,
            "liquidity_usd": positive_number(data.get("liquidity_usd")),
            "error": error,
        }
    return result


def fetch_quotes(pairs):
    """Rohkurse beider Quellen je Pool."""
    pairs = list(dict.fromkeys(pairs))
    if not pairs:
        return {}
    gecko = fetch_geckoterminal_quotes(pairs)
    paprika = fetch_dexpaprika_quotes(pairs)
    empty = {"price_usd": None, "liquidity_usd": None, "error": "not_requested"}
    return {
        pool: {
            "token_address": token,
            "geckoterminal": gecko.get(pool, empty),
            "dexpaprika": paprika.get(pool, empty),
        }
        for token, pool in pairs
    }


def combine_quote(raw, max_deviation_pct):
    """Kurs-Zusammenfassung. status: confirmed (zwei Quellen, Abweichung
    im Rahmen), disagree, single_source oder missing."""
    raw = raw or {}
    prices = {
        source: positive_number((raw.get(source) or {}).get("price_usd"))
        for source in ("geckoterminal", "dexpaprika")
    }
    liquidity = [
        value for value in (
            positive_number((raw.get(source) or {}).get("liquidity_usd"))
            for source in ("geckoterminal", "dexpaprika")
        ) if value
    ]
    available = [price for price in prices.values() if price]

    summary = {
        "geckoterminal_price_usd": prices["geckoterminal"],
        "dexpaprika_price_usd": prices["dexpaprika"],
        "price_low_usd": min(available) if available else None,
        "price_high_usd": max(available) if available else None,
        "deviation_pct": None,
        "liquidity_usd": min(liquidity) if liquidity else None,
        "errors": {
            source: (raw.get(source) or {}).get("error")
            for source in ("geckoterminal", "dexpaprika")
            if (raw.get(source) or {}).get("error")
        },
    }

    if not available:
        summary["status"] = "missing"
    elif len(available) == 1:
        summary["status"] = "single_source"
    else:
        deviation = (max(available) - min(available)) / min(available) * 100
        summary["deviation_pct"] = round(deviation, 4)
        summary["status"] = (
            "confirmed" if deviation <= max_deviation_pct else "disagree"
        )
    return summary


def trade_cost_pct(size_usd, liquidity_usd, fee_pct):
    """Gebühr plus geschätzter Preiseinfluss (Konstantprodukt-Pool,
    kleine Order: etwa Größe / halbe Liquidität). None ohne Liquidität."""
    liquidity = positive_number(liquidity_usd)
    if liquidity is None or size_usd < 0:
        return None
    return fee_pct + 2 * size_usd / liquidity * 100


# --------------------------------------------------
# Scan lesen
# --------------------------------------------------

def _scan_events(events_path):
    """Die letzten zwei security_scan_completed-Ereignisse (älteres
    zuerst). Unlesbare Zeilen werden übersprungen."""
    found = []
    try:
        with Path(events_path).open("r", encoding="utf-8") as file:
            for line in file:
                if '"security_scan_completed"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("event_type") == "security_scan_completed":
                    found = (found + [event])[-2:]
    except (OSError, UnicodeDecodeError):
        return []
    return found


def load_scan(events_path, scan_outcome, now, max_age_minutes):
    """{"status": "ok"|Grund, "pools": [...], "scan_at"}. Nur bei "ok"
    werden Pools zurückgegeben (sonst keine Einstiege, kein
    Sicherheits-Ausstieg)."""
    latest = build_latest(events_path, scan_outcome)
    if latest["scan_status"] != "ok":
        return {"status": f"scan_{latest['scan_status']}", "pools": [], "scan_at": None}

    scan_at = _parse_time(latest["scan_at"])
    if scan_at is None:
        return {"status": "scan_time_invalid", "pools": [], "scan_at": None}
    if _hours_between(scan_at, now) * 60 > max_age_minutes or scan_at > now + timedelta(minutes=5):
        return {"status": "scan_too_old", "pools": [], "scan_at": latest["scan_at"]}

    events = _scan_events(events_path)
    pools = events[-1]["details"]["checked_pools"]
    previous = {"pools": [], "scan_at": None}
    if len(events) == 2:
        details = events[0].get("details")
        checked = details.get("checked_pools") if isinstance(details, dict) else None
        previous = {
            "pools": [p for p in checked if isinstance(p, dict)] if isinstance(checked, list) else [],
            "scan_at": events[0].get("timestamp"),
        }
    return {"status": "ok", "pools": pools, "scan_at": latest["scan_at"],
            "previous": previous}


def _entry_inputs(pool):
    """Scan-Kurs und kleinere bestätigte Liquidität des Pools."""
    market_cap = pool.get("market_cap") or {}
    liquidity = pool.get("liquidity_check") or {}
    values = [
        positive_number(liquidity.get("dexpaprika_usd")),
        positive_number(liquidity.get("rugcheck_usd")),
    ]
    return {
        "scan_price_usd": positive_number(market_cap.get("price_used_usd")),
        "scan_liquidity_usd": min(values) if all(values) else None,
    }


# --------------------------------------------------
# Depot-Logik
# --------------------------------------------------

def _position_value(position):
    price = position.get("last_price_usd") or position["entry_price_usd"]
    if position.get("written_off"):
        return 0.0
    return position["token_amount"] * price


def equity_usd(state):
    return state["cash_usd"] + sum(_position_value(p) for p in state["open_positions"])


def stop_levels(position, paper):
    """Alle Stop-Stufen einer Position (nur steigend, nie zurück).

    - initial: Einstieg − initial_stop_loss_pct
    - profit_lock (Gewinnsicherung in Stufen): sobald der Höchstkurs
      profit_lock_step_pct über dem Einstieg lag, mindestens Einstand
      nach geschätzten Verkaufskosten; jede weitere volle Stufe hebt den
      Stop um eine Stufe (Höchstkurs +12 % → Stop +6 %, +18 % → +12 % …).
      Gilt nur, solange die Stufe unter dem Höchstkurs liegt.
    - trailing: ab trailing_activation_profit_pct Höchstkurs − trailing_distance_pct
    """
    entry = position["entry_price_usd"]
    highest = position["highest_price_usd"]
    levels = {"initial": position["initial_stop_usd"]}

    step = paper["profit_lock_step_pct"]
    steps = int(round((highest / entry - 1) * 100, 9) // step) if highest > entry else 0
    if steps >= 1:
        # Verkaufskosten ≈ Kaufkosten (gleiche Größe, Gebühr + Preiseinfluss);
        # fehlt der Wert, konservativ Gebühr + 2 × höchster Liquiditätsanteil
        exit_cost = position.get("entry_cost_pct")
        if exit_cost is None:
            exit_cost = paper["swap_fee_pct"] + 2 * paper["max_pool_liquidity_share_pct"]
        breakeven = entry / (1 - exit_cost / 100)
        lock = max(breakeven, entry * (1 + (steps - 1) * step / 100))
        if lock < highest:
            levels["profit_lock"] = lock

    activation = entry * (1 + paper["trailing_activation_profit_pct"] / 100)
    if highest >= activation:
        levels["trailing"] = highest * (1 - paper["trailing_distance_pct"] / 100)
    return levels


def current_stop(position, paper):
    return max(stop_levels(position, paper).values())


STOP_EXIT_REASON = {
    "initial": "stop_loss",
    "profit_lock": "profit_lock",
    "trailing": "trailing_stop",
}


def _close(state, position, now, reason, mark_price, liquidity_usd, paper,
           fraction=1.0):
    """Verkauft die Position ganz (fraction 1) oder einen Teil davon."""
    partial = fraction < 1
    amount = position["token_amount"] * fraction
    cost_basis = position["cost_basis_usd"] * fraction
    if mark_price is None:
        exit_price = 0.0
        cost_pct = None
    else:
        size = amount * mark_price
        cost_pct = trade_cost_pct(
            size,
            liquidity_usd or position.get("entry_liquidity_usd"),
            paper["swap_fee_pct"],
        )
        if cost_pct is None:
            cost_pct = 100.0
        exit_price = max(mark_price * (1 - min(cost_pct, 100) / 100), 0.0)

    proceeds = amount * exit_price
    pnl = proceeds - cost_basis
    state["cash_usd"] = round(state["cash_usd"] + proceeds, 8)

    stats = state["stats"]
    stats["realized_pnl_usd"] = round(stats["realized_pnl_usd"] + pnl, 8)
    if partial:
        # Teilverkauf: Position bleibt offen, zählt nicht als abgeschlossener
        # Trade (Gewinn/Verlust wird beim Schluss des Rests gezählt)
        position["token_amount"] -= amount
        position["cost_basis_usd"] = round(position["cost_basis_usd"] - cost_basis, 8)
        position["partial_taken"] = True
        stats["partial_sales"] = stats.get("partial_sales", 0) + 1
    else:
        state["open_positions"].remove(position)
        state["last_exit_at"][position["token_address"]] = _iso(now)
        stats["trades_closed"] += 1
        stats["wins" if pnl > 0 else "losses"] += 1

    trade = {
        "token_symbol": position.get("token_symbol"),
        "token_address": position["token_address"],
        "pool_address": position["pool_address"],
        "opened_at": position["opened_at"],
        "closed_at": _iso(now),
        "holding_hours": round(_hours_between(_parse_time(position["opened_at"]), now), 3),
        "entry_price_usd": position["entry_price_usd"],
        "exit_mark_price_usd": mark_price,
        "exit_price_usd": exit_price,
        "exit_cost_pct": None if cost_pct is None else round(cost_pct, 4),
        "token_amount": amount,
        "cost_basis_usd": round(cost_basis, 8),
        "proceeds_usd": round(proceeds, 8),
        "pnl_usd": round(pnl, 8),
        "pnl_pct": round(pnl / cost_basis * 100, 4),
        "exit_reason": reason,
        "highest_price_usd": position["highest_price_usd"],
        "partial": partial,
    }
    state["closed_trades"].append(trade)
    state["closed_trades"] = state["closed_trades"][-MAX_CLOSED_IN_STATE:]
    event = "paper_position_partial_sale" if partial else "paper_position_closed"
    return {"event": event, "at": _iso(now), **trade}


def process_exits(state, quotes, now, paper, max_dev, rejected_tokens, manual_exit_tokens=()):
    events = []
    for position in list(state["open_positions"]):
        quote = combine_quote(quotes.get(position["pool_address"]), max_dev)
        position["last_quote"] = {
            "at": _iso(now),
            "status": quote["status"],
            "price_low_usd": quote["price_low_usd"],
            "price_high_usd": quote["price_high_usd"],
            "deviation_pct": quote["deviation_pct"],
            "errors": quote["errors"],
        }
        mark = quote["price_low_usd"]

        if mark is None:
            since = _parse_time(position.get("price_missing_since")) or now
            position["price_missing_since"] = _iso(since)
            if _hours_between(since, now) >= paper["max_price_missing_hours"]:
                position["written_off"] = True
                events.append(_close(state, position, now, "price_unavailable_writeoff",
                                     None, None, paper))
            continue

        position.pop("price_missing_since", None)
        position["last_price_usd"] = mark
        position["last_price_at"] = _iso(now)
        if quote["liquidity_usd"]:
            position["last_liquidity_usd"] = quote["liquidity_usd"]
        position["highest_price_usd"] = max(position["highest_price_usd"], mark)
        levels = stop_levels(position, paper)
        stop_kind = max(levels, key=levels.get)
        stop = levels[stop_kind]
        position["current_stop_usd"] = stop
        position["current_stop_kind"] = stop_kind
        liquidity = quote["liquidity_usd"] or position.get("last_liquidity_usd")

        reason = None
        if mark <= stop:
            reason = STOP_EXIT_REASON[stop_kind]
        elif position["token_address"] in manual_exit_tokens:
            reason = "manual_exit"
        elif position["token_address"] in rejected_tokens:
            reason = "security_rejected"
        elif _hours_between(_parse_time(position["opened_at"]), now) >= paper["max_holding_hours"]:
            reason = "max_holding_time"

        if reason:
            events.append(_close(state, position, now, reason, mark, liquidity, paper))
        elif (not position.get("partial_taken")
              and mark >= position["entry_price_usd"]
              * (1 + paper["partial_take_profit_pct"] / 100)):
            events.append(_close(state, position, now, "partial_take_profit", mark,
                                 liquidity, paper,
                                 fraction=paper["partial_take_profit_share_pct"] / 100))
    return events


def confirmation_problem(pool, scan, paper):
    """Grund, warum der vorige Scan den Einstieg nicht bestätigt, oder None."""
    previous = scan.get("previous") or {}
    match = [
        p for p in previous.get("pools", [])
        if p.get("token_address") == pool.get("token_address")
        and p.get("pool_address") == pool.get("pool_address")
    ]
    if not match or match[0].get("status") != READY or match[0].get("rejection_reasons") \
            or match[0].get("missing_checks"):
        return "not_ready_in_previous_scan", {}

    current_at = _parse_time(scan.get("scan_at"))
    previous_at = _parse_time(previous.get("scan_at"))
    if current_at is None or previous_at is None:
        return "previous_scan_time_invalid", {}
    gap_minutes = (current_at - previous_at).total_seconds() / 60
    details = {"previous_scan_gap_minutes": round(gap_minutes, 1)}
    if gap_minutes < paper["confirm_min_gap_minutes"] or gap_minutes > paper["confirm_max_gap_hours"] * 60:
        return "previous_scan_gap_out_of_range", details

    now_inputs = _entry_inputs(pool)
    before = _entry_inputs(match[0])
    if before["scan_price_usd"] is None or before["scan_liquidity_usd"] is None:
        return "previous_scan_values_missing", details
    change = abs(now_inputs["scan_price_usd"] - before["scan_price_usd"]) / before["scan_price_usd"] * 100
    drop = (before["scan_liquidity_usd"] - now_inputs["scan_liquidity_usd"]) / before["scan_liquidity_usd"] * 100
    details.update(price_change_between_scans_pct=round(change, 4),
                   liquidity_drop_between_scans_pct=round(drop, 4))
    if change > paper["confirm_max_price_change_pct"]:
        return "price_changed_between_scans", details
    if drop > paper["confirm_max_liquidity_drop_pct"]:
        return "liquidity_dropped_between_scans", details
    return None, details


def entry_decision(pool, quote, state, now, paper, max_dev, scan=None):
    """(Entscheidung, Details). Entscheidung "open" oder ein Grund."""
    token = pool.get("token_address")
    pool_address = pool.get("pool_address")
    if pool.get("status") != READY or pool.get("rejection_reasons") or pool.get("missing_checks"):
        return "not_ready", {}
    if not token or not pool_address:
        return "address_missing", {}
    if token in paper["manual_exit_tokens"]:
        return "manual_exit_blocked", {}
    if any(p["token_address"] == token for p in state["open_positions"]):
        return "already_open", {}
    if len(state["open_positions"]) >= paper["max_open_positions"]:
        return "max_open_positions", {}
    last_exit = _parse_time(state["last_exit_at"].get(token))
    if last_exit and _hours_between(last_exit, now) < paper["reentry_cooldown_hours"]:
        return "reentry_cooldown", {}

    inputs = _entry_inputs(pool)
    if inputs["scan_price_usd"] is None:
        return "scan_price_missing", inputs
    if inputs["scan_liquidity_usd"] is None:
        return "scan_liquidity_missing", inputs

    problem, confirmation = confirmation_problem(pool, scan or {}, paper)
    inputs.update(confirmation)
    if problem:
        return problem, inputs

    combined = combine_quote(quote, max_dev)
    details = {**inputs, "quote": combined}
    if combined["status"] != "confirmed":
        return f"fresh_price_{combined['status']}", details

    fresh = combined["price_high_usd"]
    drift = abs(fresh - inputs["scan_price_usd"]) / inputs["scan_price_usd"] * 100
    details["price_drift_pct"] = round(drift, 4)
    if drift > paper["max_entry_price_drift_pct"]:
        return "price_moved_since_scan", details

    liquidity = min(
        value for value in (inputs["scan_liquidity_usd"], combined["liquidity_usd"]) if value
    )
    size = min(
        equity_usd(state) * paper["max_position_pct"] / 100,
        liquidity * paper["max_pool_liquidity_share_pct"] / 100,
        state["cash_usd"],
    )
    details["liquidity_usd"] = liquidity
    details["size_usd"] = round(size, 8)
    if size < paper["min_trade_usd"]:
        return "size_below_minimum", details

    cost_pct = trade_cost_pct(size, liquidity, paper["swap_fee_pct"])
    entry_price = fresh * (1 + cost_pct / 100)
    details["cost_pct"] = round(cost_pct, 4)
    details["entry_price_usd"] = entry_price
    return "open", details


def open_position(state, pool, details, now, paper):
    size = details["size_usd"]
    entry = details["entry_price_usd"]
    position = {
        "token_symbol": pool.get("token_symbol"),
        "token_name": pool.get("token_name"),
        "token_address": pool["token_address"],
        "pool_address": pool["pool_address"],
        "dex_name": pool.get("dex_name"),
        "opened_at": _iso(now),
        "entry_price_usd": entry,
        "entry_mark_price_usd": details["quote"]["price_high_usd"],
        "entry_cost_pct": details["cost_pct"],
        "entry_liquidity_usd": details["liquidity_usd"],
        "token_amount": size / entry,
        "cost_basis_usd": round(size, 8),
        "initial_stop_usd": entry * (1 - paper["initial_stop_loss_pct"] / 100),
        "highest_price_usd": entry,
        "last_price_usd": details["quote"]["price_low_usd"],
        "last_price_at": _iso(now),
        "current_stop_usd": entry * (1 - paper["initial_stop_loss_pct"] / 100),
    }
    state["cash_usd"] = round(state["cash_usd"] - size, 8)
    state["open_positions"].append(position)
    state["stats"]["trades_opened"] += 1
    return {
        "event": "paper_position_opened",
        "at": _iso(now),
        **{k: position[k] for k in (
            "token_symbol", "token_address", "pool_address", "dex_name",
            "entry_price_usd", "entry_mark_price_usd", "entry_cost_pct",
            "token_amount", "cost_basis_usd", "initial_stop_usd",
        )},
        "scan_price_usd": details["scan_price_usd"],
        "price_drift_pct": details["price_drift_pct"],
        "quote_deviation_pct": details["quote"]["deviation_pct"],
    }


def run_paper(mode, data_dir, config, now=None, quote_fetcher=fetch_quotes,
              scan_outcome=None):
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    now = now or _now()
    data_dir = Path(data_dir)
    paper = config["paper_trading"]
    max_dev = config["data_quality"]["max_price_deviation_pct"]

    state = load_state(data_dir / STATE_FILE, paper, now)

    # Überwachung ohne offene Positionen: nichts abfragen, nichts schreiben
    # (vermeidet leere Commits alle 5 Minuten).
    if mode == "monitor" and not state["open_positions"]:
        return {"state": state, "events": [], "written": False}

    scan = {"status": "not_requested", "pools": [], "scan_at": None}
    if mode == "after-scan":
        scan = load_scan(data_dir / EVENTS_FILE, scan_outcome, now,
                         paper["max_scan_age_minutes"])

    rejected = {
        pool.get("token_address") for pool in scan["pools"]
        if pool.get("status") == "rejected" and pool.get("token_address")
    }
    candidates = [pool for pool in scan["pools"] if pool.get("status") == READY]

    pairs = [(p["token_address"], p["pool_address"]) for p in state["open_positions"]]
    pairs += [
        (p["token_address"], p["pool_address"]) for p in candidates
        if p.get("token_address") and p.get("pool_address")
    ]
    quotes = quote_fetcher(pairs) if pairs else {}

    events = process_exits(state, quotes, now, paper, max_dev, rejected,
                           set(paper["manual_exit_tokens"]))

    decisions = []
    for pool in candidates:
        decision, details = entry_decision(
            pool, quotes.get(pool.get("pool_address")), state, now, paper, max_dev, scan
        )
        decisions.append({
            "token_symbol": pool.get("token_symbol"),
            "token_address": pool.get("token_address"),
            "decision": decision,
        })
        if decision == "open":
            events.append(open_position(state, pool, details, now, paper))
        else:
            events.append({
                "event": "paper_entry_skipped",
                "at": _iso(now),
                "token_symbol": pool.get("token_symbol"),
                "token_address": pool.get("token_address"),
                "reason": decision,
                "details": details,
            })

    equity = round(equity_usd(state), 8)
    point = {"at": _iso(now), "equity_usd": equity}
    history = state["equity_history"]
    last_at = _parse_time(history[-1].get("at")) if history else None
    # Je 15-Minuten-Fenster ein Punkt: im selben Fenster wird der letzte ersetzt
    slot = EQUITY_POINT_MIN_MINUTES * 60
    if last_at and int(last_at.timestamp() // slot) == int(now.timestamp() // slot):
        history[-1] = point
    else:
        history.append(point)
    state["equity_history"] = state["equity_history"][-MAX_EQUITY_POINTS:]
    state["updated_at"] = _iso(now)
    state["last_run"] = {
        "at": _iso(now),
        "mode": mode,
        "scan_status": scan["status"],
        "scan_at": scan["scan_at"],
        "entry_decisions": decisions,
        "open_positions": len(state["open_positions"]),
        "equity_usd": equity,
        "run_number": os.getenv("GITHUB_RUN_NUMBER"),
    }

    save_state(state, data_dir / STATE_FILE)
    append_trades(data_dir / TRADES_FILE, events)
    return {"state": state, "events": events, "written": True}


# --------------------------------------------------
# Kommandozeile
# --------------------------------------------------

def _format_event(event):
    symbol = event.get("token_symbol") or event.get("token_address", "?")[:6]
    if event["event"] == "paper_position_opened":
        return (f"PAPER KAUF {symbol}: {event['cost_basis_usd']:.2f} $ zu "
                f"{event['entry_price_usd']:.8g} $, Stop {event['initial_stop_usd']:.8g} $")
    if event["event"] == "paper_position_partial_sale":
        return (f"PAPER TEILVERKAUF {symbol}: {event['pnl_usd']:+.2f} $ "
                f"({event['pnl_pct']:+.1f} %)")
    if event["event"] == "paper_position_closed":
        return (f"PAPER VERKAUF {symbol} ({event['exit_reason']}): "
                f"{event['pnl_usd']:+.2f} $ ({event['pnl_pct']:+.1f} %)")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paper-Broker (nur Simulation)")
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--scan-outcome", default=None)
    args = parser.parse_args(argv)

    try:
        config = load_config()
        result = run_paper(args.mode, args.data_dir, config,
                           scan_outcome=args.scan_outcome)
    except (ConfigError, PaperStateError) as error:
        print("Paper-Broker gestoppt:", error)
        return 1

    state = result["state"]
    if not result["written"]:
        print("PAPER-BROKER: keine offene Position, nichts zu prüfen.")
        return 0
    messages = [m for m in (_format_event(e) for e in result["events"]) if m]
    print("PAPER-BROKER (nur Simulation, kein echtes Geld)")
    print("Modus:", args.mode, "| Scan:", state["last_run"]["scan_status"])
    print("Depotwert:", f"{state['last_run']['equity_usd']:.2f} $",
          "| Bargeld:", f"{state['cash_usd']:.2f} $",
          "| Offen:", len(state["open_positions"]))
    for message in messages:
        print(message)

    from desk.notify import send_telegram
    if messages:
        print("Telegram:", send_telegram("\n".join(messages)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

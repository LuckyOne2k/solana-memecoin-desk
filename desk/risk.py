"""Rechenbausteine für Positionsgröße und Stops.

Status: noch nicht angeschlossen. Wird vom späteren Paper-Broker
(Phase 2) genutzt. Alle Prozentwerte kommen aus config.yaml
(Abschnitt paper_trading) und müssen ausdrücklich übergeben werden;
es gibt bewusst keine fest eingebauten Standardwerte.
"""


def _check_pct(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")

    if value < 0:
        raise ValueError(f"{name} must not be negative")


def position_size_usd(
    *,
    portfolio_usd: float,
    requested_usd: float,
    max_position_pct: float,
) -> float:
    _check_pct("max_position_pct", max_position_pct)

    if max_position_pct > 100:
        raise ValueError("max_position_pct must not exceed 100")

    if portfolio_usd <= 0 or requested_usd <= 0:
        return 0.0

    maximum = portfolio_usd * (max_position_pct / 100)
    return min(requested_usd, maximum)


def initial_stop_price(
    *,
    entry_price: float,
    stop_loss_pct: float,
) -> float:
    _check_pct("stop_loss_pct", stop_loss_pct)

    if stop_loss_pct >= 100:
        raise ValueError("stop_loss_pct must be below 100")

    if entry_price <= 0:
        raise ValueError("Entry price must be positive")

    return entry_price * (1 - stop_loss_pct / 100)


def trailing_stop_price(
    *,
    highest_price: float,
    entry_price: float,
    trailing_pct: float,
    activation_gain_pct: float,
) -> float | None:
    """Trailing-Stop-Preis oder None, solange er nicht aktiv ist."""
    _check_pct("trailing_pct", trailing_pct)
    _check_pct("activation_gain_pct", activation_gain_pct)

    if trailing_pct >= 100:
        raise ValueError("trailing_pct must be below 100")

    if highest_price <= 0 or entry_price <= 0:
        return None

    activation_price = entry_price * (1 + activation_gain_pct / 100)

    if highest_price < activation_price:
        return None

    return highest_price * (1 - trailing_pct / 100)


def stops_from_config(config: dict) -> dict:
    """Liest die Stop-Parameter aus der geladenen Konfiguration."""
    paper = config["paper_trading"]

    return {
        "stop_loss_pct": paper["initial_stop_loss_pct"],
        "trailing_pct": paper["trailing_distance_pct"],
        "activation_gain_pct": paper["trailing_activation_profit_pct"],
        "max_position_pct": paper["max_position_pct"],
    }

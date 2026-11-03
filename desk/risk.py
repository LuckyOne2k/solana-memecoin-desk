def position_size_usd(
    portfolio_usd: float,
    requested_usd: float,
    max_position_pct: float = 8.0
) -> float:
    if portfolio_usd <= 0 or requested_usd <= 0:
        return 0.0

    maximum = portfolio_usd * (max_position_pct / 100)
    return min(requested_usd, maximum)


def initial_stop_price(entry_price: float, stop_loss_pct: float = 6.0) -> float:
    if entry_price <= 0:
        raise ValueError("Entry price must be positive")

    return entry_price * (1 - stop_loss_pct / 100)


def trailing_stop_price(
    highest_price: float,
    trailing_pct: float = 6.0,
    activation_gain_pct: float = 12.0,
    entry_price: float = 0.0
) -> float | None:
    if highest_price <= 0 or entry_price <= 0:
        return None

    activation_price = entry_price * (1 + activation_gain_pct / 100)

    if highest_price < activation_price:
        return None

    return highest_price * (1 - trailing_pct / 100)

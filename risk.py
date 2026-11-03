def position_size_usd(portfolio_value_usd: float, requested_pct: float, max_pct: float = 8) -> float:
    pct = min(max(requested_pct, 0), max_pct)
    return portfolio_value_usd * pct / 100

def stop_levels(entry_price: float, initial_stop_pct: float = 6, trailing_activation_pct: float = 12,
                trailing_distance_pct: float = 6) -> dict:
    return {
        "initial_stop": entry_price * (1 - initial_stop_pct / 100),
        "trailing_activation_price": entry_price * (1 + trailing_activation_pct / 100),
        "trailing_distance_pct": trailing_distance_pct,
    }

def update_trailing_stop(entry_price: float, highest_price: float, initial_stop_pct: float = 6,
                         activation_pct: float = 12, distance_pct: float = 6) -> float:
    initial = entry_price * (1 - initial_stop_pct / 100)
    if highest_price < entry_price * (1 + activation_pct / 100):
        return initial
    return max(initial, highest_price * (1 - distance_pct / 100))

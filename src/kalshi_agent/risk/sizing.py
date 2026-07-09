import math

from kalshi_agent.risk.fees import maker_fee, taker_fee


def net_edge_per_contract(
    fair_value: float,
    market_price: float,
    *,
    is_taker: bool,
    half_spread: float = 0.0,
    slippage: float = 0.0,
) -> float:
    """Build spec §5.1: net_edge = fair_value_edge - fee - half_spread - slippage.
    Positive means the contract is underpriced relative to fair_value (buy signal);
    caller is responsible for deciding direction (buy YES vs buy NO) beforehand."""
    gross_edge = fair_value - market_price
    fee = (taker_fee if is_taker else maker_fee)(market_price, 1)
    return gross_edge - fee - half_spread - slippage


def kelly_wager_fraction(fair_value: float, price: float) -> float:
    """Full-Kelly fraction of bankroll for a binary contract: buy 1 contract for
    `price`, it pays $1 if the true probability is `fair_value`. Derived from the
    standard Kelly formula f* = (bp - q)/b with b = (1-price)/price:
    f* = (fair_value - price) / (1 - price). Negative/zero when there's no edge."""
    if not 0 < price < 1:
        raise ValueError(f"price must be in (0, 1), got {price}")
    edge = fair_value - price
    if edge <= 0:
        return 0.0
    return edge / (1 - price)


def kelly_contracts(
    fair_value: float,
    price: float,
    *,
    bankroll: float,
    kelly_fraction: float,
    max_fraction_of_bankroll: float | None = None,
) -> int:
    """Fractional-Kelly position size, in whole contracts. `kelly_fraction`
    scales down full Kelly (build spec §5.3: "never full Kelly — calibration
    error makes it ruinous"). `max_fraction_of_bankroll` is an additional hard
    cap independent of the risk guardrails' own per-market cap."""
    wager_fraction = kelly_wager_fraction(fair_value, price) * kelly_fraction
    if max_fraction_of_bankroll is not None:
        wager_fraction = min(wager_fraction, max_fraction_of_bankroll)
    if wager_fraction <= 0 or bankroll <= 0:
        return 0
    dollars = wager_fraction * bankroll
    # Epsilon guard: floating-point noise can land just under a whole-contract
    # boundary (e.g. 49.999999999999996 instead of 50) and silently under-size
    # by one contract — hit this exact case in testing.
    return max(0, math.floor(dollars / price + 1e-9))

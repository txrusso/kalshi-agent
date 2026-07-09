from decimal import ROUND_CEILING, Decimal

# Kalshi fee schedule effective 2026-07-07 (verified against two independent
# sources 2026-07-08 — docs.kalshi.com/fee-schedule was rate-limited during
# verification, re-confirm against the live PDF before relying on this for
# real capital): taker fee = round_up($0.07 * C * P * (1-P)) per contract,
# maker fee = 25% of the taker fee. Sources disagreed on whether maker fees
# apply uniformly or only to a subset of series Kalshi flags as maker-fee-
# enabled (most series may in practice charge $0 maker fee) — using the
# uniform formula here is the conservative assumption: it won't overstate
# Maker-strategy (S1) profitability, matching the low-to-medium risk mandate.
#
# Decimal (not float) throughout: at prices like 0.30/0.70 the true fee lands
# exactly on a cent boundary, and float rounding noise pushed one side up and
# the other down in testing — an asymmetry that has no business existing in
# a symmetric fee formula and would silently bias net-edge calculations.
TAKER_FEE_RATE = Decimal("0.07")
MAKER_FEE_RATE = Decimal("0.25") * TAKER_FEE_RATE
CENT = Decimal("0.01")


def _round_up_to_cent(dollars: Decimal) -> float:
    return float(dollars.quantize(CENT, rounding=ROUND_CEILING))


def taker_fee(price: float, contracts: int) -> float:
    """`price` is the contract price in dollars, in (0, 1)."""
    if not 0 < price < 1:
        raise ValueError(f"price must be in (0, 1), got {price}")
    p = Decimal(str(price))
    per_contract = TAKER_FEE_RATE * p * (1 - p)
    return _round_up_to_cent(per_contract * contracts)


def maker_fee(price: float, contracts: int) -> float:
    if not 0 < price < 1:
        raise ValueError(f"price must be in (0, 1), got {price}")
    p = Decimal(str(price))
    per_contract = MAKER_FEE_RATE * p * (1 - p)
    return _round_up_to_cent(per_contract * contracts)

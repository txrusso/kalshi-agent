from dataclasses import dataclass


@dataclass
class CalibrationBucket:
    price_low: float
    price_high: float
    n: int = 0
    n_yes: int = 0

    @property
    def realized_frequency(self) -> float:
        return self.n_yes / self.n if self.n else 0.0

    @property
    def midpoint_price(self) -> float:
        return (self.price_low + self.price_high) / 2


def build_calibration_curve(observations: list[tuple[float, bool]], *, bucket_width: float = 0.05) -> list[CalibrationBucket]:
    """Build spec S2 / ranked-idea #5: fit a realized-frequency-vs-price curve
    from resolved markets. `observations` are (price_at_resolution, resolved_yes)
    pairs — the debiasing signal is the gap between a bucket's realized_frequency
    and its midpoint price (e.g. sub-15c contracts winning far less than 15% of
    the time is the favorite-longshot bias Bürgi–Deng–Whelan measured on Kalshi)."""
    n_buckets = max(1, round(1.0 / bucket_width))
    curve = [CalibrationBucket(price_low=i * bucket_width, price_high=(i + 1) * bucket_width) for i in range(n_buckets)]

    for price, resolved_yes in observations:
        # Strict (0, 1): a settled market can carry a boundary last_price of
        # exactly 0.0 or 1.0 (e.g. one that never really traded) — not a real
        # tradable price, and outside the fee model's valid range. Hit this
        # for real against production data 2026-07-09.
        if not 0 < price < 1:
            continue
        idx = min(int(price / bucket_width), n_buckets - 1)
        curve[idx].n += 1
        if resolved_yes:
            curve[idx].n_yes += 1

    return curve


def bucket_for_price(price: float, curve: list[CalibrationBucket]) -> CalibrationBucket | None:
    for bucket in curve:
        if bucket.price_low <= price < bucket.price_high:
            return bucket
    if curve and price >= curve[-1].price_high:
        return curve[-1]
    return None

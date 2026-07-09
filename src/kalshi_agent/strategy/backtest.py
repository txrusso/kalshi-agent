import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from kalshi_agent.data.models import Market, PriceSnapshot
from kalshi_agent.risk.fees import maker_fee, taker_fee
from kalshi_agent.risk.sizing import net_edge_per_contract
from kalshi_agent.strategy.calibration import build_calibration_curve
from kalshi_agent.strategy.signals import FavoriteLongshotSignal


@dataclass
class Observation:
    ticker: str
    event_ticker: str
    price: float
    resolved_yes: bool
    close_time: dt.datetime | None


def load_observations_with_time(session: Session) -> list[Observation]:
    resolved = session.execute(
        select(Market.ticker, Market.event_ticker, Market.result, Market.close_time).where(
            Market.result.in_(("yes", "no"))
        )
    ).all()
    if not resolved:
        return []

    tickers = [r[0] for r in resolved]
    rows = session.execute(
        select(PriceSnapshot.ticker, PriceSnapshot.last_price, PriceSnapshot.ts)
        .where(PriceSnapshot.ticker.in_(tickers))
        .order_by(PriceSnapshot.ticker, PriceSnapshot.ts.desc())
    ).all()

    latest_price: dict[str, float] = {}
    for ticker, last_price, _ts in rows:
        if ticker not in latest_price and last_price is not None:
            latest_price[ticker] = last_price

    observations: list[Observation] = []
    for ticker, event_ticker, result, close_time in resolved:
        price = latest_price.get(ticker)
        if price is None:
            continue
        observations.append(
            Observation(ticker=ticker, event_ticker=event_ticker, price=price, resolved_yes=result == "yes", close_time=close_time)
        )
    return observations


def deduplicate_by_event(observations: list[Observation]) -> list[Observation]:
    """Many Kalshi markets are different price-threshold variants of the same
    underlying event (e.g. a dozen "BTC above $X" markets for the same hour,
    or many House-race-margin buckets for one race) — they share an
    event_ticker and resolve together off one real-world draw, not
    independently. Treating each as its own Bernoulli trial let 12+ correlated
    BTC-threshold markets masquerade as 12+ independent observations, wrecking
    both the calibration curve (spurious bucket frequencies from one lucky/
    unlucky price path) and the backtest's apparent sample size. Hit this for
    real on production data 2026-07-09: 15/15 "trades" were overwhelmingly
    KXBTCD variants of a handful of actual BTC moves, and went 0-15.

    Keeps one observation per event_ticker — the one with price closest to
    0.50, since that's the most information-rich/least-extreme representative
    of the event and avoids a bias toward whichever threshold happened to
    price nearest 0 or 1."""
    best_by_event: dict[str, Observation] = {}
    for obs in observations:
        current = best_by_event.get(obs.event_ticker)
        if current is None or abs(obs.price - 0.5) < abs(current.price - 0.5):
            best_by_event[obs.event_ticker] = obs
    return list(best_by_event.values())


@dataclass
class BacktestResult:
    n_trades: int
    n_wins: int
    gross_pnl: float
    total_fees: float
    net_pnl: float
    hit_rate: float | None

    @property
    def net_pnl_per_trade(self) -> float | None:
        return self.net_pnl / self.n_trades if self.n_trades else None


def run_favorite_longshot_backtest(
    observations: list[Observation],
    *,
    train_fraction: float = 0.6,
    bucket_width: float = 0.05,
    min_samples: int = 20,
    min_net_edge: float = 0.04,
    is_taker: bool = False,
) -> BacktestResult:
    """Build spec §7: replay strategy -> decision -> fee -> sizing on stored
    data. Train/test split is chronological by close_time — building the
    calibration curve and evaluating it on the *same* data would trivially
    show an edge, since a bucket's realized_frequency is definitionally that
    data's own outcome rate. Only observations with a known close_time
    participate (unordered ones can't be placed in a chronological split).

    Deduplicates to one observation per event_ticker first (see
    deduplicate_by_event) — without this, correlated same-event price-
    threshold variants inflate apparent sample size and can dominate both the
    calibration curve and the backtest's trade set with what is really a
    single underlying draw repeated many times."""
    deduped = deduplicate_by_event(observations)
    timed = sorted((o for o in deduped if o.close_time is not None), key=lambda o: o.close_time)
    split_idx = int(len(timed) * train_fraction)
    train, test = timed[:split_idx], timed[split_idx:]

    curve = build_calibration_curve([(o.price, o.resolved_yes) for o in train], bucket_width=bucket_width)
    signal = FavoriteLongshotSignal(curve, min_samples=min_samples)

    n_trades = 0
    n_wins = 0
    gross_pnl = 0.0
    total_fees = 0.0

    for obs in test:
        if not 0 < obs.price < 1:
            # Boundary last_price (market never really traded) — not a real
            # tradable price, and outside the fee model's valid range.
            continue

        result = signal.evaluate(obs.price)
        if result.fair_value is None:
            continue

        # Try both sides (buy YES vs. buy NO); trade whichever clears the net-
        # edge gate, preferring the larger edge if both would.
        yes_edge = net_edge_per_contract(result.fair_value, obs.price, is_taker=is_taker)
        no_edge = net_edge_per_contract(1 - result.fair_value, 1 - obs.price, is_taker=is_taker)

        if yes_edge > min_net_edge and yes_edge >= no_edge:
            side_is_yes, entry_price = True, obs.price
        elif no_edge > min_net_edge:
            side_is_yes, entry_price = False, 1 - obs.price
        else:
            continue

        won = obs.resolved_yes if side_is_yes else not obs.resolved_yes
        fee = (taker_fee if is_taker else maker_fee)(entry_price, 1)
        payout = 1.0 if won else 0.0

        n_trades += 1
        n_wins += 1 if won else 0
        gross_pnl += payout - entry_price
        total_fees += fee

    return BacktestResult(
        n_trades=n_trades,
        n_wins=n_wins,
        gross_pnl=gross_pnl,
        total_fees=total_fees,
        net_pnl=gross_pnl - total_fees,
        hit_rate=(n_wins / n_trades) if n_trades else None,
    )

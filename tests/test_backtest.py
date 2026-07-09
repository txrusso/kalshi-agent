import datetime as dt
import random

from kalshi_agent.strategy.backtest import Observation, deduplicate_by_event, run_favorite_longshot_backtest


def _synthetic_observations(*, n: int, price: float, true_win_rate: float, seed: int, start: dt.datetime) -> list[Observation]:
    # event_ticker == ticker: each synthetic observation models its own
    # independent event, not a correlated price-threshold variant of a
    # shared one — see deduplicate_by_event for why that distinction matters.
    rng = random.Random(seed)
    obs = []
    for i in range(n):
        won = rng.random() < true_win_rate
        ticker = f"T-{price}-{i}"
        obs.append(
            Observation(
                ticker=ticker,
                event_ticker=ticker,
                price=price,
                resolved_yes=won,
                close_time=start + dt.timedelta(minutes=i),
            )
        )
    return obs


def _biased_market_dataset(n_per_bucket: int = 200, seed: int = 42) -> list[Observation]:
    """Mirrors the documented favorite-longshot bias: cheap longshots (10c)
    win far less than 10% of the time; heavy favorites (90c) win slightly
    more than 90% of the time. Same underlying distribution across the whole
    timeline, so a proper out-of-sample split should still find the edge."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    longshots = _synthetic_observations(n=n_per_bucket, price=0.10, true_win_rate=0.03, seed=seed, start=start)
    favorites = _synthetic_observations(
        n=n_per_bucket, price=0.90, true_win_rate=0.95, seed=seed + 1, start=start + dt.timedelta(days=1)
    )
    combined = longshots + favorites
    combined.sort(key=lambda o: o.close_time)
    return combined


def test_backtest_finds_positive_net_edge_on_biased_data():
    observations = _biased_market_dataset(n_per_bucket=300)
    result = run_favorite_longshot_backtest(observations, min_samples=20, min_net_edge=0.03)

    assert result.n_trades > 0
    assert result.net_pnl > 0
    assert result.hit_rate is not None


def test_backtest_fees_reduce_net_pnl_below_gross():
    observations = _biased_market_dataset(n_per_bucket=300)
    result = run_favorite_longshot_backtest(observations, min_samples=20, min_net_edge=0.03)

    assert result.n_trades > 0
    assert result.total_fees > 0
    assert result.net_pnl < result.gross_pnl
    assert result.net_pnl == result.gross_pnl - result.total_fees


def test_backtest_no_trades_on_efficient_market():
    # Price == true win rate everywhere -> no edge anywhere -> no trades.
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    observations = _synthetic_observations(n=500, price=0.50, true_win_rate=0.50, seed=1, start=start)

    result = run_favorite_longshot_backtest(observations, min_samples=20, min_net_edge=0.03)

    assert result.n_trades == 0
    assert result.net_pnl == 0.0
    assert result.hit_rate is None


def test_backtest_ignores_observations_without_close_time():
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    timed = _synthetic_observations(n=100, price=0.10, true_win_rate=0.03, seed=5, start=start)
    untimed = [
        Observation(ticker=f"no-time-{i}", event_ticker=f"no-time-{i}", price=0.10, resolved_yes=False, close_time=None)
        for i in range(50)
    ]

    result_with_untimed = run_favorite_longshot_backtest(timed + untimed, min_samples=20, min_net_edge=0.03)
    result_without = run_favorite_longshot_backtest(timed, min_samples=20, min_net_edge=0.03)

    assert result_with_untimed.n_trades == result_without.n_trades
    assert result_with_untimed.net_pnl == result_without.net_pnl


def test_backtest_skips_boundary_price_observations_without_crashing():
    # A settled market that never really traded can carry last_price 0.0 or
    # 1.0 — outside the fee model's valid (0,1) range. Hit this for real
    # against production data 2026-07-09; must be skipped, not crash.
    start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    normal = _biased_market_dataset(n_per_bucket=300)
    boundary = [
        Observation(ticker="never-traded-0", event_ticker="never-traded-0", price=0.0, resolved_yes=False, close_time=start + dt.timedelta(days=2)),
        Observation(ticker="never-traded-1", event_ticker="never-traded-1", price=1.0, resolved_yes=True, close_time=start + dt.timedelta(days=2)),
    ]

    result = run_favorite_longshot_backtest(normal + boundary, min_samples=20, min_net_edge=0.03)

    assert result.n_trades > 0  # didn't crash, and still traded the valid observations


def test_deduplicate_by_event_keeps_one_per_event_closest_to_50c():
    # Simulates several BTC-threshold markets for the same underlying event —
    # same event_ticker, different strike prices, all resolving off one real
    # price path. Only the price-closest-to-50c variant should survive.
    same_day = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    correlated = [
        Observation(ticker="KXBTCD-D1-T1", event_ticker="KXBTCD-D1", price=0.05, resolved_yes=False, close_time=same_day),
        Observation(ticker="KXBTCD-D1-T2", event_ticker="KXBTCD-D1", price=0.45, resolved_yes=False, close_time=same_day),
        Observation(ticker="KXBTCD-D1-T3", event_ticker="KXBTCD-D1", price=0.95, resolved_yes=True, close_time=same_day),
    ]
    unrelated = Observation(ticker="KXFED-D2-T1", event_ticker="KXFED-D2", price=0.60, resolved_yes=True, close_time=same_day)

    result = deduplicate_by_event(correlated + [unrelated])

    assert len(result) == 2
    kept_tickers = {o.ticker for o in result}
    assert kept_tickers == {"KXBTCD-D1-T2", "KXFED-D2-T1"}


def test_deduplicate_by_event_is_noop_when_events_are_distinct():
    obs = [
        Observation(ticker=f"T{i}", event_ticker=f"E{i}", price=0.5, resolved_yes=True, close_time=None)
        for i in range(10)
    ]
    assert len(deduplicate_by_event(obs)) == 10

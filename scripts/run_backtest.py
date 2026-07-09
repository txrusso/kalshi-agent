"""Runs the S2 favorite-longshot backtest against the real local data store
and prints a report. Usage: uv run python scripts/run_backtest.py
"""

from kalshi_agent.config import settings
from kalshi_agent.data.store import make_engine, make_session_factory
from kalshi_agent.strategy.backtest import load_observations_with_time, run_favorite_longshot_backtest


def main() -> None:
    engine = make_engine(settings)
    Session = make_session_factory(engine)

    with Session() as session:
        observations = load_observations_with_time(session)

    n_timed = sum(1 for o in observations if o.close_time is not None)
    print(f"loaded {len(observations)} resolved observations ({n_timed} with close_time)")

    if n_timed < 100:
        print("too few timed observations for a meaningful chronological train/test split — stopping.")
        return

    result = run_favorite_longshot_backtest(
        observations,
        train_fraction=0.6,
        bucket_width=settings.calibration_bucket_width,
        min_samples=settings.calibration_min_samples,
        min_net_edge=settings.min_net_edge_dollars,
    )

    print(f"trades:        {result.n_trades}")
    print(f"wins:          {result.n_wins}")
    print(f"hit rate:      {result.hit_rate:.1%}" if result.hit_rate is not None else "hit rate:      n/a")
    print(f"gross P&L:     ${result.gross_pnl:.2f}")
    print(f"total fees:    ${result.total_fees:.2f}")
    print(f"net P&L:       ${result.net_pnl:.2f}")
    if result.net_pnl_per_trade is not None:
        print(f"net P&L/trade: ${result.net_pnl_per_trade:.4f}")


if __name__ == "__main__":
    main()

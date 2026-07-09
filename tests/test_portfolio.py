import datetime as dt

from sqlalchemy import create_engine

from kalshi_agent.data.models import Fill
from kalshi_agent.data.store import init_db, make_session_factory
from kalshi_agent.ledger.portfolio import compute_portfolio_state, compute_realized_pnl_today


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pnl.db'}", future=True)
    init_db(engine)
    return make_session_factory(engine)


def _fill(ticker, side, action, price, count, fee, ts):
    return Fill(order_id=f"o-{ts.isoformat()}", ticker=ticker, side=side, action=action,
                price=price, count=count, fee=fee, is_taker=False, mode="PAPER", ts=ts, raw=None)


def test_buying_a_new_position_is_not_a_realized_loss(tmp_path):
    """Regression test for a real bug (2026-07-09): a single $99.84 buy
    showed daily_pnl=-99.91 and tripped the 5% daily-loss circuit breaker
    immediately, blocking every other trade that cycle. Opening a position
    is not a loss -- only its fee is a realized cost."""
    Session = _session_factory(tmp_path)
    now = dt.datetime.now(dt.timezone.utc)
    with Session() as session:
        session.add(_fill("KXFED-1", "no", "buy", 0.96, 104, 0.07, now))
        session.commit()

        pnl = compute_realized_pnl_today(session, mode="PAPER")

    assert pnl == -0.07  # only the fee, not the $99.84 notional


def test_selling_at_a_profit_is_realized_gain(tmp_path):
    Session = _session_factory(tmp_path)
    t0 = dt.datetime.now(dt.timezone.utc)
    t1 = t0 + dt.timedelta(minutes=1)
    with Session() as session:
        session.add(_fill("KXFED-1", "yes", "buy", 0.30, 100, 0.10, t0))
        session.add(_fill("KXFED-1", "yes", "sell", 0.50, 100, 0.10, t1))
        session.commit()

        pnl = compute_realized_pnl_today(session, mode="PAPER")

    # (0.50 - 0.30) * 100 - buy fee - sell fee
    assert pnl == (0.50 - 0.30) * 100 - 0.10 - 0.10


def test_selling_at_a_loss_is_realized_loss(tmp_path):
    Session = _session_factory(tmp_path)
    t0 = dt.datetime.now(dt.timezone.utc)
    t1 = t0 + dt.timedelta(minutes=1)
    with Session() as session:
        session.add(_fill("KXFED-1", "yes", "buy", 0.50, 100, 0.10, t0))
        session.add(_fill("KXFED-1", "yes", "sell", 0.30, 100, 0.10, t1))
        session.commit()

        pnl = compute_realized_pnl_today(session, mode="PAPER")

    assert pnl == (0.30 - 0.50) * 100 - 0.10 - 0.10


def test_entry_from_a_prior_day_still_values_todays_sell_correctly(tmp_path):
    Session = _session_factory(tmp_path)
    yesterday = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    today = dt.datetime.now(dt.timezone.utc)
    with Session() as session:
        session.add(_fill("KXFED-1", "yes", "buy", 0.40, 100, 0.10, yesterday))
        session.add(_fill("KXFED-1", "yes", "sell", 0.60, 100, 0.10, today))
        session.commit()

        pnl = compute_realized_pnl_today(session, mode="PAPER")

    # Yesterday's buy fee must NOT count today; only today's sell P&L does.
    assert pnl == (0.60 - 0.40) * 100 - 0.10


def test_portfolio_state_daily_pnl_uses_realized_not_cash_flow(tmp_path):
    Session = _session_factory(tmp_path)
    now = dt.datetime.now(dt.timezone.utc)
    with Session() as session:
        session.add(_fill("KXFED-1", "no", "buy", 0.96, 104, 0.07, now))
        session.commit()

        state = compute_portfolio_state(session, mode="PAPER", balance=900.0)

    # Old (buggy) behavior would have been -99.91 here.
    assert state.daily_pnl == -0.07

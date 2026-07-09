import datetime as dt
from dataclasses import dataclass

from sqlalchemy import create_engine

from kalshi_agent.data.models import Fill, LatestPrice, Market, Order
from kalshi_agent.data.store import init_db, make_session_factory
from kalshi_agent.strategy.exit import evaluate_alpha_realized_exit, find_positions_to_exit


@dataclass
class FakeSettings:
    alpha_realized_exit_fraction: float = 0.7


# -- evaluate_alpha_realized_exit (pure function) ---------------------------


def test_exits_once_threshold_fraction_captured():
    # entry 0.30, fair_value 0.60 -> edge 0.30. 70% captured = price 0.51.
    decision = evaluate_alpha_realized_exit(
        entry_price=0.30, fair_value_at_entry=0.60, current_price=0.52, alpha_realized_exit_fraction=0.7
    )
    assert decision.should_exit


def test_does_not_exit_below_threshold_fraction():
    decision = evaluate_alpha_realized_exit(
        entry_price=0.30, fair_value_at_entry=0.60, current_price=0.45, alpha_realized_exit_fraction=0.7
    )
    assert not decision.should_exit


def test_no_edge_at_entry_never_exits():
    decision = evaluate_alpha_realized_exit(
        entry_price=0.50, fair_value_at_entry=0.50, current_price=0.90, alpha_realized_exit_fraction=0.7
    )
    assert not decision.should_exit


# -- find_positions_to_exit (DB-backed) --------------------------------------


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'exit.db'}", future=True)
    init_db(engine)
    return make_session_factory(engine)


def _seed_long_position(Session, *, entry_price, fair_value_at_entry, current_price, time_horizon="long"):
    now = dt.datetime.now(dt.timezone.utc)
    with Session() as session:
        session.add(Market(ticker="KXFED-T1", event_ticker="KXFED-E1", series_ticker="KXFED",
                            title="t", status="open", raw={}))
        session.add(Order(client_order_id="o1", kalshi_order_id=None, ticker="KXFED-T1", side="yes",
                           action="buy", order_type="limit", price=entry_price, count=10, status="filled",
                           mode="PAPER", strategy=None, reason=None, fair_value_at_entry=fair_value_at_entry,
                           time_horizon=time_horizon, created_ts=now, updated_ts=now, raw=None))
        session.add(Fill(order_id="o1", ticker="KXFED-T1", side="yes", action="buy", price=entry_price,
                          count=10, fee=0.02, is_taker=False, mode="PAPER", ts=now, raw=None))
        session.add(LatestPrice(ticker="KXFED-T1", ts=now, yes_bid=current_price, yes_ask=current_price,
                                 last_price=current_price, volume=1, open_interest=1))
        session.commit()


def test_finds_long_term_position_that_realized_alpha(tmp_path):
    Session = _session_factory(tmp_path)
    _seed_long_position(Session, entry_price=0.30, fair_value_at_entry=0.60, current_price=0.55)

    with Session() as session:
        exits = find_positions_to_exit(session, FakeSettings())

    assert len(exits) == 1
    assert exits[0].ticker == "KXFED-T1"
    assert exits[0].action == "sell"
    assert exits[0].side == "yes"
    assert exits[0].count == 10
    assert exits[0].event_ticker == "KXFED-E1"


def test_ignores_short_term_positions(tmp_path):
    Session = _session_factory(tmp_path)
    _seed_long_position(Session, entry_price=0.30, fair_value_at_entry=0.60, current_price=0.55, time_horizon="short")

    with Session() as session:
        exits = find_positions_to_exit(session, FakeSettings())

    assert exits == []


def test_ignores_position_below_exit_threshold(tmp_path):
    Session = _session_factory(tmp_path)
    _seed_long_position(Session, entry_price=0.30, fair_value_at_entry=0.60, current_price=0.35)

    with Session() as session:
        exits = find_positions_to_exit(session, FakeSettings())

    assert exits == []


def test_no_positions_returns_empty(tmp_path):
    Session = _session_factory(tmp_path)
    with Session() as session:
        exits = find_positions_to_exit(session, FakeSettings())
    assert exits == []

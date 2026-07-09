from dataclasses import dataclass

from sqlalchemy import create_engine

from kalshi_agent.data.models import Market
from kalshi_agent.data.store import init_db, make_session_factory
from kalshi_agent.execution.paper import PaperExecutionAdapter
from kalshi_agent.ledger.portfolio import compute_cash_balance
from kalshi_agent.risk.guardrails import OrderRequest


@dataclass
class FakeSettings:
    trading_enabled: bool = True
    live_armed: bool = False
    per_market_max_fraction: float = 1.0
    aggregate_max_fraction: float = 1.0
    correlated_event_max_fraction: float = 1.0
    daily_loss_limit_fraction: float = 1.0
    max_contracts_per_order: int = 10_000
    paper_starting_balance: float = 1000.0


def _make_adapter(tmp_path, settings=None):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", future=True)
    init_db(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        session.add(Market(ticker="KXFED-25DEC-T4.50", event_ticker="KXFED-25DEC", series_ticker="KXFED", title="t", status="open", raw={}))
        session.commit()
    return PaperExecutionAdapter(Session, settings or FakeSettings()), Session


def _order(**overrides) -> OrderRequest:
    base = dict(
        ticker="KXFED-25DEC-T4.50",
        event_ticker="KXFED-25DEC",
        side="yes",
        action="buy",
        price=0.45,
        count=10,
        mode="PAPER",
    )
    base.update(overrides)
    return OrderRequest(**base)


async def test_accepted_order_fills_and_charges_fee(tmp_path):
    adapter, Session = _make_adapter(tmp_path)
    result = await adapter.submit_order(_order(order_type="limit"), client_order_id="order-1")

    assert result.accepted
    assert result.filled_count == 10
    assert result.fill_price == 0.45
    assert result.fee > 0  # maker fee, non-zero at 45c


async def test_cash_balance_decreases_after_buy(tmp_path):
    adapter, Session = _make_adapter(tmp_path)
    result = await adapter.submit_order(_order(price=0.45, count=10, order_type="limit"), client_order_id="order-1")

    with Session() as session:
        balance = compute_cash_balance(session, mode="PAPER", starting_balance=1000.0)

    expected = 1000.0 - (0.45 * 10) - result.fee
    assert balance == expected


async def test_rejected_when_trading_disabled(tmp_path):
    settings = FakeSettings(trading_enabled=False)
    adapter, Session = _make_adapter(tmp_path, settings)
    result = await adapter.submit_order(_order(), client_order_id="order-1")

    assert not result.accepted
    assert "trading_enabled" in result.reason


async def test_duplicate_client_order_id_is_idempotent(tmp_path):
    adapter, Session = _make_adapter(tmp_path)
    first = await adapter.submit_order(_order(), client_order_id="order-1")
    second = await adapter.submit_order(_order(), client_order_id="order-1")

    assert first.accepted and second.accepted
    assert first.filled_count == second.filled_count
    assert first.fee == second.fee

    with Session() as session:
        balance = compute_cash_balance(session, mode="PAPER", starting_balance=1000.0)
    # Only one fill should have been recorded, not two.
    expected = 1000.0 - (0.45 * 10) - first.fee
    assert balance == expected


async def test_rejects_live_mode_orders():
    adapter = PaperExecutionAdapter(session_factory=None, settings=FakeSettings())
    try:
        await adapter.submit_order(_order(mode="LIVE"), client_order_id="order-1")
        assert False, "expected ValueError"
    except ValueError:
        pass

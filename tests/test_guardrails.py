from dataclasses import dataclass

from kalshi_agent.risk.guardrails import OrderRequest, PortfolioState, check_order


@dataclass
class FakeSettings:
    trading_enabled: bool = True
    live_armed: bool = False
    per_market_max_fraction: float = 0.10
    aggregate_max_fraction: float = 0.50
    correlated_event_max_fraction: float = 0.20
    daily_loss_limit_fraction: float = 0.05
    max_contracts_per_order: int = 500


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


def test_rejects_when_master_switch_off():
    settings = FakeSettings(trading_enabled=False)
    result = check_order(_order(), PortfolioState(balance=100), settings)
    assert not result.allowed
    assert "trading_enabled" in result.reason


def test_rejects_live_without_arming():
    settings = FakeSettings(trading_enabled=True, live_armed=False)
    result = check_order(_order(mode="LIVE"), PortfolioState(balance=100), settings)
    assert not result.allowed
    assert "live_armed" in result.reason


def test_allows_live_when_armed():
    settings = FakeSettings(trading_enabled=True, live_armed=True)
    result = check_order(_order(mode="LIVE", price=0.45, count=1), PortfolioState(balance=100), settings)
    assert result.allowed


def test_rejects_price_out_of_range():
    settings = FakeSettings()
    result = check_order(_order(price=1.0), PortfolioState(balance=100), settings)
    assert not result.allowed
    assert "1-99c" in result.reason


def test_rejects_nonpositive_count():
    settings = FakeSettings()
    result = check_order(_order(count=0), PortfolioState(balance=100), settings)
    assert not result.allowed


def test_rejects_over_max_contracts_per_order():
    settings = FakeSettings(max_contracts_per_order=5)
    result = check_order(_order(count=6), PortfolioState(balance=1000), settings)
    assert not result.allowed
    assert "max_contracts_per_order" in result.reason


def test_rejects_over_per_market_cap():
    settings = FakeSettings(per_market_max_fraction=0.10)
    # notional = 0.45 * 10 = 4.50, cap = 10% of balance=10 -> 1.00
    result = check_order(_order(price=0.45, count=10), PortfolioState(balance=10), settings)
    assert not result.allowed
    assert "per-market" in result.reason


def test_rejects_over_aggregate_cap():
    settings = FakeSettings(per_market_max_fraction=1.0, aggregate_max_fraction=0.5)
    portfolio = PortfolioState(balance=10, exposure_by_ticker={"OTHER-TICKER": 4.0})
    # new notional = 0.45*10=4.5, existing=4.0, total=8.5 > cap(5.0)
    result = check_order(_order(price=0.45, count=10), portfolio, settings)
    assert not result.allowed
    assert "aggregate" in result.reason


def test_rejects_over_correlated_event_cap():
    settings = FakeSettings(per_market_max_fraction=1.0, aggregate_max_fraction=1.0, correlated_event_max_fraction=0.2)
    portfolio = PortfolioState(balance=10, exposure_by_event={"KXFED-25DEC": 1.5})
    # new notional = 0.45*10=4.5, existing=1.5, total=6.0 > cap(2.0)
    result = check_order(_order(price=0.45, count=10), portfolio, settings)
    assert not result.allowed
    assert "correlated-event" in result.reason


def test_rejects_when_daily_loss_limit_tripped():
    settings = FakeSettings(daily_loss_limit_fraction=0.05)
    portfolio = PortfolioState(balance=100, daily_pnl=-6.0)
    result = check_order(_order(price=0.45, count=1), portfolio, settings)
    assert not result.allowed
    assert "circuit breaker" in result.reason


def test_allows_reasonable_order_within_all_limits():
    settings = FakeSettings()
    portfolio = PortfolioState(balance=1000)
    result = check_order(_order(price=0.45, count=10), portfolio, settings)
    assert result.allowed
    assert result.reason is None

import datetime as dt
from dataclasses import dataclass

from kalshi_agent.strategy.decision import build_order_request
from kalshi_agent.strategy.signals import SignalResult


@dataclass
class FakeSettings:
    mode: str = "PAPER"
    min_net_edge_dollars: float = 0.04
    kelly_fraction: float = 0.125
    per_market_max_fraction: float = 0.10
    short_term_horizon_days: float = 7.0
    long_term_edge_multiplier: float = 2.0


def test_no_opinion_returns_none():
    signal = SignalResult(fair_value=None, confidence=0.0)
    result = build_order_request(ticker="T", event_ticker="E", price=0.50, signal=signal, bankroll=1000, settings=FakeSettings())
    assert result is None


def test_edge_below_threshold_returns_none():
    # fair_value very close to price -> edge below min_net_edge after fees
    signal = SignalResult(fair_value=0.501, confidence=1.0)
    result = build_order_request(ticker="T", event_ticker="E", price=0.50, signal=signal, bankroll=1000, settings=FakeSettings())
    assert result is None


def test_positive_yes_edge_buys_yes_at_market_price():
    signal = SignalResult(fair_value=0.65, confidence=1.0)
    result = build_order_request(ticker="T", event_ticker="E", price=0.50, signal=signal, bankroll=1000, settings=FakeSettings())

    assert result is not None
    assert result.side == "yes"
    assert result.action == "buy"
    assert result.price == 0.50
    assert result.count > 0
    assert result.order_type == "limit"
    assert result.mode == "PAPER"


def test_positive_no_edge_buys_no_at_complement_price():
    # low fair_value relative to price -> the contract is overpriced -> fade it (buy NO)
    signal = SignalResult(fair_value=0.10, confidence=1.0)
    result = build_order_request(ticker="T", event_ticker="E", price=0.30, signal=signal, bankroll=1000, settings=FakeSettings())

    assert result is not None
    assert result.side == "no"
    assert result.price == 0.70  # 1 - 0.30


def test_lower_confidence_sizes_smaller():
    high_conf = SignalResult(fair_value=0.65, confidence=1.0)
    low_conf = SignalResult(fair_value=0.65, confidence=0.1)

    settings = FakeSettings()
    high_result = build_order_request(ticker="T", event_ticker="E", price=0.50, signal=high_conf, bankroll=1000, settings=settings)
    low_result = build_order_request(ticker="T", event_ticker="E", price=0.50, signal=low_conf, bankroll=1000, settings=settings)

    assert high_result is not None
    assert low_result is not None
    assert low_result.count < high_result.count


def test_tiny_bankroll_can_size_to_zero_and_returns_none():
    signal = SignalResult(fair_value=0.65, confidence=1.0)
    result = build_order_request(ticker="T", event_ticker="E", price=0.50, signal=signal, bankroll=0.01, settings=FakeSettings())
    assert result is None


def test_accepted_order_records_entry_fields_for_exit_logic():
    signal = SignalResult(fair_value=0.65, confidence=1.0)
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    result = build_order_request(
        ticker="T", event_ticker="E", price=0.50, signal=signal, bankroll=1000,
        settings=FakeSettings(), expiration_time=now + dt.timedelta(days=2), now=now,
    )
    assert result is not None
    assert result.fair_value_at_entry == 0.65
    assert result.time_horizon == "short"


def test_no_expiration_time_defaults_to_long_horizon():
    signal = SignalResult(fair_value=0.65, confidence=1.0)
    result = build_order_request(ticker="T", event_ticker="E", price=0.50, signal=signal, bankroll=1000, settings=FakeSettings())
    assert result is not None
    assert result.time_horizon == "long"


def test_long_term_trade_needs_bigger_edge_to_clear():
    # gross edge 0.05 (0.55 - 0.50); clears the short-term bar (0.04) but not
    # the long-term one (0.04 * 2.0 = 0.08).
    signal = SignalResult(fair_value=0.55, confidence=1.0)
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

    short_result = build_order_request(
        ticker="T", event_ticker="E", price=0.50, signal=signal, bankroll=1000,
        settings=FakeSettings(), expiration_time=now + dt.timedelta(days=1), now=now,
    )
    long_result = build_order_request(
        ticker="T", event_ticker="E", price=0.50, signal=signal, bankroll=1000,
        settings=FakeSettings(), expiration_time=now + dt.timedelta(days=60), now=now,
    )

    assert short_result is not None
    assert long_result is None

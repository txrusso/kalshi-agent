import pytest

from kalshi_agent.risk.sizing import kelly_contracts, kelly_wager_fraction, net_edge_per_contract


def test_kelly_wager_fraction_no_edge_is_zero():
    assert kelly_wager_fraction(0.50, 0.50) == 0.0
    assert kelly_wager_fraction(0.40, 0.50) == 0.0  # negative edge


def test_kelly_wager_fraction_known_case():
    # p=0.60, price=0.50 -> edge=0.10, f* = 0.10 / 0.50 = 0.20
    assert kelly_wager_fraction(0.60, 0.50) == pytest.approx(0.20)


def test_kelly_wager_fraction_extreme_cheap_longshot():
    # p=0.10, price=0.05 -> edge=0.05, f* = 0.05 / 0.95
    assert kelly_wager_fraction(0.10, 0.05) == pytest.approx(0.05 / 0.95)


def test_kelly_contracts_applies_fraction_and_bankroll():
    # full-Kelly fraction 0.20, at 1/8-Kelly -> 0.025 of bankroll
    # bankroll=1000 -> $25 -> at price 0.50 -> 50 contracts
    n = kelly_contracts(0.60, 0.50, bankroll=1000, kelly_fraction=0.125)
    assert n == 50


def test_kelly_contracts_respects_max_fraction_cap():
    n_uncapped = kelly_contracts(0.90, 0.10, bankroll=1000, kelly_fraction=1.0)
    n_capped = kelly_contracts(0.90, 0.10, bankroll=1000, kelly_fraction=1.0, max_fraction_of_bankroll=0.05)
    assert n_capped < n_uncapped
    assert n_capped == 500  # 5% of 1000 = $50 / 0.10 price = 500 contracts


def test_kelly_contracts_zero_when_no_edge():
    assert kelly_contracts(0.50, 0.50, bankroll=1000, kelly_fraction=0.125) == 0


def test_net_edge_subtracts_taker_fee():
    edge = net_edge_per_contract(0.55, 0.50, is_taker=True)
    # gross edge 0.05, taker fee at 0.50 = 0.02 -> net 0.03
    assert edge == pytest.approx(0.03)


def test_net_edge_maker_cheaper_than_taker():
    maker_edge = net_edge_per_contract(0.55, 0.50, is_taker=False)
    taker_edge = net_edge_per_contract(0.55, 0.50, is_taker=True)
    assert maker_edge > taker_edge

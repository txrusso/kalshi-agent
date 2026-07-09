import datetime as dt

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Market(Base):
    __tablename__ = "markets"

    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    event_ticker: Mapped[str] = mapped_column(String, index=True)
    series_ticker: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(Text)
    # Raw resolution rule text — critical for cross-venue arbitrage filtering (build spec §3.2).
    resolution_rules: Mapped[str | None] = mapped_column(Text)
    open_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expiration_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String)
    result: Mapped[str | None] = mapped_column(String)
    raw: Mapped[dict] = mapped_column(JSON)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("markets.ticker"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Kalshi's current market schema reports these as dollar-string fields
    # (e.g. "0.4500" for 45c), not integer cents — stored here as floats in [0, 1].
    yes_bid: Mapped[float | None] = mapped_column(Float)
    yes_ask: Mapped[float | None] = mapped_column(Float)
    last_price: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    open_interest: Mapped[float | None] = mapped_column(Float)


class LatestPrice(Base):
    """One row per ticker, upserted in place — the bounded, continuously-
    refreshed "current price" feed for live trading decisions. Added
    2026-07-09: PriceSnapshot is append-only (one new row every sync cycle
    per market) and is meant as the static historical research corpus for
    calibration — it's already at ~472MB of the 500MB cap from a one-time
    backfill. Continuously re-snapshotting ~22k open markets every cycle
    for ongoing operation would blow straight through the cap within days,
    since sync_markets_and_snapshot's size-cap check only gates *new*
    Market rows, not repeated PriceSnapshot inserts for already-tracked
    ones. This table is upserted (session.merge), so its size is bounded by
    the number of currently-open tracked markets (~tens of thousands of
    rows, a few MB) no matter how many refresh cycles run."""

    __tablename__ = "latest_prices"

    ticker: Mapped[str] = mapped_column(ForeignKey("markets.ticker"), primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    yes_bid: Mapped[float | None] = mapped_column(Float)
    yes_ask: Mapped[float | None] = mapped_column(Float)
    last_price: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    open_interest: Mapped[float | None] = mapped_column(Float)


class OrderbookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(ForeignKey("markets.ticker"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    yes_levels: Mapped[list] = mapped_column(JSON)  # [[price_cents, size], ...]
    no_levels: Mapped[list] = mapped_column(JSON)


class Order(Base):
    __tablename__ = "orders"

    # Client-generated idempotency key (build spec §6 "Idempotency") — primary
    # key so retried submissions of the same logical order can't double-insert.
    client_order_id: Mapped[str] = mapped_column(String, primary_key=True)
    kalshi_order_id: Mapped[str | None] = mapped_column(String, index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    side: Mapped[str] = mapped_column(String)  # "yes" | "no"
    action: Mapped[str] = mapped_column(String)  # "buy" | "sell"
    order_type: Mapped[str] = mapped_column(String)  # "limit" | "market"
    price: Mapped[float | None] = mapped_column(Float)
    count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)  # "pending" | "resting" | "filled" | "cancelled" | "rejected"
    mode: Mapped[str] = mapped_column(String)  # "PAPER" | "LIVE"
    strategy: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(Text)  # signal/edge that triggered this order
    # Entry-order-only fields for the alpha-realization exit (user directive
    # 2026-07-09, see strategy/exit.py) — both already in the entry side's own
    # price terms, matching how OrderRequest stores them.
    fair_value_at_entry: Mapped[float | None] = mapped_column(Float)
    time_horizon: Mapped[str | None] = mapped_column(String)  # "short" | "long"
    created_ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSON)


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.client_order_id"), index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    side: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    price: Mapped[float] = mapped_column(Float)
    count: Mapped[int] = mapped_column(Integer)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    is_taker: Mapped[bool] = mapped_column()
    mode: Mapped[str] = mapped_column(String)  # "PAPER" | "LIVE"
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw: Mapped[dict | None] = mapped_column(JSON)


class AuditLogEntry(Base):
    """Every signal, decision, order, fill, and cancel — build spec §6 "Audit log"."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    ticker: Mapped[str | None] = mapped_column(String, index=True)
    details: Mapped[dict] = mapped_column(JSON)
